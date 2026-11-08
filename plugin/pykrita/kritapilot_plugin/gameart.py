"""Game art: export layers with a manifest, slice sprite sheets, palettes (reduce, swap), outlines, and
variable-width ink strokes. The pixel maths lives in pixels.py (Qt-free, tested on its own)."""
import json
import math
import re

from . import history, pixels
from .handlers import (AA, CM_IN, FMT_ALPHA8, FMT_ARGB32, NO_PEN, BridgeError, QBrush, QColor, QImage, QPainter,
                       QPainterPath, QPointF, QPolygonF, Qt, _enum, _num, _refresh, _rgba_image, _uid, active_doc,
                       find_layer, handler, node_info, paint_layer, read_image, write_image)
from .util import parse_color, safe_path

WINDING = _enum(Qt, "FillRule", "WindingFill")
MASKS = {"transparencymask", "filtermask", "transformmask", "selectionmask", "colorizemask"}
MAX_EXPORTS = 2000
MAX_TILES = 4096
MAX_PALETTE_PIXELS = 4_000_000  # pure-Python per pixel: keep Krita responsive
MAX_INK_POINTS = 20000


def _slug(text):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("._")[:80] or "layer"


def _unique(folder, stem, used):
    name, k = stem, 2
    while name.lower() in used:
        name, k = "%s_%d" % (stem, k), k + 1
    used.add(name.lower())
    return folder / (name + ".png")


def _save(img, path):
    if not img.convertToFormat(FMT_ARGB32).save(str(path)):
        raise BridgeError("could not write %s" % path)


def _clip(doc, rect):
    """A layer's content box cut to the canvas: (x, y, w, h), or None when nothing is on the canvas."""
    x0, y0 = max(0, rect.x()), max(0, rect.y())
    x1, y1 = min(doc.width(), rect.x() + rect.width()), min(doc.height(), rect.y() + rect.height())
    return (x0, y0, x1 - x0, y1 - y0) if x1 > x0 and y1 > y0 else None


def _trim(raw, w, h):
    """The box (x, y, w, h) around the pixels that are not fully transparent, inside a w x h BGRA buffer."""
    alpha = raw[3::4]
    rows = [r for r in range(h) if alpha[r * w:(r + 1) * w].strip(b"\x00")]
    if not rows:
        return None
    left, right = w, 0
    for r in range(rows[0], rows[-1] + 1):
        line = alpha[r * w:(r + 1) * w]
        s = len(line) - len(line.lstrip(b"\x00"))
        if s < w:
            left = min(left, s)
            right = max(right, w - (len(line) - len(line.rstrip(b"\x00"))))
    return left, rows[0], right - left, rows[-1] - rows[0] + 1


# ------------------------------------------------------------------------------------------- export layers
def _walk(node, path, merge_groups, include_hidden):
    for c in reversed(node.childNodes()):  # top first, like the Layers docker
        if c.type() in MASKS or (not include_hidden and not c.visible()):
            continue
        here = path + [c.name()]
        if c.type() == "grouplayer" and not merge_groups:
            for item in _walk(c, here, merge_groups, include_hidden):
                yield item
        else:
            yield c, here


@handler("layers.export")
def layers_export(p):
    """Each layer to its own PNG (cropped to what is on it) plus manifest.json with names, positions and order -
    what a game engine or a web page needs to put the picture back together."""
    doc = active_doc()
    folder = safe_path(p.get("folder") or "layers")
    folder.mkdir(parents=True, exist_ok=True)
    trim = p.get("trim", True)
    if p.get("layers"):
        chosen = []
        for ref in p["layers"]:
            n = find_layer(doc, ref)
            names, walk = [], n
            while walk is not None and walk != doc.rootNode():
                names.insert(0, walk.name())
                walk = walk.parentNode()
            chosen.append((n, names))
    else:
        chosen = list(_walk(doc.rootNode(), [], bool(p.get("merge_groups")), bool(p.get("include_hidden"))))
    if not chosen:
        raise BridgeError("no layers to export")
    if len(chosen) > MAX_EXPORTS:
        raise BridgeError("too many layers (%d, max %d)" % (len(chosen), MAX_EXPORTS))
    _refresh(doc)
    used, items = set(), []
    for z, (n, names) in enumerate(reversed(chosen)):  # z: 0 = the bottom layer
        box = _clip(doc, n.bounds()) if trim else (0, 0, doc.width(), doc.height())
        if box is None:
            continue
        x, y, w, h = box
        raw = bytes(n.projectionPixelData(x, y, w, h))
        if trim:
            inner = _trim(raw, w, h)
            if inner is None:
                continue
            ix, iy, w, h = inner
            x, y = x + ix, y + iy
            raw = bytes(n.projectionPixelData(x, y, w, h))
        path = _unique(folder, _slug("_".join(names)), used)
        _save(_rgba_image(raw, w, h), path)
        items.append({"name": n.name(), "path": "/".join(names), "file": path.name, "x": x, "y": y, "width": w,
                      "height": h, "z": z, "opacity": round(n.opacity() / 255.0, 3), "visible": n.visible(),
                      "blend": n.blendingMode(), "id": _uid(n)})
    items.reverse()
    manifest = {"document": doc.name(), "width": doc.width(), "height": doc.height(), "layers": items}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"folder": str(folder), "manifest": str(folder / "manifest.json"), "exported": len(items),
            "layers": [{"file": i["file"], "x": i["x"], "y": i["y"], "width": i["width"], "height": i["height"]}
                       for i in items[:50]]}


# ------------------------------------------------------------------------------------------- sprite sheets
@handler("image.slice_sheet")
def image_slice_sheet(p):
    """Cut a sprite sheet or tileset (the picture, or one layer) into a grid of tiles, each saved as a PNG."""
    doc = active_doc()
    W, H = doc.width(), doc.height()
    margin = _num(p, "margin", 0, 0, 4096, True)
    spacing = _num(p, "spacing", 0, 0, 4096, True)
    if p.get("columns") or p.get("rows"):
        cols = _num(p, "columns", 1, 1, 1024, True)
        rows = _num(p, "rows", 1, 1, 1024, True)
        tw = (W - 2 * margin - spacing * (cols - 1)) // cols
        th = (H - 2 * margin - spacing * (rows - 1)) // rows
    else:
        tw = _num(p, "tile_width", None, 1, W, True)
        th = _num(p, "tile_height", tw, 1, H, True)
    grid = pixels.tile_grid(W, H, tw, th, margin, spacing)
    if not grid:
        raise BridgeError("no whole %dx%d tile fits in the %dx%d picture" % (tw, th, W, H))
    if len(grid) > MAX_TILES:
        raise BridgeError("too many tiles (%d, max %d) - use bigger tiles" % (len(grid), MAX_TILES))
    folder = safe_path(p.get("folder") or "tiles")
    folder.mkdir(parents=True, exist_ok=True)
    prefix = _slug(p.get("prefix") or "tile")
    node = find_layer(doc, p["layer"]) if p.get("layer") else None
    skip_empty = p.get("skip_empty", True)
    _refresh(doc)
    tiles, skipped = [], 0
    for i, (x, y, r, c) in enumerate(grid):
        img = (_rgba_image(bytes(node.projectionPixelData(x, y, tw, th)), tw, th) if node is not None
               else doc.projection(x, y, tw, th).convertToFormat(FMT_ARGB32))
        if skip_empty:
            raw = img.constBits().asstring(img.sizeInBytes())
            if not raw[3::4].strip(b"\x00"):
                skipped += 1
                continue
        path = folder / ("%s_%03d.png" % (prefix, i))
        _save(img, path)
        tiles.append({"file": path.name, "index": i, "row": r, "column": c, "x": x, "y": y})
    manifest = {"source": node.name() if node is not None else doc.name(), "tile_width": tw, "tile_height": th,
                "columns": max(t[3] for t in grid) + 1, "rows": max(t[2] for t in grid) + 1, "margin": margin,
                "spacing": spacing, "tiles": tiles}
    (folder / ("%s.json" % prefix)).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"folder": str(folder), "manifest": str(folder / ("%s.json" % prefix)), "saved": len(tiles),
            "skipped_empty": skipped, "tile_width": tw, "tile_height": th, "columns": manifest["columns"],
            "rows": manifest["rows"]}


# ------------------------------------------------------------------------------------------- palettes
def _area(doc, node, p):
    """(x, y, w, h, selection) to work on: the selection's box if there is one, else the layer's content."""
    sel = doc.selection()
    if sel is not None and sel.width() > 0 and not p.get("ignore_selection"):
        box = (sel.x(), sel.y(), sel.width(), sel.height())
    else:
        sel, box = None, _clip(doc, node.bounds())
    if box is None:
        raise BridgeError("layer %r is empty" % node.name())
    if box[2] * box[3] > MAX_PALETTE_PIXELS:
        raise BridgeError("the area is too big for this (%dx%d; max %d pixels) - select a part, or scale the "
                          "picture down" % (box[2], box[3], MAX_PALETTE_PIXELS))
    return box + (sel,)


def _write(node, raw, out, x, y, w, h, sel):
    new = QImage(out, w, h, w * 4, FMT_ARGB32).copy()
    if sel is not None:  # only inside the (soft) selection
        mask = QImage(bytes(sel.pixelData(x, y, w, h)), w, h, w, FMT_ALPHA8).copy()
        pt = QPainter(new)
        pt.setCompositionMode(CM_IN)
        pt.drawImage(0, 0, mask)
        pt.end()
        base = _rgba_image(raw, w, h)
        pt = QPainter(base)
        pt.drawImage(0, 0, new)
        pt.end()
        new = base
    write_image(node, new, x, y)


def _hex(c):
    return "#%02x%02x%02x" % tuple(c[:3])


def _colors(value, what):
    if not isinstance(value, list) or not value:
        raise BridgeError("%s must be a non-empty list of colours" % what)
    if len(value) > pixels.MAX_PALETTE:
        raise BridgeError("%s: at most %d colours" % (what, pixels.MAX_PALETTE))
    return [parse_color(c) for c in value]


@handler("layer.quantize", edit=True)
def layer_quantize(p):
    """Repaint a layer with a limited palette: the colours you give, or the best `colors` found in it."""
    doc = active_doc()
    node = paint_layer(doc, p.get("layer"))
    x, y, w, h, sel = _area(doc, node, p)
    raw = bytes(node.pixelData(x, y, w, h))
    if p.get("palette"):
        palette = [c[:3] for c in _colors(p["palette"], "palette")]
    else:
        palette = pixels.median_cut(raw, _num(p, "colors", 16, 2, pixels.MAX_PALETTE, True))
        if not palette:
            raise BridgeError("layer %r has no opaque pixels to take colours from" % node.name())
    dither = str(p.get("dither") or "none").lower()
    if dither not in ("none", "ordered", "floyd"):
        raise BridgeError("dither must be none, ordered or floyd")
    threshold = p.get("alpha_threshold")
    out = pixels.quantize(raw, w, h, palette, dither, _num(p, "strength", 48, 0, 255),
                          None if threshold is None else _num(p, "alpha_threshold", 128, 1, 255, True))
    history.backup_layer(node, doc)
    _write(node, raw, out, x, y, w, h, sel)
    _refresh(doc)
    return {"layer": node_info(node), "palette": [_hex(c) for c in palette], "dither": dither,
            "area": [x, y, w, h]}


@handler("layer.swap_colors", edit=True)
def layer_swap_colors(p):
    """Palette swap: replace each `from` colour with its `to` colour (recolour a sprite, team colours...)."""
    doc = active_doc()
    node = paint_layer(doc, p.get("layer"))
    pairs = p.get("pairs")
    if isinstance(pairs, dict):
        pairs = list(pairs.items())
    if not isinstance(pairs, list) or not pairs:
        raise BridgeError("pairs must be a list like [[\"#ff0000\", \"#0000ff\"], ...]")
    swaps = []
    for pair in pairs:
        if isinstance(pair, dict):
            pair = (pair.get("from"), pair.get("to"))
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise BridgeError("each pair is [from colour, to colour]")
        swaps.append((parse_color(pair[0]), parse_color(pair[1])))
    x, y, w, h, sel = _area(doc, node, p)
    raw = bytes(node.pixelData(x, y, w, h))
    out, changed = pixels.swap_colors(raw, swaps, _num(p, "tolerance", 0, 0, 255, True))
    if changed:
        history.backup_layer(node, doc)
        _write(node, raw, out, x, y, w, h, sel)
        _refresh(doc)
    return {"layer": node_info(node), "changed_pixels": changed}


@handler("layer.palette")
def layer_palette(p):
    """The colours a layer uses (exact, when there are few - pixel art) or its main ones."""
    doc = active_doc()
    node = find_layer(doc, p.get("layer"))
    box = _clip(doc, node.bounds())
    if box is None:
        return {"colors": [], "exact": True}
    x, y, w, h = box
    raw = bytes(node.projectionPixelData(x, y, w, h))
    found = pixels.used_colors(raw)
    if len(found) <= _num(p, "max_colors", 64, 1, pixels.MAX_PALETTE, True):
        return {"colors": [_hex(c) for c in sorted(found, key=lambda c: 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2])],
                "exact": True}
    main = pixels.median_cut(raw, _num(p, "max_colors", 64, 1, pixels.MAX_PALETTE, True))
    return {"colors": [_hex(c) for c in main], "exact": False}


# ------------------------------------------------------------------------------------------- outlines
def _put_below(doc, new, node):
    parent = node.parentNode()
    kids = parent.childNodes()
    k = kids.index(node)
    if k > 0:
        parent.addChildNode(new, kids[k - 1])
    else:  # `node` is the bottom one: put `new` above it, then lift `node` over `new`
        parent.addChildNode(new, node)
        node.remove()
        parent.addChildNode(node, new)


@handler("layer.outline", edit=True)
def layer_outline(p):
    """An outline around what is on a layer (a sprite, text, a cut-out), on its own layer below - or inside the
    edge, on a layer above."""
    doc = active_doc()
    node = find_layer(doc, p.get("layer"))
    W, H = doc.width(), doc.height()
    width = _num(p, "width", 1, 1, 64, True)
    shape = str(p.get("shape") or "round").lower()
    if shape not in ("round", "square", "diamond"):
        raise BridgeError("shape must be round, square or diamond")
    position = str(p.get("position") or "outside").lower()
    if position not in ("outside", "inside"):
        raise BridgeError("position must be outside or inside")
    color = parse_color(p.get("color") or "#000000")
    _refresh(doc)
    raw = bytes(node.projectionPixelData(0, 0, W, H))
    solid = pixels.alpha_mask(raw, _num(p, "threshold", 128, 1, 255, True))
    if not solid.strip(b"\x00"):
        raise BridgeError("layer %r has nothing on it to outline" % node.name())
    if position == "outside":
        ring = pixels.mask_and_not(pixels.dilate(solid, W, H, width, shape), solid)
    else:
        ring = pixels.mask_and_not(solid, pixels.erode(solid, W, H, width, shape))
    out = pixels.paint_mask(ring, color)
    if p.get("new_layer", True):
        target = doc.createNode(str(p.get("name") or "%s outline" % node.name())[:120], "paintlayer")
        if position == "outside":
            _put_below(doc, target, node)
        else:
            node.parentNode().addChildNode(target, node)
        write_image(target, QImage(out, W, H, W * 4, FMT_ARGB32).copy(), 0, 0)
    else:
        target = paint_layer(doc, p.get("layer"))
        history.backup_layer(target, doc)
        img = _rgba_image(bytes(target.pixelData(0, 0, W, H)), W, H)
        pt = QPainter(img)
        if position == "outside":  # behind the existing pixels
            pt.setCompositionMode(_enum(QPainter, "CompositionMode", "CompositionMode_DestinationOver"))
        pt.drawImage(0, 0, QImage(out, W, H, W * 4, FMT_ARGB32).copy())
        pt.end()
        write_image(target, img, 0, 0)
    _refresh(doc)
    return {"layer": node_info(target), "outline_pixels": ring.count(b"\xff")}


# ------------------------------------------------------------------------------------------- ink strokes
def _chaikin(pts, rounds):
    for _ in range(rounds):
        if len(pts) < 3:
            break
        out = [pts[0]]
        for a, b in zip(pts, pts[1:]):
            out.append(tuple(0.75 * u + 0.25 * v for u, v in zip(a, b)))
            out.append(tuple(0.25 * u + 0.75 * v for u, v in zip(a, b)))
        out.append(pts[-1])
        pts = out
    return pts


def _signed_area(pts):
    return sum(a.x() * b.y() - b.x() * a.y() for a, b in zip(pts, pts[1:] + pts[:1]))


_TURN = []


def _ellipse_turn():
    """Which way Qt draws ellipses; every quad must turn the same way, or with the winding fill rule a quad
    and a dab cancel out where they overlap and leave holes."""
    if not _TURN:
        e = QPainterPath()
        e.addEllipse(QPointF(0, 0), 10, 10)
        poly = e.toFillPolygon()
        _TURN.append(_signed_area([poly[i] for i in range(poly.size() if hasattr(poly, "size") else len(poly))]) > 0)
    return _TURN[0]


def ink_path(points, width, taper_start=0.2, taper_end=0.35, smoothing=2, min_width=0.0):
    """A filled outline for a stroke whose width follows each point's pressure and thins at the ends: round
    dabs joined by the tangent quads between them, filled once (no darker overlaps when semi-transparent)."""
    pts = []
    for q in points:
        if isinstance(q, dict):
            q = (q.get("x"), q.get("y"), q.get("pressure", 1.0))
        if not isinstance(q, (list, tuple)) or len(q) < 2:
            raise BridgeError("each point is [x, y] or [x, y, pressure 0..1]")
        pts.append((float(q[0]), float(q[1]), max(0.0, min(1.0, float(q[2]) if len(q) > 2 else 1.0))))
    if not pts:
        raise BridgeError("the stroke needs points")
    pts = _chaikin(pts, smoothing)
    dist = [0.0]
    for a, b in zip(pts, pts[1:]):
        dist.append(dist[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = dist[-1] or 1.0
    radii = []
    for (x, y, pr), d in zip(pts, dist):
        t = 1.0
        if taper_start > 0 and d < taper_start * total:
            t = min(t, d / (taper_start * total))
        if taper_end > 0 and total - d < taper_end * total:
            t = min(t, (total - d) / (taper_end * total))
        t = math.sin(t * math.pi / 2)  # a soft, brush-like taper
        radii.append(max(min_width / 2.0, width * pr * t / 2.0))
    turn = _ellipse_turn()
    path = QPainterPath()
    path.setFillRule(WINDING)
    for (x, y, _), r in zip(pts, radii):
        if r > 0.05:
            path.addEllipse(QPointF(x, y), r, r)
    for (a, ra), (b, rb) in zip(zip(pts, radii), zip(pts[1:], radii[1:])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length < 1e-6 or (ra <= 0.05 and rb <= 0.05):
            continue
        nx, ny = -dy / length, dx / length
        quad = [QPointF(a[0] + nx * ra, a[1] + ny * ra), QPointF(b[0] + nx * rb, b[1] + ny * rb),
                QPointF(b[0] - nx * rb, b[1] - ny * rb), QPointF(a[0] - nx * ra, a[1] - ny * ra)]
        if (_signed_area(quad) > 0) != turn:
            quad.reverse()
        path.addPolygon(QPolygonF(quad))
        path.closeSubpath()
    return path


@handler("draw.ink", edit=True)
def draw_ink(p):
    """Variable-width ink strokes - line art, lettering, calligraphy: thick where pressure is high, tapered ends."""
    doc = active_doc()
    strokes = p.get("strokes") or ([{"points": p.get("points")}] if p.get("points") else None)
    if not isinstance(strokes, list) or not strokes:
        raise BridgeError("give 'points' (one stroke) or 'strokes' (a list of {points, ...})")
    if sum(len(s.get("points") or []) for s in strokes if isinstance(s, dict)) > MAX_INK_POINTS:
        raise BridgeError("too many points (max %d per call)" % MAX_INK_POINTS)
    node = paint_layer(doc, p.get("layer"))
    W, H = doc.width(), doc.height()
    paths = []
    for s in strokes:
        if not isinstance(s, dict):
            raise BridgeError("each stroke is {points: [[x, y, pressure], ...], width, color, ...}")
        get = lambda k, d: s.get(k, p.get(k, d))  # noqa: E731 - a stroke's own setting, else the call's
        width = float(_num({"w": get("width", 8)}, "w", 8, 0.5, 500))
        color = QColor(*parse_color(get("color", "#000000")))
        color.setAlphaF(color.alphaF() * float(_num({"o": get("opacity", 1.0)}, "o", 1.0, 0, 1)))
        path = ink_path(s.get("points") or [], width, float(get("taper_start", 0.2)), float(get("taper_end", 0.35)),
                        int(_num({"s": get("smoothing", 2)}, "s", 2, 0, 5, True)), float(get("min_width", 0.0)))
        paths.append((path, color))
    box = None
    for path, _ in paths:
        r = path.boundingRect()
        box = r if box is None else box.united(r)
    x0, y0 = max(0, int(math.floor(box.x())) - 2), max(0, int(math.floor(box.y())) - 2)
    x1 = min(W, int(math.ceil(box.x() + box.width())) + 2)
    y1 = min(H, int(math.ceil(box.y() + box.height())) + 2)
    if x1 <= x0 or y1 <= y0:
        raise BridgeError("the strokes are outside the canvas")
    history.backup_layer(node, doc)
    img = read_image(node, x0, y0, x1 - x0, y1 - y0)
    pt = QPainter(img)
    try:
        pt.setRenderHint(AA, bool(p.get("antialias", True)))
        pt.translate(-x0, -y0)
        pt.setPen(NO_PEN)
        for path, color in paths:
            pt.setBrush(QBrush(color))
            pt.drawPath(path)
    finally:
        pt.end()
    write_image(node, img, x0, y0)
    _refresh(doc)
    return {"strokes": len(paths), "area": [x0, y0, x1 - x0, y1 - y0], "layer": node_info(node)}
