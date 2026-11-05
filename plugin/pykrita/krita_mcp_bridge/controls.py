"""Controls: a curated set of Krita actions, the user's view, colours and brush, notifications."""
from krita import ManagedColor

from .extras import _color_hex
from .handlers import (BridgeError, _active_view, _find_preset, _num, _qcolor, _refresh, active_doc, app,
                       find_layer, handler, node_info)

# friendly name -> Krita action id. Only actions that act immediately (no dialog) are listed.
ACTIONS = {
    # selection
    "select_all": "select_all", "deselect": "deselect", "reselect": "reselect",
    "invert_selection": "invert_selection", "select_opaque": "selectopaque",
    "select_opaque_add": "selectopaque_add", "select_opaque_subtract": "selectopaque_subtract",
    "select_opaque_intersect": "selectopaque_intersect",
    "copy_selection_to_new_layer": "copy_selection_to_new_layer",
    "cut_selection_to_new_layer": "cut_selection_to_new_layer",
    "clear_selection_pixels": "clear",
    "fill_with_foreground": "fill_selection_foreground_color",
    "fill_with_background": "fill_selection_background_color", "fill_with_pattern": "fill_selection_pattern",
    "selection_to_vector_shape": "convert_selection_to_shape",
    # layers
    "convert_to_paint_layer": "convert_to_paint_layer", "convert_to_animated_layer": "convert_to_animated",
    "convert_group_to_animated": "convert_group_to_animated",
    "convert_to_transparency_mask": "convert_to_transparency_mask",
    "convert_to_selection_mask": "convert_to_selection_mask", "alpha_into_mask": "split_alpha_into_mask",
    "flatten_layer": "flatten_layer", "isolate_layer": "isolate_active_layer",
    "isolate_group": "isolate_active_group", "rotate_layer_90_cw": "rotateLayerCW90",
    "rotate_layer_90_ccw": "rotateLayerCCW90", "rotate_layer_180": "rotateLayer180",
    "mirror_all_layers_horizontally": "mirrorAllNodesX", "mirror_all_layers_vertically": "mirrorAllNodesY",
    # image
    "rotate_image_90_cw": "rotateImageCW90", "rotate_image_90_ccw": "rotateImageCCW90",
    "rotate_image_180": "rotateImage180", "trim_to_image": "trim_to_image",
    "trim_to_layer": "resizeimagetolayer", "trim_to_selection": "resizeimagetoselection",
    "duplicate_image_as_new_document": "create_copy", "create_snapshot": "create_snapshot",
    # animation
    "next_frame": "next_frame", "previous_frame": "previous_frame", "next_keyframe": "next_keyframe",
    "previous_keyframe": "previous_keyframe", "first_frame": "first_frame", "last_frame": "last_frame",
    "toggle_onion_skin": "toggle_onion_skin", "play_pause": "toggle_playback", "stop_playback": "stop_playback",
    # view (the user's window)
    "zoom_to_fit": "zoom_to_fit", "zoom_to_100": "zoom_to_100pct", "zoom_in": "view_zoom_in",
    "zoom_out": "view_zoom_out", "mirror_view": "mirror_canvas", "rotate_view_left": "rotate_canvas_left",
    "rotate_view_right": "rotate_canvas_right", "reset_view": "reset_display",
    "toggle_wrap_around": "wrap_around_mode", "toggle_grid": "view_grid", "toggle_pixel_grid": "view_pixel_grid",
    "toggle_guides": "view_show_guides", "toggle_rulers": "view_ruler",
    "toggle_selection_display": "toggle_display_selection", "toggle_canvas_only": "view_show_canvas_only",
}


@handler("krita.action", edit=True)
def krita_action(p):
    """Trigger one of Krita's own actions (curated: no dialogs) on the active document/layer."""
    name = p.get("name")
    if name not in ACTIONS:
        raise BridgeError("unknown action %r; available: %s" % (name, ", ".join(sorted(ACTIONS))))
    doc = active_doc()
    _active_view(doc)
    if p.get("layer"):
        doc.setActiveNode(find_layer(doc, p["layer"]))
    act = app().action(ACTIONS[name])
    if act is None:
        raise BridgeError("This Krita version has no %r action" % ACTIONS[name])
    if not act.isEnabled():
        raise BridgeError("%s is not available right now (e.g. needs a selection or another layer type)" % name)
    act.trigger()
    _refresh(doc)
    active = doc.activeNode()
    return {"action": name, "document": doc.name(), "active_layer": node_info(active) if active else None}


@handler("krita.action_list")
def krita_action_list(p):
    return {"actions": sorted(ACTIONS)}


# ------------------------------------------------------------------ the view
@handler("view.control")
def view_control(p):
    """Zoom / rotate / mirror / centre the user's canvas view (does not change the image)."""
    doc = active_doc()
    canvas = _active_view(doc).canvas()
    if canvas is None:
        raise BridgeError("no canvas")
    if p.get("fit"):
        app().action("zoom_to_fit").trigger()
    if p.get("zoom") is not None:
        canvas.setZoomLevel(_num(p, "zoom", None, 0.01, 64))
    if p.get("rotation") is not None:
        canvas.setRotation(_num(p, "rotation", None, -360, 360))
    if p.get("mirror") is not None:
        canvas.setMirror(bool(p["mirror"]))
    if p.get("center_x") is not None or p.get("center_y") is not None:
        from .handlers import QPointF
        c = canvas.preferredCenter()
        canvas.setPreferredCenter(QPointF(_num(p, "center_x", c.x()), _num(p, "center_y", c.y())))
    if p.get("reset"):
        canvas.resetZoom()
        canvas.resetRotation()
        canvas.setMirror(False)
    c = canvas.preferredCenter()
    return {"zoom": round(canvas.zoomLevel(), 4), "rotation": canvas.rotation(), "mirror": canvas.mirror(),
            "center": [round(c.x(), 1), round(c.y(), 1)]}


@handler("user.notify")
def user_notify(p):
    """Show a short floating message on the user's canvas (e.g. what the AI is doing)."""
    doc = active_doc()
    msg = str(p.get("message") or "")[:300]
    if not msg:
        raise BridgeError("message is empty")
    try:
        from PyQt6.QtGui import QIcon
    except ImportError:
        from PyQt5.QtGui import QIcon
    _active_view(doc).showFloatingMessage(msg, QIcon(), _num(p, "seconds", 4, 1, 30, True) * 1000, 1)
    return {"shown": msg}


# ----------------------------------------------------- the user's own tool state
@handler("user.tool_state")
def user_tool_state(p):
    """Read or set the user's foreground/background colour and brush (what they paint with next)."""
    doc = active_doc()
    view = _active_view(doc)
    if p.get("foreground"):
        view.setForeGroundColor(ManagedColor.fromQColor(_qcolor(p["foreground"]), view.canvas()))
    if p.get("background"):
        view.setBackGroundColor(ManagedColor.fromQColor(_qcolor(p["background"]), view.canvas()))
    if p.get("preset"):
        view.setCurrentBrushPreset(_find_preset(str(p["preset"])))
    if p.get("size") is not None:
        view.setBrushSize(_num(p, "size", None, 0.5, 1000))
    if p.get("opacity") is not None:
        view.setPaintingOpacity(_num(p, "opacity", None, 0, 1))
    if p.get("flow") is not None:
        view.setPaintingFlow(_num(p, "flow", None, 0, 1))
    if p.get("blend_mode"):
        view.setCurrentBlendingMode(str(p["blend_mode"]))
    if p.get("eraser") is not None:
        view.setEraserMode(bool(p["eraser"]))
    preset = view.currentBrushPreset()
    canvas = view.canvas()
    return {"foreground": _color_hex(view.foregroundColor(), canvas),
            "background": _color_hex(view.backgroundColor(), canvas),
            "preset": preset.name() if preset else None, "size": view.brushSize(),
            "opacity": round(view.paintingOpacity(), 3), "flow": round(view.paintingFlow(), 3),
            "blend_mode": view.currentBlendingMode(), "eraser": view.eraserMode()}


# ------------------------------------------------------------------ colourize
@handler("lineart.colorize", edit=True)
def lineart_colorize(p):
    """Flat-colour line art with Krita's Colourise Mask: give a colour + a scribble/rect per region."""
    from .handlers import FMT_ALPHA8, NO_BRUSH, QImage, QPainter, QPen, QPointF, QRectF, TRANSPARENT
    doc = active_doc()
    line = find_layer(doc, p.get("layer"))
    if line.type() != "paintlayer":
        raise BridgeError("colourise works on a paint layer holding line art")
    regions = p.get("regions")
    if not isinstance(regions, list) or not regions:
        raise BridgeError("'regions' must be a list of {color, points:[[x,y],...]} or {color, rect:[x,y,w,h]}")
    W, H = doc.width(), doc.height()
    canvas = _active_view(doc).canvas()
    mask = doc.createColorizeMask(str(p.get("name") or "Colourise"))
    line.addChildNode(mask, None)
    colors, strokes = [], []
    for r in regions:
        col = ManagedColor.fromQColor(_qcolor(r.get("color")), canvas)
        img = QImage(W, H, FMT_ALPHA8)
        img.fill(TRANSPARENT)
        pt = QPainter(img)
        pen = QPen(_qcolor("#000000"), float(r.get("width", 12)))
        pt.setPen(pen)
        pt.setBrush(NO_BRUSH)
        if r.get("rect"):
            x, y, w, h = (float(v) for v in r["rect"])
            pt.fillRect(QRectF(x, y, w, h), _qcolor("#000000"))
        else:
            pts = [QPointF(float(a), float(b)) for a, b in r.get("points") or []]
            if not pts:
                pt.end()
                raise BridgeError("each region needs 'points' or 'rect'")
            if len(pts) == 1:
                pts.append(pts[0])
            for a, b in zip(pts, pts[1:]):
                pt.drawLine(a, b)
        pt.end()
        bits = img.constBits().asstring(img.sizeInBytes())
        bpl = img.bytesPerLine()
        data = bits if bpl == W else b"".join(bits[i * bpl:i * bpl + W] for i in range(H))
        colors.append(col)
        strokes.append((col, data))
    transparent = -1
    if p.get("transparent_color") is not None:
        colors.append(ManagedColor.fromQColor(_qcolor(p["transparent_color"]), canvas))
        transparent = len(colors) - 1
    mask.initializeKeyStrokeColors(colors, transparent)
    for col, data in strokes:
        mask.setKeyStrokePixelData(data, col, 0, 0, W, H)
    mask.setUseEdgeDetection(bool(p.get("edge_detection", False)))
    mask.setCleanUpAmount(_num(p, "cleanup", 0.7, 0, 1))
    mask.updateMask(True)
    _refresh(doc)
    mask.setEditKeyStrokes(False)
    mask.setShowOutput(True)
    doc.setActiveNode(line)
    from . import history
    _refresh(doc)
    history.pump_for(0.4)  # let the layer panel catch up with the new mask before anything else happens
    return {"mask": node_info(mask), "layer": node_info(line), "regions": len(strokes),
            "note": "the fills update live; convert later with krita_action convert_to_paint_layer on the mask"}
