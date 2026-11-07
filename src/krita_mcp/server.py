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

from . import __version__
from .bridge_client import BridgeClient, KritaError
from .netfetch import fetch_image

INSTRUCTIONS = """\
Controls a running Krita instance. When the user asks for a picture from a prompt, start with create_artwork:
it decides whether to GENERATE it with an image model (realistic / photographic results) or set it up for
you to PAINT it with Krita's brushes, and you finish the work. Typical workflow:
1. krita_status (shows the workspace folder; all file paths must be inside it).
2. document_new or document_open, then work on separate layers: draw_shapes (crisp geometry),
   brush_stroke (Krita's real brushes), add_svg (editable vector shapes/text), import_image, filters.
3. LOOK at your work: canvas_snapshot (use grid=100 to read exact coordinates), and READ it:
   analyze_image (palette, light, contrast, layout), read_pixels (exact colours), find_color (where a
   colour is). Iterate until it looks right.
4. export_image / save_document to write files into the workspace.
undo/redo revert whole AI edits (pixels, properties, layers). Use batch to run many steps in one call.
Photos: adjust_image (levels, curves, hue/saturation, sharpen, denoise...), auto_enhance, add_vignette.
Video/animation: import_video -> edit frames (animation_set frame=..., or for_each_frame) -> render_video;
keyframe/list_keyframes, animation_contact_sheet to review motion. krita_action runs Krita's own commands.
Generative AI (ai_backends shows what is connected: ComfyUI, AUTOMATIC1111/Forge, OpenAI, Gemini,
Stability, Replicate, fal): generate_image (txt2img / img2img / inpaint the selection, count>1 = variations
+ comparison sheet -> pick_variation), outpaint, upscale_image, remove_background, match_colors (blend an
element into the scene), generate_video (Sora, Veo, HeyGen avatars, ...), ask_vision (second opinion from
Claude, GPT or Gemini). Results always land on new layers, cut to the selection, one undo step each.
Coordinates are pixels with the origin at the top-left. Colours are '#rrggbb' or '#rrggbbaa'.
Layers can be referenced by id or (if unique) name.
"""

mcp = _Server("krita", instructions=INSTRUCTIONS)
_client = BridgeClient()
TOOLS = {}  # name -> coroutine function, for `batch`


def tool(fn):
    TOOLS[fn.__name__] = fn
    return mcp.tool()(fn)


async def _call(method: str, /, **params) -> Any:
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
    """Export the flattened image to png/jpg/webp/bmp/tiff/psd/ora in the workspace (format from extension).
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
async def scale_image(width: int, height: int, method: str = "Bicubic") -> dict:
    """Scale the whole image (all layers) to a new size. method: Bicubic, Bilinear, Lanczos3, Box,
    Hermite, Mitchell, BSpline or NearestNeighbor (pixel art)."""
    return await _call("document.scale", width=width, height=height, method=method)


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
                          layer: Optional[str] = None, grid: Optional[int] = None,
                          frame: Optional[int] = None) -> Image:
    """Return the current composite image so you can SEE the canvas. Optionally zoom into a region
    (x, y, width, height in canvas pixels). max_size caps the longest side (64-4096).
    layer = show only that layer (transparent elsewhere).
    grid = draw labelled canvas-coordinate lines every `grid` pixels, to read off exact positions.
    frame = look at another animation frame (the current frame is restored)."""
    r = await _call("canvas.snapshot", max_size=max_size, x=x, y=y, width=width, height=height, layer=layer,
                    grid=grid, frame=frame)
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
                       path: Optional[str] = None, generator: Optional[str] = None,
                       generator_config: Optional[dict[str, Any]] = None) -> dict:
    """Create a layer or mask; it becomes the active layer. type:
      paintlayer (fill = optional solid colour), vectorlayer, grouplayer,
      filllayer (fill = colour, or pattern = pattern resource name) - a non-destructive colour/pattern layer,
      filterlayer (filter + filter_params) - non-destructive adjustment of everything below,
      clonelayer (source = layer to mirror live),
      filelayer (path = image in the workspace, kept as a live link),
      filllayer with generator = seexpr | screentone | gradient | multigrid and generator_config, e.g.
        seexpr {"script": "$c = noise(10*$u, 10*$v); [$c, $c*0.8, 1-$c]"} (procedural textures),
        screentone {"pattern": 0, "shape": 0, "frequency_x": 30, "rotation": 45,
                    "foreground_color": "#000000", "background_color": "#ffffff"} (manga tones),
        gradient {"shape": "radial", "start_position_x": 50, "start_position_y": 50,
                  "end_position_x": 100, "end_position_y": 50} (uses the user's fg/bg colours),
      filtermask (filter + filter_params) / transparencymask - attached to `parent` (default: active layer),
      limited to the current selection if there is one.
    parent = group layer id/name (or the layer a mask attaches to); above = sibling to stack over."""
    return await _call("layer.create", name=name, type=type, parent=parent, above=above, fill=fill,
                       filter=filter, filter_params=filter_params, source=source, pattern=pattern, path=path,
                       generator=generator, generator_config=generator_config)


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
async def keyframe(frame: int, kind: str = "blank", layer: Optional[str] = None) -> dict:
    """Keyframe operation on a paint layer at `frame`: blank (new empty drawing), duplicate (copy of the
    previous drawing), remove, remove_and_pull (shift later frames back), hold (insert a hold frame,
    shifting later ones), unhold. The layer becomes animated; draw on it while at that frame."""
    return await _call("animation.frame", frame=frame, kind=kind, layer=layer)


@tool
async def list_keyframes(layer: Optional[str] = None, start: Optional[int] = None,
                         end: Optional[int] = None) -> dict:
    """Frames at which a layer has keyframes."""
    return await _call("animation.keyframes", layer=layer, start=start, end=end)


@tool
async def import_frames(paths: list[str], first_frame: int = 0, step: int = 1) -> dict:
    """Load image files from the workspace (in order) as frames of a new animated layer."""
    return await _call("animation.import_frames", paths=paths, first_frame=first_frame, step=step)


@tool
async def animation_contact_sheet(start: Optional[int] = None, end: Optional[int] = None,
                                  step: Optional[int] = None, columns: Optional[int] = None,
                                  cell_size: int = 256) -> Image:
    """See many frames at once: a labelled grid of frames (max 48) to review motion and consistency."""
    r = await _call("animation.contact_sheet", start=start, end=end, step=step, columns=columns,
                    cell_size=cell_size)
    return Image(data=base64.b64decode(r["png_base64"]), format="png")


# -------------------------------------------------------------------- video
@tool
async def video_info(path: str) -> dict:
    """Size, fps, duration, frame count, codec and audio presence of a video in the workspace."""
    return await _call("video.info", path=path)


@tool
async def import_video(path: str, start: float = 0, duration: Optional[float] = None,
                       fps: Optional[float] = None, max_frames: int = 240, width: Optional[int] = None,
                       into_current: bool = False, name: Optional[str] = None) -> dict:
    """Decode a video (mp4/mov/webm/mkv/avi/gif...) from the workspace into an animated layer so every
    frame can be painted, filtered or adjusted. Opens a new document sized to the video unless
    into_current. start/duration in seconds; fps resamples; width scales (keeps aspect). Max 1000 frames."""
    return await _call("video.import", path=path, start=start, duration=duration, fps=fps,
                       max_frames=max_frames, width=width, into_current=into_current or None, name=name)


@tool
async def render_video(path: str, start: Optional[int] = None, end: Optional[int] = None,
                       fps: Optional[float] = None, width: Optional[int] = None, quality: Optional[int] = None,
                       bitrate_kbps: Optional[int] = None) -> dict:
    """Render the animation (default: playback range) to .mp4 (H.264), .webm (VP9), .gif, .mov (ProRes),
    .mkv or .apng in the workspace, using the ffmpeg bundled with Krita. width scales the output."""
    return await _call("video.render", path=path, start=start, end=end, fps=fps, width=width, quality=quality,
                       bitrate_kbps=bitrate_kbps)


# ------------------------------------------------------------- photo editing
@tool
async def adjust_image(operation: str, settings: Optional[dict[str, Any]] = None, layer: Optional[str] = None,
                       non_destructive: bool = False) -> dict:
    """Photo adjustment on a paint layer (limited to the selection if any). operation + settings:
    exact tone tools (pixel-precise, channel = rgb|r|g|b):
      levels {in_black, in_white, gamma, out_black, out_white} · curves {points: [[in,out],...] 0-255}
      brightness_contrast {brightness, contrast -100..100} · exposure {stops} · gamma {gamma}
      temperature {temperature -100..100 (warm+), tint -100..100 (magenta+)}
    Krita filters (non_destructive=true adds them as an editable filter mask instead):
      hue_saturation {hue -180..180, saturation, lightness -100..100, colorize}
      color_balance {shadows_|midtones_|highlights_ + cyan_red|magenta_green|yellow_blue: -100..100}
      sharpen {amount 0-10, radius, threshold} · blur {radius} · motion_blur {angle, length}
      lens_blur {radius} · denoise {strength} · denoise_gaussian {threshold, window}
      desaturate {method} · auto_contrast · normalize · invert · posterize {steps}
      threshold {level} · pixelize {size} · oil_paint {brush_size, smooth} · emboss {depth}
      edge_detect {radius} · high_pass {radius} · noise {amount, opacity, grayscale}
      color_to_alpha {color, threshold} · dodge / burn {exposure 0-1, range 0 shadows|1 mid|2 highlights}
      wave {amplitude, wavelength} · round_corners {radius}
    Any other Krita filter: apply_filter."""
    params = dict(settings or {})
    return await _call("photo.adjust", operation=operation, layer=layer,
                       non_destructive=non_destructive or None, **params)


@tool
async def auto_enhance(layer: Optional[str] = None, clip_percent: float = 0.5, per_channel: bool = True) -> dict:
    """One-click photo fix: stretch the tonal range (per channel also removes colour casts)."""
    return await _call("photo.auto_enhance", layer=layer, clip=clip_percent, per_channel=per_channel)


@tool
async def add_vignette(strength: float = 0.5, size: float = 0.65, color: str = "#000000",
                       blend_mode: str = "normal") -> dict:
    """Darken (or tint) the edges on a new layer on top; adjust its opacity or delete it later."""
    return await _call("photo.vignette", strength=strength, size=size, color=color, blend_mode=blend_mode)


@tool
async def convert_color_space(model: str = "RGBA", depth: str = "U8", profile: Optional[str] = None) -> dict:
    """Convert the image colour space, e.g. depth U16/F16/F32 for high-precision grading, back to RGBA/U8
    for pixel tools. model: RGBA, GRAYA, CMYKA, LABA, XYZA. Profile defaults to sRGB."""
    return await _call("document.color_space", model=model, depth=depth, profile=profile)


@tool
async def colorize_lineart(regions: list[dict[str, Any]], layer: Optional[str] = None,
                           transparent_color: Optional[str] = None, edge_detection: bool = False,
                           cleanup: float = 0.7, name: str = "Colourise") -> dict:
    """Flat-colour line art with Krita's Colourise Mask: regions = [{"color": "#e0b090",
    "points": [[x,y],...] (a scribble inside the area)} or {"color": ..., "rect": [x,y,w,h]}].
    Krita floods each scribble's colour up to the lines. transparent_color marks areas to leave empty."""
    return await _call("lineart.colorize", regions=regions, layer=layer, transparent_color=transparent_color,
                       edge_detection=edge_detection, cleanup=cleanup, name=name)


# ------------------------------------------------------------------ controls
@tool
async def krita_action(name: str, layer: Optional[str] = None) -> dict:
    """Run one of Krita's own commands on the active document/layer (curated, no dialogs):
    selection: select_all, deselect, reselect, invert_selection, select_opaque(_add|_subtract|_intersect),
      copy_selection_to_new_layer, cut_selection_to_new_layer, clear_selection_pixels,
      fill_with_foreground, fill_with_background, fill_with_pattern, selection_to_vector_shape
    layers: convert_to_paint_layer, convert_to_animated_layer, convert_group_to_animated,
      convert_to_transparency_mask, convert_to_selection_mask, alpha_into_mask, flatten_layer,
      isolate_layer, isolate_group, rotate_layer_90_cw|90_ccw|180, mirror_all_layers_horizontally|vertically
    image: rotate_image_90_cw|90_ccw|180, trim_to_image, trim_to_layer, trim_to_selection,
      duplicate_image_as_new_document, create_snapshot
    animation: next_frame, previous_frame, next_keyframe, previous_keyframe, first_frame, last_frame,
      toggle_onion_skin, play_pause, stop_playback
    view: zoom_to_fit, zoom_to_100, zoom_in, zoom_out, mirror_view, rotate_view_left|right, reset_view,
      toggle_wrap_around, toggle_grid, toggle_pixel_grid, toggle_guides, toggle_rulers,
      toggle_selection_display, toggle_canvas_only"""
    return await _call("krita.action", name=name, layer=layer)


@tool
async def view_control(zoom: Optional[float] = None, rotation: Optional[float] = None,
                       mirror: Optional[bool] = None, fit: bool = False, reset: bool = False,
                       center_x: Optional[float] = None, center_y: Optional[float] = None) -> dict:
    """Move the user's view of the canvas (not the image): zoom (1.0 = 100%), rotation in degrees,
    mirror, fit to window, reset, or centre on a canvas point - e.g. to show the user what you changed."""
    return await _call("view.control", zoom=zoom, rotation=rotation, mirror=mirror, fit=fit or None,
                       reset=reset or None, center_x=center_x, center_y=center_y)


@tool
async def notify_user(message: str, seconds: int = 4) -> dict:
    """Show a short floating message on the user's Krita canvas."""
    return await _call("user.notify", message=message, seconds=seconds)


@tool
async def user_tool_state(foreground: Optional[str] = None, background: Optional[str] = None,
                          preset: Optional[str] = None, size: Optional[float] = None,
                          opacity: Optional[float] = None, flow: Optional[float] = None,
                          blend_mode: Optional[str] = None, eraser: Optional[bool] = None) -> dict:
    """Read (no arguments) or set the colours and brush the USER paints with next - e.g. hand them the
    palette colour or brush you used. Returns the current state."""
    return await _call("user.tool_state", foreground=foreground, background=background, preset=preset, size=size,
                       opacity=opacity, flow=flow, blend_mode=blend_mode, eraser=eraser)


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


@tool
async def for_each_frame(operations: list[dict[str, Any]], start: Optional[int] = None,
                         end: Optional[int] = None, step: int = 1) -> dict:
    """Apply the same tool calls to every frame of an animation / imported video, e.g. colour-grade a clip:
    [{"tool": "adjust_image", "args": {"operation": "temperature", "settings": {"temperature": 30}}}].
    Frames default to the playback range (max 1000 frames, 20 operations)."""
    if not isinstance(operations, list) or not 1 <= len(operations) <= 20:
        raise ToolError("operations must be a list of 1..20 {tool, args} objects")
    for op in operations:
        if not isinstance(op, dict) or op.get("tool") not in TOOLS or op.get("tool") in ("batch", "for_each_frame"):
            raise ToolError("unknown or disallowed tool %r" % (op.get("tool") if isinstance(op, dict) else op))
    info = await _call("animation.info")
    first = info["playback"][0] if start is None else start
    last = info["playback"][1] if end is None else end
    frames = list(range(first, last + 1, max(1, step)))[:1000]
    done, errors = 0, []
    for f in frames:
        await _call("animation.set", frame=f)
        for op in operations:
            try:
                await TOOLS[op["tool"]](**(op.get("args") or {}))
            except (ToolError, TypeError) as e:
                errors.append({"frame": f, "tool": op["tool"], "error": str(e)})
                if len(errors) >= 5:
                    return {"frames_done": done, "stopped": True, "errors": errors}
        done += 1
    await _call("animation.set", frame=first)
    return {"frames_done": done, "frames": [frames[0], frames[-1]] if frames else [], "errors": errors}


from . import gen_tools  # noqa: E402  (importing registers the generative-AI tools and prompts)

FEATURE_MODULES = (gen_tools,)


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
    ap.add_argument("--transport", choices=["stdio", "http"], default="stdio",
                    help="stdio for local clients (default); http for clients that need a URL")
    ap.add_argument("--host", default="127.0.0.1",
                    help="HTTP bind address (default 127.0.0.1; others need KRITA_MCP_HTTP_TOKEN)")
    ap.add_argument("--port", type=int, default=8765, help="HTTP port (default 8765)")
    ap.add_argument("--agent", action="store_true",
                    help="run the in-Krita assistant service for the AI Chat docker instead of an MCP server")
    ap.add_argument("--agent-port", type=int, default=None,
                    help="assistant service port (default 5680 or KRITA_MCP_AGENT_PORT)")
    ap.add_argument("--check-update", action="store_true", help="check GitHub for a newer release (JSON)")
    ap.add_argument("--update", action="store_true",
                    help="download, verify and install the newest release of this server from GitHub")
    ap.add_argument("--version", action="version", version="krita-mcp " + __version__)
    args = ap.parse_args(argv)

    if args.check_update or args.update:
        from . import updater
        sys.exit(updater.main("check" if args.check_update else "update"))

    if args.agent:
        from . import agent
        print("krita-mcp assistant on 127.0.0.1:%d" % (args.agent_port or agent.DEFAULT_PORT), file=sys.stderr)
        agent.serve(args.agent_port)
        return

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
