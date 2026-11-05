"""Vector layers (SVG in/out), resources and palettes, guides, animation."""
import re

from . import history
from .handlers import (FMT_ARGB32, BridgeError, _active_view, _num, _refresh, active_doc, app, find_layer,
                       handler, node_info, safe_path)

MAX_SVG = 2 * 1024 * 1024
RESOURCE_TYPES = ("preset", "pattern", "gradient", "palette", "brush", "workspace")


# ----------------------------------------------------------------- vectors
def _pt_per_px(doc):
    return 72.0 / (doc.xRes() or 72.0)


def _wrap_svg(doc, svg):
    """Accept a full <svg> document or bare elements; bare elements get a pixel-unit wrapper."""
    if re.search(r"<svg[\s>]", svg):
        return svg
    k = _pt_per_px(doc)
    return ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            'width="%gpt" height="%gpt" viewBox="0 0 %d %d">%s</svg>'
            % (doc.width() * k, doc.height() * k, doc.width(), doc.height(), svg))


def _shape_info(doc, s, with_svg=False):
    b, k = s.boundingBox(), 1.0 / _pt_per_px(doc)
    d = {"name": s.name(), "type": s.type(), "visible": s.visible(), "z": s.zIndex(),
         "bbox": [round(b.x() * k, 1), round(b.y() * k, 1), round(b.width() * k, 1), round(b.height() * k, 1)]}
    if with_svg:
        d["svg"] = s.toSvg()
    return d


def _vector_layer(doc, ref, create_name=None):
    if ref:
        n = find_layer(doc, ref)
        if n.type() != "vectorlayer":
            raise BridgeError("Layer %r is a %s, not a vectorlayer" % (n.name(), n.type()))
        return n
    n = doc.activeNode()
    if n is not None and n.type() == "vectorlayer":
        return n
    if create_name is None:
        raise BridgeError("give the vector layer to use")
    n = doc.createVectorLayer(create_name)
    doc.rootNode().addChildNode(n, None)
    return n


@handler("vector.add_svg", edit=True)
def vector_add_svg(p):
    """Add SVG elements (paths, shapes, text) to a vector layer as editable Krita shapes.
    Coordinates are canvas pixels unless a full <svg> with its own units is given."""
    doc = active_doc()
    svg = p.get("svg")
    if not isinstance(svg, str) or not svg.strip():
        raise BridgeError("'svg' must be SVG markup")
    if len(svg) > MAX_SVG:
        raise BridgeError("SVG larger than 2 MB")
    if re.search(r"<!ENTITY|<!DOCTYPE", svg, re.I):
        raise BridgeError("DOCTYPE/ENTITY declarations are not allowed")
    if re.search(r"(?:xlink:)?href\s*=\s*[\"']\s*(?!#|data:)", svg) or \
            re.search(r"url\(\s*[\"']?\s*(?!#)", svg) or "@import" in svg:
        raise BridgeError("external references are not allowed (href/url() must point to #ids; "
                          "embed images as data: URIs)")
    layer = _vector_layer(doc, p.get("layer"), str(p.get("name") or "Vector")[:120])
    wrapped = _wrap_svg(doc, svg)
    added = {"shapes": layer.addShapesFromSvg(wrapped)}
    if not added["shapes"]:
        raise BridgeError("Krita did not create any shapes from that SVG")

    def undo():
        for s in added["shapes"]:
            s.remove()

    def redo():
        added["shapes"] = layer.addShapesFromSvg(wrapped)
    history.backup_callback(undo, redo)
    doc.setActiveNode(layer)
    _refresh(doc)
    return {"layer": node_info(layer), "added": [_shape_info(doc, s) for s in added["shapes"]]}


@handler("vector.list")
def vector_list(p):
    doc = active_doc()
    layer = _vector_layer(doc, p.get("layer"))
    out = {"layer": node_info(layer), "shapes": [_shape_info(doc, s, bool(p.get("include_svg")))
                                                for s in layer.shapes()]}
    if p.get("layer_svg"):
        out["svg"] = layer.toSvg()
    return out


@handler("vector.remove", edit=True)
def vector_remove(p):
    doc = active_doc()
    layer = _vector_layer(doc, p.get("layer"))
    names = p.get("names")
    shapes = layer.shapes()
    if names == "all" or names == ["all"]:
        victims = shapes
    elif isinstance(names, list) and names:
        victims = [s for s in shapes if s.name() in names]
        missing = set(names) - {s.name() for s in victims}
        if missing:
            raise BridgeError("no shapes named %s" % ", ".join(sorted(missing)))
    else:
        raise BridgeError("'names' must be a list of shape names, or \"all\"")
    svg = _wrap_svg(doc, "".join(s.toSvg() for s in victims))
    for s in victims:
        s.remove()
    holder = {"shapes": []}

    def undo():
        holder["shapes"] = layer.addShapesFromSvg(svg)

    def redo():
        for s in holder["shapes"]:
            s.remove()
    history.backup_callback(undo, redo)
    _refresh(doc)
    return {"removed": len(victims)}


# --------------------------------------------------------------- resources
@handler("resources.list")
def resources_list(p):
    kind = p.get("type", "preset")
    if kind not in RESOURCE_TYPES:
        raise BridgeError("type must be one of %s" % ", ".join(RESOURCE_TYPES))
    names = sorted(app().resources(kind))
    q = str(p.get("filter") or "").lower()
    if q:
        names = [n for n in names if q in n.lower()]
    limit = _num(p, "limit", 200, 1, 5000, True)
    return {"type": kind, "count": len(names), "names": names[:limit]}


def _color_hex(mc):
    comps = mc.componentsOrdered()
    if mc.colorModel() in ("RGBA", "GRAYA") and comps:
        vals = comps[:3] if mc.colorModel() == "RGBA" else comps[:1] * 3
        return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v * 255)))) for v in vals)
    return None


@handler("palette.colors")
def palette_colors(p):
    from krita import Palette
    res = app().resources("palette").get(str(p.get("name")))
    if res is None:
        raise BridgeError("No palette named %r; see list_resources(type='palette')" % p.get("name"))
    pal = Palette(res)
    out = []
    for i in range(pal.colorsCountTotal()):
        sw = pal.colorSetEntryByIndex(i)
        if sw is None or not sw.isValid():
            continue
        mc = sw.color()
        out.append({"name": sw.name(), "hex": _color_hex(mc), "model": mc.colorModel()})
    return {"name": res.name(), "colors": out}


# ------------------------------------------------------------------ guides
@handler("document.guides")
def document_guides(p):
    doc = active_doc()
    if p.get("clear"):
        doc.setHorizontalGuides([])
        doc.setVerticalGuides([])
    for key, setter in (("horizontal", doc.setHorizontalGuides), ("vertical", doc.setVerticalGuides)):
        if key in p:
            vals = p[key]
            if not isinstance(vals, list) or not all(isinstance(v, (int, float)) for v in vals):
                raise BridgeError("%r must be a list of pixel positions" % key)
            setter([float(v) for v in vals])
    if "visible" in p:
        doc.setGuidesVisible(bool(p["visible"]))
    return {"horizontal": doc.horizontalGuides(), "vertical": doc.verticalGuides(),
            "visible": doc.guidesVisible()}


# --------------------------------------------------------------- animation
def _anim_info(doc):
    layers = [node_info(n) for n in _all_nodes(doc) if n.animated()]
    return {"fps": doc.framesPerSecond(), "current_frame": doc.currentTime(),
            "playback": [doc.playBackStartTime(), doc.playBackEndTime()],
            "clip": [doc.fullClipRangeStartTime(), doc.fullClipRangeEndTime()],
            "animated_layers": [{"id": l["id"], "name": l["name"]} for l in layers]}


def _all_nodes(doc):
    stack = list(doc.rootNode().childNodes())
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.childNodes())


@handler("animation.info")
def animation_info(p):
    return _anim_info(active_doc())


@handler("animation.set")
def animation_set(p):
    doc = active_doc()
    if "fps" in p:
        doc.setFramesPerSecond(_num(p, "fps", None, 1, 240, True))
    if "start" in p or "end" in p:
        start = _num(p, "start", doc.fullClipRangeStartTime(), 0, 100000, True)
        end = _num(p, "end", doc.fullClipRangeEndTime(), start, 100000, True)
        doc.setFullClipRangeStartTime(start)
        doc.setFullClipRangeEndTime(end)
        doc.setPlayBackRange(start, end)
    if "frame" in p:
        doc.setCurrentTime(_num(p, "frame", None, 0, 100000, True))
        _refresh(doc)
    return _anim_info(doc)


@handler("animation.keyframe", edit=True)
def animation_keyframe(p):
    """Create a keyframe on a layer; later drawing on that layer at that frame edits the keyframe."""
    doc = active_doc()
    n = find_layer(doc, p.get("layer"))
    if n.type() != "paintlayer":
        raise BridgeError("keyframes are supported on paint layers")
    frame = _num(p, "frame", None, 0, 100000, True)
    kind = p.get("kind", "blank")
    action = {"blank": "add_blank_frame", "duplicate": "add_duplicate_frame"}.get(kind)
    if action is None:
        raise BridgeError("kind must be blank or duplicate")
    _active_view(doc)
    if not n.animated():
        n.enableAnimation()
    doc.setActiveNode(n)
    doc.setCurrentTime(frame)
    _refresh(doc)
    act = app().action(action)
    if act is None:
        raise BridgeError("This Krita version has no %r action" % action)
    act.trigger()
    _refresh(doc)
    if not n.hasKeyframeAtTime(frame):
        raise BridgeError("Krita did not create the keyframe (is the timeline docker available?)")
    return {"layer": node_info(n), "frame": frame}


@handler("animation.export_frames")
def animation_export_frames(p):
    """Render frames to numbered PNGs in a workspace folder."""
    doc = active_doc()
    folder = safe_path(p.get("folder") or "frames")
    folder.mkdir(parents=True, exist_ok=True)
    start = _num(p, "start", doc.playBackStartTime(), 0, 100000, True)
    end = _num(p, "end", doc.playBackEndTime(), start, start + 2000, True)
    prefix = re.sub(r"[^A-Za-z0-9_.-]", "_", str(p.get("prefix") or "frame"))[:60]
    keep = doc.currentTime()
    files = []
    try:
        for t in range(start, end + 1):
            doc.setCurrentTime(t)
            _refresh(doc)
            path = folder / ("%s_%04d.png" % (prefix, t))
            if not doc.projection(0, 0, doc.width(), doc.height()).convertToFormat(FMT_ARGB32).save(str(path)):
                raise BridgeError("could not write %s" % path)
            files.append(str(path))
    finally:
        doc.setCurrentTime(keep)
        _refresh(doc)
    return {"folder": str(folder), "frames": len(files), "first": files[0], "last": files[-1]}
