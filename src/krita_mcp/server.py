"""MCP server exposing Krita. Works with any MCP client (Claude, ChatGPT, Gemini, Cursor, ...)."""
import argparse
import asyncio
import base64
import hmac
import os
import secrets
import sys
from typing import Any, Optional

try:  # MCP Python SDK v2
    from mcp.server.mcpserver import Image, MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # MCP Python SDK v1
    from mcp.server.fastmcp import FastMCP as _Server, Image
    from mcp.server.fastmcp.exceptions import ToolError

from .bridge_client import BridgeClient, KritaError
from .netfetch import fetch_image

INSTRUCTIONS = """\
Controls a running Krita instance. Typical workflow:
1. krita_status (shows the workspace folder; all file paths must be inside it).
2. document_new or document_open, then work on separate layers: draw_shapes (crisp geometry),
   brush_stroke (Krita's real brushes), add_svg (editable vector shapes/text), import_image, filters.
3. LOOK at your work: canvas_snapshot (use grid=100 to read exact coordinates), and READ it:
   analyze_image (palette, light, contrast, layout), read_pixels (exact colours), find_color (where a
   colour is). Iterate until it looks right.
4. export_image / save_document to write files into the workspace.
undo/redo revert whole AI edits (pixels, properties, layers). Use batch to run many steps in one call.
Inpainting with an external image model: set_selection -> export_image(x,y,width,height) +
export_selection_mask -> generate -> import_image(fit="selection").
Coordinates are pixels with the origin at the top-left. Colours are '#rrggbb' or '#rrggbbaa'.
Layers can be referenced by id or (if unique) name.
"""

mcp = _Server("krita", instructions=INSTRUCTIONS)
_client = BridgeClient()
TOOLS = {}  # name -> coroutine function, for `batch`


def tool(fn):
    TOOLS[fn.__name__] = fn
    return mcp.tool()(fn)


async def _call(method: str, **params) -> Any:
    clean = {k: v for k, v in params.items() if v is not None}
    try:
        return await asyncio.to_thread(_client.call, method, clean)
    except KritaError as e:  # ToolError => the model sees the real message, not a generic crash
        raise ToolError(str(e)) from None


# ------------------------------------------------------------------ status
@tool
async def krita_status() -> dict:
    """Krita version, the sandboxed workspace folder, and the open documents (index, size, active)."""
    return await _call("app.info")


# --------------------------------------------------------------- documents
@tool
async def document_new(width: int = 1024, height: int = 1024, name: str = "Untitled",
                       background: str = "white", resolution_ppi: float = 72) -> dict:
    """Create a new RGBA 8-bit sRGB document and make it active. background: colour or 'transparent'."""
    return await _call("document.new", width=width, height=height, name=name,
                       background=background, resolution_ppi=resolution_ppi)


@tool
async def document_open(path: str) -> dict:
    """Open a .kra/.ora/.psd or image file from the workspace and make it active."""
    return await _call("document.open", path=path)


@tool
async def document_activate(index: int) -> dict:
    """Switch the active document (index from krita_status)."""
    return await _call("document.activate", index=index)


@tool
async def document_info() -> dict:
    """Active document details including the full layer tree (listed bottom-to-top) and selection bounds."""
    return await _call("document.info")


@tool
async def save_document(path: Optional[str] = None) -> dict:
    """Save the active document as native .kra/.ora in the workspace (omit path to re-save in place)."""
    return await _call("document.save", path=path)


@tool
async def export_image(path: str, layer: Optional[str] = None, quality: Optional[int] = None,
                       full_canvas: bool = False, x: Optional[int] = None, y: Optional[int] = None,
                       width: Optional[int] = None, height: Optional[int] = None) -> dict:
    """Export the flattened image to png/jpg/webp/bmp/tiff in the workspace (format from extension).
    layer = export only that layer (cropped to its content unless full_canvas). quality = 0-100 for jpg/webp.
    x/y/width/height = export just that region (e.g. the area to send to an inpainting model)."""
    return await _call("document.export", path=path, layer=layer, quality=quality,
                       full_canvas=full_canvas or None, x=x, y=y, width=width, height=height)


@tool
async def document_close(save: bool = False) -> dict:
    """Close the active document. Unsaved changes are discarded unless save=true."""
    return await _call("document.close", save=save)


@tool
async def resize_canvas(width: int, height: int, x_offset: int = 0, y_offset: int = 0) -> dict:
    """Change the canvas size WITHOUT scaling content. Offsets shift existing content."""
    return await _call("document.resize_canvas", width=width, height=height, x=x_offset, y=y_offset)


@tool
async def scale_image(width: int, height: int) -> dict:
    """Scale the whole image (all layers) to a new size."""
    return await _call("document.scale", width=width, height=height)


@tool
async def crop_image(x: Optional[int] = None, y: Optional[int] = None, width: Optional[int] = None,
                     height: Optional[int] = None) -> dict:
    """Crop the canvas to a rectangle, or to the current selection's bounds when no rectangle is given."""
    return await _call("document.crop", x=x, y=y, width=width, height=height)


@tool
async def rotate_image(degrees: float) -> dict:
    """Rotate the whole image clockwise by any angle; the canvas grows to fit."""
    return await _call("document.rotate", degrees=degrees)


@tool
async def flip(target: str = "image", direction: str = "horizontal", layer: Optional[str] = None) -> dict:
    """Mirror the whole image (target=image) or one layer (target=layer). direction: horizontal or vertical."""
    return await _call("document.flip", target=target, direction=direction, layer=layer)


@tool
async def flatten_image() -> dict:
    """Merge all layers into one."""
    return await _call("document.flatten")


# ------------------------------------------------------------------ canvas
@tool
async def canvas_snapshot(max_size: int = 1024, x: Optional[int] = None, y: Optional[int] = None,
                          width: Optional[int] = None, height: Optional[int] = None,
                          layer: Optional[str] = None, grid: Optional[int] = None) -> Image:
    """Return the current composite image so you can SEE the canvas. Optionally zoom into a region
    (x, y, width, height in canvas pixels). max_size caps the longest side (64-4096).
    layer = show only that layer (transparent elsewhere).
    grid = draw labelled canvas-coordinate lines every `grid` pixels, to read off exact positions."""
    r = await _call("canvas.snapshot", max_size=max_size, x=x, y=y, width=width, height=height, layer=layer,
                    grid=grid)
    return Image(data=base64.b64decode(r["png_base64"]), format="png")


@tool
async def sample_color(x: int, y: int, layer: Optional[str] = None) -> dict:
    """Colour (rgba + '#rrggbbaa') of one pixel of the visible image, or of a single layer if given."""
    return await _call("canvas.sample", x=x, y=y, layer=layer)


# ------------------------------------------------------------------ layers
@tool
async def list_layers() -> dict:
    """Layer tree of the active document (children bottom-to-top) and the active layer id."""
    return await _call("layer.list")


@tool
async def create_layer(name: str = "Layer", type: str = "paintlayer", parent: Optional[str] = None,
                       above: Optional[str] = None, fill: Optional[str] = None,
                       filter: Optional[str] = None, filter_params: Optional[dict[str, Any]] = None,
                       source: Optional[str] = None, pattern: Optional[str] = None,
                       path: Optional[str] = None) -> dict:
    """Create a layer or mask; it becomes the active layer. type:
      paintlayer (fill = optional solid colour), vectorlayer, grouplayer,
      filllayer (fill = colour, or pattern = pattern resource name) - a non-destructive colour/pattern layer,
      filterlayer (filter + filter_params) - non-destructive adjustment of everything below,
      clonelayer (source = layer to mirror live),
      filelayer (path = image in the workspace, kept as a live link),
      filtermask (filter + filter_params) / transparencymask - attached to `parent` (default: active layer),
      limited to the current selection if there is one.
    parent = group layer id/name (or the layer a mask attaches to); above = sibling to stack over."""
    return await _call("layer.create", name=name, type=type, parent=parent, above=above, fill=fill,
                       filter=filter, filter_params=filter_params, source=source, pattern=pattern, path=path)


@tool
async def update_layer(layer: Optional[str] = None, name: Optional[str] = None, visible: Optional[bool] = None,
                       opacity: Optional[float] = None, blend_mode: Optional[str] = None,
                       locked: Optional[bool] = None, alpha_locked: Optional[bool] = None,
                       clipped: Optional[bool] = None, color_label: Optional[int] = None,
                       x: Optional[int] = None, y: Optional[int] = None,
                       dx: Optional[int] = None, dy: Optional[int] = None, activate: bool = False) -> dict:
    """Change layer properties. layer = id or name (default: active layer). opacity is 0..1.
    blend_mode uses Krita ids such as normal, multiply, screen, overlay, add, color, hue.
    alpha_locked = only paint where the layer already has pixels; clipped = clip to the layers below in the
    group (Krita "inherit alpha"); color_label 0-8.
    x/y place the top-left of the layer's content (its "bounds"); dx/dy move it relative to where it is."""
    return await _call("layer.update", layer=layer, name=name, visible=visible, opacity=opacity,
                       blend_mode=blend_mode, locked=locked, alpha_locked=alpha_locked, clipped=clipped,
                       color_label=color_label, x=x, y=y, dx=dx, dy=dy, activate=activate or None)


@tool
async def arrange_layer(layer: Optional[str] = None, to: Optional[str] = None, above: Optional[str] = None,
                        parent: Optional[str] = None) -> dict:
    """Restack a layer. to = top, bottom, up or down; or above = a sibling to sit directly over;
    parent = a group to move the layer into (top of it unless `to`/`above` say otherwise)."""
    return await _call("layer.arrange", layer=layer, to=to, above=above, parent=parent)


@tool
async def merge_down(layer: Optional[str] = None) -> dict:
    """Merge a layer (default: active) into the layer below it. Returns the merged layer."""
    return await _call("layer.merge_down", layer=layer)


@tool
async def delete_layer(layer: Optional[str] = None) -> dict:
    """Delete a layer (default: active layer)."""
    return await _call("layer.delete", layer=layer)


@tool
async def duplicate_layer(layer: Optional[str] = None, name: Optional[str] = None) -> dict:
    """Duplicate a layer directly above the original."""
    return await _call("layer.duplicate", layer=layer, name=name)


@tool
async def transform_layer(layer: Optional[str] = None, scale_x: Optional[float] = None,
                          scale_y: Optional[float] = None, rotate_degrees: Optional[float] = None,
                          shear_x_degrees: Optional[float] = None, shear_y_degrees: Optional[float] = None) -> dict:
    """Scale (about the layer centre), rotate (degrees, clockwise) and/or shear a layer."""
    return await _call("layer.transform", layer=layer, scale_x=scale_x, scale_y=scale_y,
                       rotate_degrees=rotate_degrees, shear_x_degrees=shear_x_degrees,
                       shear_y_degrees=shear_y_degrees)


# ------------------------------------------------- bringing in AI-made art
@tool
async def import_image(path: Optional[str] = None, image_base64: Optional[str] = None,
                       name: str = "Imported image", fit: Optional[str] = None,
                       x: Optional[int] = None, y: Optional[int] = None,
                       width: Optional[int] = None, height: Optional[int] = None,
                       parent: Optional[str] = None) -> dict:
    """Put a raster image on a NEW paint layer. Provide `path` (inside the workspace) OR `image_base64`
    (PNG/JPEG/WebP bytes). fit: contain (default), cover, stretch, none (natural size at x,y), or
    selection (stretched over the selection and masked by it, soft edges included - for inpainting).
    This is how images from an image-generation model get into Krita for further editing."""
    return await _call("layer.import_image", path=path, data_base64=image_base64, name=name, fit=fit,
                       x=x, y=y, width=width, height=height, parent=parent)


@tool
async def import_image_from_url(url: str, name: str = "Imported image", fit: Optional[str] = None,
                                x: Optional[int] = None, y: Optional[int] = None,
                                width: Optional[int] = None, height: Optional[int] = None) -> dict:
    """Download a PUBLIC http(s) image URL (no redirects, max 25 MB, private addresses blocked) and
    place it on a new layer. Same placement options as import_image."""
    try:
        data = await asyncio.to_thread(fetch_image, url)
    except ValueError as e:
        raise ToolError("Could not fetch image: %s" % e) from None
    return await _call("layer.import_image", data_base64=base64.b64encode(data).decode(), name=name,
                       fit=fit, x=x, y=y, width=width, height=height)


# ----------------------------------------------------------------- drawing
@tool
async def draw_shapes(shapes: list[dict[str, Any]], layer: Optional[str] = None) -> dict:
    """Draw vector-style primitives (anti-aliased) onto a paint layer, in order (max 5000 per call).
    Every shape has "type" and optional "opacity" (0..1). Types:
      line      {x1,y1,x2,y2,color,stroke_width}
      polyline  {points:[[x,y],...],color,stroke_width}
      bezier    {points:[start, c1,c2,end, c1,c2,end ...],color,stroke_width}   (1+3n points)
      rect      {x,y,width,height,radius?,fill?,stroke?,stroke_width?,gradient?}
      ellipse   {cx,cy,rx,ry,fill?,stroke?,stroke_width?,gradient?}
      polygon   {points:[[x,y],...],fill?,stroke?,stroke_width?,gradient?}
      text      {x,y,text,size(px),font?,bold?,color}   (x,y = baseline-left)
    For closed shapes `color` is the fill when neither fill nor stroke is given.
    gradient: {"type":"linear","x1","y1","x2","y2","stops":[[0,"#fff"],[1,"#000"]]} or
              {"type":"radial","cx","cy","r","stops":[...]}.
    layer = id or name of a paint layer (default: active layer).
    These are crisp geometric shapes; for painterly marks use brush_stroke."""
    return await _call("draw.shapes", shapes=shapes, layer=layer)


@tool
async def brush_stroke(points: list[list[float]], preset: Optional[str] = None, size: Optional[float] = None,
                       color: Optional[str] = None, opacity: Optional[float] = None,
                       flow: Optional[float] = None, eraser: bool = False, smooth: bool = True,
                       closed: bool = False, fill: bool = False, outline: bool = True,
                       layer: Optional[str] = None) -> dict:
    """Paint ONE stroke with Krita's real brush engine (texture, wet mixing, ... from the preset).
    points: [[x, y], ...] or [[x, y, pressure 0..1], ...] (pressure needs a pressure-sensitive preset).
    preset: brush preset name or unique part of it (see list_brush_presets; default: the current brush).
    size in px, opacity/flow 0..1, eraser=true erases with the brush. smooth=true curves through the points.
    closed/fill: close the path / fill it with the colour (outline=false for fill only).
    Painting respects the current selection. The user's own brush settings are restored afterwards."""
    return await _call("brush.stroke", points=points, preset=preset, size=size, color=color, opacity=opacity,
                       flow=flow, eraser=eraser or None, smooth=smooth, closed=closed or None,
                       fill=fill or None, outline=outline, layer=layer)


@tool
async def list_brush_presets(filter: Optional[str] = None, limit: int = 200) -> dict:
    """Names of Krita's brush presets (optionally containing `filter`, e.g. 'ink', 'pencil', 'airbrush')."""
    return await _call("brush.presets", filter=filter, limit=limit)


# --------------------------------------------------------------- selection
@tool
async def set_selection(kind: str = "rect", x: Optional[float] = None, y: Optional[float] = None,
                        width: Optional[float] = None, height: Optional[float] = None,
                        points: Optional[list[list[float]]] = None, layer: Optional[str] = None,
                        color: Optional[str] = None, tolerance: Optional[int] = None,
                        contiguous: bool = False, mode: str = "replace") -> dict:
    """Select: kind=rect or ellipse (x, y, width, height), polygon (points [[x,y],...]),
    layer (the opaque pixels of `layer`), color (every pixel within `tolerance` 0-255 of `color`, or of the
    colour at x,y; contiguous=true = magic wand from x,y), all, or none.
    mode: replace, add, subtract, intersect. Later draw/brush/filter/fill operations are limited to it."""
    return await _call("selection.set", kind=kind, x=x, y=y, width=width, height=height, points=points,
                       layer=layer, color=color, tolerance=tolerance, contiguous=contiguous or None, mode=mode)


@tool
async def modify_selection(operation: str, amount: int = 1) -> dict:
    """operation: invert, grow, shrink, feather, border or smooth. amount in pixels."""
    return await _call("selection.modify", operation=operation, amount=amount)


@tool
async def fill_selection(color: Optional[str] = None, layer: Optional[str] = None) -> dict:
    """Fill the current selection on a paint layer with a colour; omit colour to erase the selected pixels."""
    return await _call("selection.fill", color=color, layer=layer)


# ----------------------------------------------------------------- filters
@tool
async def list_filters() -> dict:
    """Names of all Krita filters usable with apply_filter."""
    return await _call("filter.list")


@tool
async def describe_filter(name: str) -> dict:
    """Default parameters of a filter, so you know which `params` apply_filter accepts."""
    return await _call("filter.describe", name=name)


@tool
async def apply_filter(name: str, params: Optional[dict[str, Any]] = None, layer: Optional[str] = None,
                       ignore_selection: bool = False) -> dict:
    """Apply a Krita filter (blur, levels, invert, ...) to a paint layer, restricted to the selection if any.
    params: filter properties from describe_filter (string/number/bool values only)."""
    return await _call("filter.apply", name=name, params=params, layer=layer,
                       ignore_selection=ignore_selection or None)


# ----------------------------------------------------------------- history
@tool
async def undo(steps: int = 1, native: bool = False) -> dict:
    """Undo your last edit(s) (each tool call is one step: pixels, layer changes, selection...).
    native=true steps through Krita's own undo stack instead (also covers edits made by hand)."""
    return await _call("history.undo", steps=steps, native=native or None)


@tool
async def redo(steps: int = 1, native: bool = False) -> dict:
    """Redo edit(s) undone with undo."""
    return await _call("history.redo", steps=steps, native=native or None)


@tool
async def history(limit: int = 30) -> dict:
    """Your undoable edits in the active document plus Krita's own undo stack (done=false = undone)."""
    return await _call("history.list", limit=limit)


# ----------------------------------------------------------- reading images
@tool
async def analyze_image(x: Optional[int] = None, y: Optional[int] = None, width: Optional[int] = None,
                        height: Optional[int] = None, layer: Optional[str] = None, palette_size: int = 8,
                        grid: int = 3) -> dict:
    """Numbers about the image (or a region / one layer): dominant palette with shares, mean colour,
    brightness, contrast, sharpness (0..1), coverage of non-transparent pixels, content bounds, and a
    grid x grid layout of mean colours (top row first). Use it to judge and match colours and lighting."""
    return await _call("image.analyze", x=x, y=y, width=width, height=height, layer=layer,
                       palette_size=palette_size, grid=grid)


@tool
async def read_pixels(x: int, y: int, width: int = 8, height: int = 8, layer: Optional[str] = None) -> dict:
    """Exact colours of a small block (max 1024 pixels), rows top to bottom."""
    return await _call("image.read_pixels", x=x, y=y, width=width, height=height, layer=layer)


@tool
async def find_color(color: str, tolerance: int = 24, layer: Optional[str] = None,
                     x: Optional[int] = None, y: Optional[int] = None, width: Optional[int] = None,
                     height: Optional[int] = None, limit: int = 20) -> dict:
    """Where does a colour occur? Returns connected regions (bbox, share, centroid), largest first,
    plus total coverage. Good for locating objects before editing them."""
    return await _call("image.find_color", color=color, tolerance=tolerance, layer=layer, x=x, y=y,
                       width=width, height=height, limit=limit)


@tool
async def view_image(path: str, max_size: int = 1024) -> Image:
    """Look at an image file in the workspace (reference, generated output, export) without importing it."""
    r = await _call("file.view", path=path, max_size=max_size)
    return Image(data=base64.b64decode(r["png_base64"]), format="png")


@tool
async def list_workspace(folder: Optional[str] = None, recursive: bool = True, limit: int = 200) -> dict:
    """Files in the workspace folder (path relative to it, size, modified time)."""
    return await _call("file.list", folder=folder, recursive=recursive, limit=limit)


@tool
async def export_selection_mask(path: str, invert: bool = False) -> dict:
    """Save the current selection as a greyscale PNG mask (white = selected) for inpainting models."""
    return await _call("selection.export_mask", path=path, invert=invert or None)


# ------------------------------------------------------------------ vectors
@tool
async def add_svg(svg: str, layer: Optional[str] = None, name: str = "Vector") -> dict:
    """Add SVG elements as EDITABLE vector shapes: <path>, <rect>, <circle>, <ellipse>, <polygon>, <line>,
    <text>, gradients in <defs>, groups. Bare elements use canvas pixel coordinates. Goes onto `layer`
    (a vectorlayer), the active vector layer, or a new vector layer called `name`.
    No external references (href/url() only to #ids, images as data: URIs)."""
    return await _call("vector.add_svg", svg=svg, layer=layer, name=name)


@tool
async def list_vector_shapes(layer: Optional[str] = None, include_svg: bool = False) -> dict:
    """Shapes on a vector layer: name, type, bbox in pixels (optionally each shape's SVG)."""
    return await _call("vector.list", layer=layer, include_svg=include_svg or None)


@tool
async def remove_vector_shapes(names: list[str], layer: Optional[str] = None) -> dict:
    """Delete shapes by name from a vector layer (["all"] removes every shape)."""
    return await _call("vector.remove", names=names, layer=layer)


# ---------------------------------------------------------------- resources
@tool
async def list_resources(type: str = "preset", filter: Optional[str] = None, limit: int = 200) -> dict:
    """Names of Krita resources: type = preset (brushes), pattern, gradient, palette, brush, workspace."""
    return await _call("resources.list", type=type, filter=filter, limit=limit)


@tool
async def palette_colors(name: str) -> dict:
    """The colours (name + hex) of a Krita palette resource, for a coherent colour scheme."""
    return await _call("palette.colors", name=name)


@tool
async def set_guides(horizontal: Optional[list[float]] = None, vertical: Optional[list[float]] = None,
                     visible: Optional[bool] = None, clear: bool = False) -> dict:
    """Set the document's guide lines (pixel positions), e.g. thirds or a layout grid the user can see."""
    return await _call("document.guides", horizontal=horizontal, vertical=vertical, visible=visible,
                       clear=clear or None)


# ---------------------------------------------------------------- animation
@tool
async def animation_info() -> dict:
    """Frame rate, playback/clip range, current frame and animated layers."""
    return await _call("animation.info")


@tool
async def animation_set(fps: Optional[int] = None, start: Optional[int] = None, end: Optional[int] = None,
                        frame: Optional[int] = None) -> dict:
    """Set frame rate, frame range (start..end) and/or go to `frame` (drawing then edits that frame)."""
    return await _call("animation.set", fps=fps, start=start, end=end, frame=frame)


@tool
async def add_keyframe(frame: int, layer: Optional[str] = None, kind: str = "blank") -> dict:
    """Create a keyframe on a paint layer at `frame` (kind blank or duplicate of the previous one).
    The layer becomes animated; draw on it while at that frame to fill the keyframe."""
    return await _call("animation.keyframe", frame=frame, layer=layer, kind=kind)


@tool
async def export_animation_frames(folder: str = "frames", start: Optional[int] = None, end: Optional[int] = None,
                                  prefix: str = "frame") -> dict:
    """Render frames as numbered PNGs into a workspace folder."""
    return await _call("animation.export_frames", folder=folder, start=start, end=end, prefix=prefix)


# -------------------------------------------------------------------- batch
@tool
async def batch(operations: list[dict[str, Any]], stop_on_error: bool = True) -> list:
    """Run several tool calls in one go, in order: [{"tool": "draw_shapes", "args": {...}}, ...] (max 200).
    Any tool except batch itself. Returns one result per operation; images (canvas_snapshot,
    view_image) are returned after the summary. Much faster than one call per step."""
    if not isinstance(operations, list) or not 1 <= len(operations) <= 200:
        raise ToolError("operations must be a list of 1..200 {tool, args} objects")
    results, images = [], []
    for i, op in enumerate(operations):
        name = op.get("tool") if isinstance(op, dict) else None
        fn = TOOLS.get(name)
        if fn is None or name == "batch":
            err = "unknown tool %r" % name
        else:
            try:
                out = await fn(**(op.get("args") or {}))
                if isinstance(out, Image):
                    images.append(out)
                    out = {"image": len(images)}
                results.append({"index": i, "tool": name, "ok": True, "result": out})
                continue
            except ToolError as e:
                err = str(e)
            except TypeError as e:  # bad/missing arguments
                err = "bad arguments: %s" % e
        results.append({"index": i, "tool": name, "ok": False, "error": err})
        if stop_on_error:
            break
    return [{"completed": sum(r["ok"] for r in results), "of": len(operations), "results": results}] + images


# ------------------------------------------------------------------- main
class _BearerAuth:
    """Pure-ASGI shared-secret check for the HTTP transport."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            hdrs = dict(scope.get("headers") or [])
            given = hdrs.get(b"authorization", b"")
            ok = given.lower().startswith(b"bearer ") and hmac.compare_digest(given[7:].strip(), self.token)
            if not ok:
                if scope["type"] == "http":
                    await send({"type": "http.response.start", "status": 401,
                                "headers": [(b"content-type", b"text/plain"), (b"www-authenticate", b"Bearer")]})
                    await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="krita-mcp", description=__doc__)
    ap.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1", help="HTTP bind address (default loopback)")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)

    if args.transport == "stdio":
        mcp.run("stdio")
        return

    import uvicorn

    token = os.environ.get("KRITA_MCP_HTTP_TOKEN") or secrets.token_urlsafe(32)
    if args.host not in ("127.0.0.1", "::1", "localhost") and not os.environ.get("KRITA_MCP_HTTP_TOKEN"):
        sys.exit("Refusing to bind a non-loopback address without KRITA_MCP_HTTP_TOKEN set.")
    print("krita-mcp HTTP endpoint: http://%s:%d/mcp\nAuthorization: Bearer %s" % (args.host, args.port, token),
          file=sys.stderr)
    uvicorn.run(_BearerAuth(mcp.streamable_http_app(), token), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
