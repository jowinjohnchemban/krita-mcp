"""Krita-side command handlers. Everything here runs on Krita's GUI thread.

Pixel model: all raster edits go through QImage (Format_ARGB32, which is
byte-identical to Krita's BGRA 8-bit layout) and Node.setPixelData, so layers
must be RGBA / 8-bit integer. Documents created by `document.new` always are.
"""
import base64
import math

from krita import InfoObject, Krita, ManagedColor, Selection

try:  # Krita 6
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QPoint, QPointF, QRectF, Qt
    from PyQt6.QtGui import (QBrush, QColor, QFont, QImage, QLinearGradient, QPainter,
                             QPainterPath, QPen, QPolygonF, QRadialGradient)
    from PyQt6.QtWidgets import QAbstractItemView
except ImportError:  # Krita 5
    from PyQt5.QtCore import QBuffer, QByteArray, QIODevice, QPoint, QPointF, QRectF, Qt
    from PyQt5.QtGui import (QBrush, QColor, QFont, QImage, QLinearGradient, QPainter,
                             QPainterPath, QPen, QPolygonF, QRadialGradient)
    from PyQt5.QtWidgets import QAbstractItemView

from .util import DOC_EXTS, IMAGE_EXTS, OPEN_EXTS, BridgeError, parse_color, safe_path, workspace_root

BRIDGE_VERSION = "0.1.0"
MAX_DIM = 16384
MAX_PIXELS = 100_000_000
MAX_SHAPES = 5000
MAX_IMAGE_BYTES = 48 * 1024 * 1024


def _enum(owner, group, member):
    """Qt5/Qt6-agnostic enum lookup (Qt6 needs the scoped form)."""
    g = getattr(owner, group, None)
    if g is not None and hasattr(g, member):
        return getattr(g, member)
    return getattr(owner, member)


FMT_ARGB32 = _enum(QImage, "Format", "Format_ARGB32")
FMT_ALPHA8 = _enum(QImage, "Format", "Format_Alpha8")
AA = _enum(QPainter, "RenderHint", "Antialiasing")
AA_TEXT = _enum(QPainter, "RenderHint", "TextAntialiasing")
AA_PIX = _enum(QPainter, "RenderHint", "SmoothPixmapTransform")
CM_IN = _enum(QPainter, "CompositionMode", "CompositionMode_DestinationIn")
CM_OUT = _enum(QPainter, "CompositionMode", "CompositionMode_DestinationOut")
CAP_ROUND = _enum(Qt, "PenCapStyle", "RoundCap")
JOIN_ROUND = _enum(Qt, "PenJoinStyle", "RoundJoin")
NO_PEN = _enum(Qt, "PenStyle", "NoPen")
NO_BRUSH = _enum(Qt, "BrushStyle", "NoBrush")
KEEP_AR = _enum(Qt, "AspectRatioMode", "KeepAspectRatio")
SMOOTH = _enum(Qt, "TransformationMode", "SmoothTransformation")
WRITE_ONLY = _enum(QIODevice, "OpenModeFlag", "WriteOnly")
TRANSPARENT = _enum(Qt, "GlobalColor", "transparent")

HANDLERS = {}


def handler(name):
    def deco(fn):
        HANDLERS[name] = fn
        return fn
    return deco


def dispatch(method, params):
    fn = HANDLERS.get(method)
    if fn is None:
        raise BridgeError("Unknown method %r" % method)
    return fn(params)


# ---------------------------------------------------------------- helpers
def app():
    return Krita.instance()


def active_doc():
    d = app().activeDocument()
    if d is None:
        raise BridgeError("No active document. Call document_new or document_open first.")
    return d


def _num(p, key, default=None, lo=None, hi=None, integer=False):
    v = p.get(key, default)
    if v is None:
        raise BridgeError("missing required parameter %r" % key)
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise BridgeError("%r must be a number" % key)
    if math.isnan(v) or math.isinf(v):
        raise BridgeError("%r must be finite" % key)
    if lo is not None and v < lo:
        raise BridgeError("%r must be >= %s" % (key, lo))
    if hi is not None and v > hi:
        raise BridgeError("%r must be <= %s" % (key, hi))
    return int(round(v)) if integer else v


def _qcolor(value, default=None):
    c = parse_color(value, default)
    return None if c is None else QColor(*c)


def _uid(n):
    return n.uniqueId().toString().strip("{}")


def _walk(node):
    for c in node.childNodes():
        yield c
        yield from _walk(c)


def find_layer(doc, ref):
    if ref in (None, "", "active"):
        n = doc.activeNode()
        if n is None:
            raise BridgeError("No active layer")
        return n
    ref = str(ref).strip("{}")
    nodes = list(_walk(doc.rootNode()))
    for n in nodes:
        if _uid(n) == ref:
            return n
    named = [n for n in nodes if n.name() == ref]
    if len(named) == 1:
        return named[0]
    if len(named) > 1:
        raise BridgeError("Several layers are named %r; use the layer id from layer_list" % ref)
    raise BridgeError("No layer with id/name %r" % ref)


def node_info(n, deep=False):
    b = n.bounds()
    d = {
        "id": _uid(n), "name": n.name(), "type": n.type(), "visible": n.visible(),
        "locked": n.locked(), "opacity": round(n.opacity() / 255.0, 3),
        "blend_mode": n.blendingMode(), "bounds": [b.x(), b.y(), b.width(), b.height()],
    }
    for key, getter in (("alpha_locked", "alphaLocked"), ("clipped", "inheritAlpha")):
        try:
            d[key] = getattr(n, getter)()
        except AttributeError:  # not every node type has it
            pass
    if deep:
        d["children"] = [node_info(c, True) for c in n.childNodes()]  # bottom -> top
    return d


def doc_info(doc):
    return {
        "name": doc.name(), "file": doc.fileName() or None, "width": doc.width(),
        "height": doc.height(), "resolution_ppi": doc.xRes(), "color_model": doc.colorModel(),
        "color_depth": doc.colorDepth(), "modified": doc.modified(),
    }


def paint_layer(doc, ref, need_unlocked=True):
    n = find_layer(doc, ref)
    if n.type() != "paintlayer":
        raise BridgeError("Layer %r is a %s; pixel operations need a paint layer" % (n.name(), n.type()))
    if n.colorModel() != "RGBA" or n.colorDepth() != "U8":
        raise BridgeError("Layer is %s/%s; only RGBA 8-bit integer layers are supported "
                          "(Image > Convert Image Color Space)" % (n.colorModel(), n.colorDepth()))
    if need_unlocked and n.locked():
        raise BridgeError("Layer %r is locked" % n.name())
    return n


def read_image(node, x, y, w, h):
    raw = bytes(node.pixelData(x, y, w, h))
    return QImage(raw, w, h, w * 4, FMT_ARGB32).copy()  # copy -> detach from `raw`


def write_image(node, img, x, y):
    img = img.convertToFormat(FMT_ARGB32)
    data = img.constBits().asstring(img.sizeInBytes())
    node.setPixelData(QByteArray(data), x, y, img.width(), img.height())


def _refresh(doc):
    doc.refreshProjection()
    try:
        doc.waitForDone()
    except Exception:  # noqa: BLE001 - older builds
        pass


def _b64png(img):
    buf = QBuffer()
    buf.open(WRITE_ONLY)
    img.save(buf, "PNG")
    return base64.b64encode(bytes(buf.data())).decode()


def _load_qimage(p):
    if p.get("path"):
        img = QImage(str(safe_path(p["path"], IMAGE_EXTS)))
    elif p.get("data_base64"):
        try:
            raw = base64.b64decode(p["data_base64"])
        except Exception:  # noqa: BLE001
            raise BridgeError("data_base64 is not valid base64")
        if len(raw) > MAX_IMAGE_BYTES:
            raise BridgeError("image larger than %d MB" % (MAX_IMAGE_BYTES // 2**20))
        img = QImage.fromData(QByteArray(raw))
    else:
        raise BridgeError("provide either 'path' (inside the workspace) or 'data_base64'")
    if img.isNull():
        raise BridgeError("Could not decode image")
    if img.width() * img.height() > MAX_PIXELS:
        raise BridgeError("image too large")
    return img.convertToFormat(FMT_ARGB32)


def _rgba_image(raw, w, h):
    """QImage (ARGB32) from Krita BGRA 8-bit bytes."""
    return QImage(bytes(raw), w, h, w * 4, FMT_ARGB32).copy()


def _active_view(doc):
    """The view showing `doc` in the active window, made current (brush painting uses its resources)."""
    win = app().activeWindow()
    if win is None:
        raise BridgeError("Krita has no open window")
    view = win.activeView()
    if view is None or view.document() != doc:
        view = next((v for v in win.views() if v.document() == doc), None) or win.addView(doc)
        win.showView(view)
    return view


def _mask_selection(mask, x, y):
    """Selection from an Alpha8 QImage placed at (x, y)."""
    w, h = mask.width(), mask.height()
    bits = mask.constBits().asstring(mask.sizeInBytes())
    bpl = mask.bytesPerLine()
    data = bits if bpl == w else b"".join(bits[r * bpl:r * bpl + w] for r in range(h))
    sel = Selection()
    sel.setPixelData(QByteArray(data), x, y, w, h)
    return sel


def _canvas_selection(doc):
    sel = Selection()
    sel.select(0, 0, doc.width(), doc.height(), 255)
    return sel


def _configured_filter(name, params):
    f = app().filter(str(name))
    if f is None:
        raise BridgeError("Unknown filter %r. Call filter_list." % name)
    cfg = f.configuration()
    for k, v in (params or {}).items():
        if not isinstance(v, (str, int, float, bool)):
            raise BridgeError("filter param %r must be a string, number or boolean" % k)
        cfg.setProperty(str(k), v)
    f.setConfiguration(cfg)
    return f


# -------------------------------------------------------------------- app
@handler("app.info")
def app_info(p):
    a = app()
    active = a.activeDocument()
    docs = []
    for i, d in enumerate(a.documents()):
        info = doc_info(d)
        info.update(index=i, active=(active is not None and d == active))
        docs.append(info)
    return {"krita_version": a.version(), "bridge_version": BRIDGE_VERSION,
            "workspace": str(workspace_root()), "documents": docs}


# -------------------------------------------------------------- documents
@handler("document.new")
def document_new(p):
    w = _num(p, "width", 1024, 1, MAX_DIM, True)
    h = _num(p, "height", 1024, 1, MAX_DIM, True)
    if w * h > MAX_PIXELS:
        raise BridgeError("canvas too large (max %d megapixels)" % (MAX_PIXELS // 10**6))
    res = _num(p, "resolution_ppi", 72, 1, 2400)
    name = str(p.get("name") or "Untitled")[:120]
    bg = p.get("background", "white")
    doc = app().createDocument(w, h, name, "RGBA", "U8", "sRGB-elle-V2-srgbtrc.icc", res)
    if doc is None:
        doc = app().createDocument(w, h, name, "RGBA", "U8", "", res)
    if doc is None:
        raise BridgeError("Krita refused to create the document")
    win = app().activeWindow()
    if win:
        win.addView(doc)
    app().setActiveDocument(doc)
    root = doc.rootNode()
    if not root.childNodes():
        root.addChildNode(doc.createNode("Background", "paintlayer"), None)
    layer = root.childNodes()[0]
    c = parse_color(bg, (255, 255, 255, 255))
    if c[3] > 0:
        img = QImage(w, h, FMT_ARGB32)
        img.fill(QColor(*c))
        write_image(layer, img, 0, 0)
    doc.setActiveNode(layer)
    _refresh(doc)
    return doc_info(doc)


@handler("document.open")
def document_open(p):
    path = safe_path(p.get("path"), OPEN_EXTS)
    if not path.exists():
        raise BridgeError("File not found: %s" % path)
    doc = app().openDocument(str(path))
    if doc is None:
        raise BridgeError("Krita could not open %s" % path)
    win = app().activeWindow()
    if win:
        win.addView(doc)
    app().setActiveDocument(doc)
    return doc_info(doc)


@handler("document.activate")
def document_activate(p):
    docs = app().documents()
    i = _num(p, "index", None, 0, len(docs) - 1 if docs else 0, True)
    if not docs:
        raise BridgeError("No open documents")
    app().setActiveDocument(docs[i])
    return doc_info(docs[i])


@handler("document.info")
def document_get_info(p):
    doc = active_doc()
    info = doc_info(doc)
    info["layers"] = [node_info(c, True) for c in doc.rootNode().childNodes()]
    sel = doc.selection()
    info["selection"] = None if sel is None else [sel.x(), sel.y(), sel.width(), sel.height()]
    return info


@handler("document.save")
def document_save(p):
    doc = active_doc()
    doc.setBatchmode(True)  # never pop up dialogs
    if p.get("path"):
        path = safe_path(p["path"], DOC_EXTS)
        ok = doc.saveAs(str(path))
    else:
        if not doc.fileName():
            raise BridgeError("Document has never been saved; pass a 'path' ending in .kra")
        safe_path(doc.fileName(), DOC_EXTS)
        ok = doc.save()
        path = doc.fileName()
    if not ok:
        raise BridgeError("Krita failed to save the document")
    return {"path": str(path)}


@handler("document.export")
def document_export(p):
    doc = active_doc()
    path = safe_path(p.get("path"), IMAGE_EXTS)
    cfg = InfoObject()
    if p.get("quality") is not None:  # jpeg/webp quality 0..100
        cfg.setProperty("quality", _num(p, "quality", None, 0, 100, True))
    doc.setBatchmode(True)
    _refresh(doc)
    if p.get("layer"):  # one layer only, cropped to its content unless the whole canvas is asked for
        n = find_layer(doc, p["layer"])
        b = n.bounds()
        rect = doc.bounds() if p.get("full_canvas") or b.width() <= 0 else b
        before = path.stat().st_mtime_ns if path.exists() else None
        n.save(str(path), doc.xRes(), doc.yRes(), cfg, rect)  # returns nothing; check the file instead
        if not path.exists() or path.stat().st_mtime_ns == before:
            raise BridgeError("Krita failed to export layer %r to %s" % (n.name(), path))
    elif not doc.exportImage(str(path), cfg):
        raise BridgeError("Krita failed to export %s" % path)
    return {"path": str(path), "bytes": path.stat().st_size if path.exists() else None}


@handler("document.close")
def document_close(p):
    doc = active_doc()
    saved = None
    if p.get("save"):  # Document.close() never saves by itself
        saved = document_save({})["path"]
    doc.setModified(False)
    doc.setBatchmode(True)
    doc.close()
    return {"saved": saved}


@handler("document.resize_canvas")
def document_resize_canvas(p):
    doc = active_doc()
    w = _num(p, "width", None, 1, MAX_DIM, True)
    h = _num(p, "height", None, 1, MAX_DIM, True)
    if w * h > MAX_PIXELS:
        raise BridgeError("canvas too large")
    doc.resizeImage(_num(p, "x", 0, integer=True), _num(p, "y", 0, integer=True), w, h)
    _refresh(doc)
    return doc_info(doc)


@handler("document.scale")
def document_scale(p):
    doc = active_doc()
    w = _num(p, "width", None, 1, MAX_DIM, True)
    h = _num(p, "height", None, 1, MAX_DIM, True)
    if w * h > MAX_PIXELS:
        raise BridgeError("image too large")
    doc.scaleImage(w, h, int(doc.xRes()), int(doc.yRes()), "Bicubic")
    _refresh(doc)
    return doc_info(doc)


@handler("document.crop")
def document_crop(p):
    """Crop the canvas to a rectangle, or to the selection bounds when no rectangle is given."""
    doc = active_doc()
    if "width" in p or "height" in p:
        x, y = _num(p, "x", 0, integer=True), _num(p, "y", 0, integer=True)
        w, h = _num(p, "width", None, 1, MAX_DIM, True), _num(p, "height", None, 1, MAX_DIM, True)
    else:
        sel = doc.selection()
        if sel is None or sel.width() <= 0:
            raise BridgeError("give x, y, width, height or make a selection first")
        x, y, w, h = sel.x(), sel.y(), sel.width(), sel.height()
    doc.crop(x, y, w, h)
    _refresh(doc)
    return doc_info(doc)


@handler("document.rotate")
def document_rotate(p):
    """Rotate the whole image clockwise; the canvas grows to fit."""
    doc = active_doc()
    doc.rotateImage(math.radians(_num(p, "degrees", None, -360, 360)))
    _refresh(doc)
    return doc_info(doc)


@handler("document.flatten")
def document_flatten(p):
    doc = active_doc()
    doc.flatten()
    _refresh(doc)
    return {"layers": [node_info(c) for c in doc.rootNode().childNodes()]}


_MIRROR_ACTIONS = {
    ("image", "horizontal"): "mirrorImageHorizontal", ("image", "vertical"): "mirrorImageVertical",
    ("layer", "horizontal"): "mirrorNodeX", ("layer", "vertical"): "mirrorNodeY",
}


@handler("document.flip")
def document_flip(p):
    """Mirror the whole image or one layer, through Krita's own (undoable) actions."""
    doc = active_doc()
    target, direction = p.get("target", "image"), p.get("direction", "horizontal")
    name = _MIRROR_ACTIONS.get((target, direction))
    if name is None:
        raise BridgeError("target must be image or layer; direction horizontal or vertical")
    _active_view(doc)
    if target == "layer":
        n = find_layer(doc, p.get("layer"))
        if n.locked():
            raise BridgeError("Layer is locked")
        doc.setActiveNode(n)
    act = app().action(name)
    if act is None:
        raise BridgeError("This Krita version has no %r action" % name)
    act.trigger()
    _refresh(doc)
    return doc_info(doc) if target == "image" else node_info(n)


# ----------------------------------------------------------------- canvas
@handler("canvas.snapshot")
def canvas_snapshot(p):
    doc = active_doc()
    _refresh(doc)
    W, H = doc.width(), doc.height()
    x = _num(p, "x", 0, 0, W - 1, True)
    y = _num(p, "y", 0, 0, H - 1, True)
    w = min(_num(p, "width", W - x, 1, None, True), W - x)
    h = min(_num(p, "height", H - y, 1, None, True), H - y)
    if p.get("layer"):  # just this layer (with its masks), transparent elsewhere
        n = find_layer(doc, p["layer"])
        if doc.colorModel() != "RGBA" or doc.colorDepth() != "U8":
            raise BridgeError("layer snapshots need an RGBA 8-bit image")
        img = _rgba_image(n.projectionPixelData(x, y, w, h), w, h)
    else:
        img = doc.projection(x, y, w, h)
    m = _num(p, "max_size", 1024, 64, 4096, True)
    if max(img.width(), img.height()) > m:
        img = img.scaled(m, m, KEEP_AR, SMOOTH)
    return {"png_base64": _b64png(img), "width": img.width(), "height": img.height(),
            "region": [x, y, w, h]}


@handler("canvas.sample")
def canvas_sample(p):
    """Colour of one pixel of the composite image, or of a single layer."""
    doc = active_doc()
    x = _num(p, "x", None, 0, doc.width() - 1, True)
    y = _num(p, "y", None, 0, doc.height() - 1, True)
    if p.get("layer"):
        src = find_layer(doc, p["layer"])
    else:
        _refresh(doc)
        src = doc
    if src.colorModel() != "RGBA" or src.colorDepth() != "U8":
        raise BridgeError("sampling needs an RGBA 8-bit image")
    b, g, r, a = bytes(src.pixelData(x, y, 1, 1))[:4]
    return {"rgba": [r, g, b, a], "hex": "#%02x%02x%02x%02x" % (r, g, b, a)}


# ----------------------------------------------------------------- layers
@handler("layer.list")
def layer_list(p):
    doc = active_doc()
    active = doc.activeNode()
    return {"active": None if active is None else _uid(active),
            "note": "children are listed bottom-to-top",
            "layers": [node_info(c, True) for c in doc.rootNode().childNodes()]}


LAYER_TYPES = ("paintlayer", "vectorlayer", "grouplayer", "filllayer", "filterlayer", "clonelayer")
MASK_TYPES = ("filtermask", "transparencymask")


def _create_node(doc, kind, name, p):
    if kind in ("paintlayer", "vectorlayer", "grouplayer"):
        return doc.createNode(name, kind)
    if kind == "filllayer":
        cfg = InfoObject()
        if p.get("pattern"):
            gen = "pattern"
            cfg.setProperty("pattern", str(p["pattern"]))
        else:
            gen = "color"
            cfg.setProperty("color", _qcolor(p.get("fill") or p.get("color") or "#000000"))
        return doc.createFillLayer(name, gen, cfg, _canvas_selection(doc))
    if kind == "filterlayer":
        if not p.get("filter"):
            raise BridgeError("a filterlayer needs 'filter' (see list_filters)")
        return doc.createFilterLayer(name, _configured_filter(p["filter"], p.get("filter_params")),
                                     _canvas_selection(doc))
    if kind == "clonelayer":
        if not p.get("source"):
            raise BridgeError("a clonelayer needs 'source' (layer id or name)")
        return doc.createCloneLayer(name, find_layer(doc, p["source"]))
    if kind == "filtermask":
        if not p.get("filter"):
            raise BridgeError("a filtermask needs 'filter' (see list_filters)")
        return doc.createFilterMask(name, _configured_filter(p["filter"], p.get("filter_params")),
                                    doc.selection() or _canvas_selection(doc))
    if kind == "transparencymask":
        mask = doc.createTransparencyMask(name)
        mask.setSelection(doc.selection() or _canvas_selection(doc))
        return mask
    raise BridgeError("type must be one of %s" % ", ".join(LAYER_TYPES + MASK_TYPES))


@handler("layer.create")
def layer_create(p):
    doc = active_doc()
    kind = p.get("type", "paintlayer")
    if kind not in LAYER_TYPES + MASK_TYPES:
        raise BridgeError("type must be one of %s" % ", ".join(LAYER_TYPES + MASK_TYPES))
    above = find_layer(doc, p["above"]) if p.get("above") else None
    if p.get("parent"):
        parent = find_layer(doc, p["parent"])
    elif kind in MASK_TYPES:
        parent = find_layer(doc, None)  # masks attach to the active layer by default
    else:
        parent = above.parentNode() if above is not None else doc.rootNode()
    if kind in MASK_TYPES:
        if parent.type() not in ("paintlayer", "grouplayer", "vectorlayer", "filllayer", "clonelayer"):
            raise BridgeError("masks attach to a layer; 'parent' is a %s" % parent.type())
    elif parent != doc.rootNode() and parent.type() != "grouplayer":
        raise BridgeError("parent must be a group layer")
    if above is not None and above.parentNode() != parent:
        raise BridgeError("'above' must be a child of the parent")
    node = _create_node(doc, kind, str(p.get("name") or "Layer")[:120], p)
    if node is None:
        raise BridgeError("Krita could not create a %s with these settings" % kind)
    parent.addChildNode(node, above)
    if kind == "paintlayer" and p.get("fill"):
        img = QImage(doc.width(), doc.height(), FMT_ARGB32)
        img.fill(_qcolor(p["fill"]))
        write_image(node, img, 0, 0)
    doc.setActiveNode(node)
    _refresh(doc)
    return node_info(node)


@handler("layer.update")
def layer_update(p):
    doc = active_doc()
    n = find_layer(doc, p.get("layer"))
    if "name" in p:
        n.setName(str(p["name"])[:120])
    if "visible" in p:
        n.setVisible(bool(p["visible"]))
    if "locked" in p:
        n.setLocked(bool(p["locked"]))
    if "opacity" in p:
        n.setOpacity(int(round(_num(p, "opacity", None, 0, 1) * 255)))
    if "blend_mode" in p:
        n.setBlendingMode(str(p["blend_mode"]))
    if "alpha_locked" in p:
        n.setAlphaLocked(bool(p["alpha_locked"]))
    if "clipped" in p:  # Krita's "inherit alpha": clip to the layers below in the same group
        n.setInheritAlpha(bool(p["clipped"]))
    if "color_label" in p:
        n.setColorLabel(_num(p, "color_label", None, 0, 8, True))
    if any(k in p for k in ("x", "y", "dx", "dy")):
        # Node.move() sets the layer *offset*; x/y address the top-left of the visible content
        # (as reported in "bounds"), dx/dy shift relative to the current place.
        pos, b = n.position(), n.bounds()
        ox, oy = (b.x(), b.y()) if b.width() > 0 else (pos.x(), pos.y())
        tx = _num(p, "x", ox, integer=True) + _num(p, "dx", 0, integer=True)
        ty = _num(p, "y", oy, integer=True) + _num(p, "dy", 0, integer=True)
        n.move(pos.x() + tx - ox, pos.y() + ty - oy)
    if p.get("activate"):
        doc.setActiveNode(n)
    _refresh(doc)
    return node_info(n)


@handler("layer.delete")
def layer_delete(p):
    doc = active_doc()
    n = find_layer(doc, p.get("layer"))
    n.remove()
    _refresh(doc)
    return {}


@handler("layer.duplicate")
def layer_duplicate(p):
    doc = active_doc()
    n = find_layer(doc, p.get("layer"))
    dup = n.duplicate()
    if p.get("name"):
        dup.setName(str(p["name"])[:120])
    n.parentNode().addChildNode(dup, n)
    doc.setActiveNode(dup)
    _refresh(doc)
    return node_info(dup)


@handler("layer.arrange")
def layer_arrange(p):
    """Restack a layer: to top/bottom/up/down within its group, above a sibling, or into another group."""
    doc = active_doc()
    n = find_layer(doc, p.get("layer"))
    if p.get("parent"):
        parent = find_layer(doc, p["parent"])
    elif p.get("above"):
        parent = find_layer(doc, p["above"]).parentNode()
    else:
        parent = n.parentNode()
    if parent != doc.rootNode() and parent.type() != "grouplayer":
        raise BridgeError("parent must be a group layer")
    walk = parent
    while walk is not None:  # refuse to move a group into itself
        if walk == n:
            raise BridgeError("cannot move a group into itself")
        walk = walk.parentNode()
    siblings = [c for c in parent.childNodes() if c != n]  # bottom -> top
    to = p.get("to")
    if p.get("above"):
        above = find_layer(doc, p["above"])
        if above not in siblings:
            raise BridgeError("'above' must be in the same group as the destination")
    elif to in ("top", None):
        above = siblings[-1] if siblings else None
    elif to == "bottom":
        above = None
    elif to in ("up", "down"):
        if n.parentNode() != parent:
            raise BridgeError("up/down move within the layer's own group")
        k = parent.childNodes().index(n) + (1 if to == "up" else -1)  # new slot among siblings
        k = max(0, min(len(siblings), k))
        above = siblings[k - 1] if k > 0 else None
    else:
        raise BridgeError("'to' must be top, bottom, up or down (or give 'above')")
    n.remove()
    if above is None and siblings:
        # addChildNode(x, None) means "on top", so reach the bottom by lifting the old bottom layer over n
        parent.addChildNode(n, siblings[0])
        siblings[0].remove()
        parent.addChildNode(siblings[0], n)
    else:
        parent.addChildNode(n, above)
    doc.setActiveNode(n)
    _refresh(doc)
    return {"layer": node_info(n), "order": [c.name() for c in parent.childNodes()]}


@handler("layer.merge_down")
def layer_merge_down(p):
    doc = active_doc()
    n = find_layer(doc, p.get("layer"))
    parent = n.parentNode()
    kids = parent.childNodes()
    if kids.index(n) == 0:
        raise BridgeError("Layer %r has no layer below it to merge into" % n.name())
    merged = n.mergeDown()
    _refresh(doc)
    if merged is None:  # some builds return nothing; the merged layer takes the lower one's place
        merged = doc.activeNode()
    return node_info(merged)


@handler("layer.transform")
def layer_transform(p):
    doc = active_doc()
    n = find_layer(doc, p.get("layer"))
    if n.locked():
        raise BridgeError("Layer is locked")
    if "scale_x" in p or "scale_y" in p:
        sx, sy = _num(p, "scale_x", 1, 0.01, 50), _num(p, "scale_y", 1, 0.01, 50)
        b = n.bounds()
        nw, nh = max(1, int(b.width() * sx)), max(1, int(b.height() * sy))
        if nw * nh > MAX_PIXELS:
            raise BridgeError("result too large")
        n.scaleNode(QPointF(b.x() + b.width() / 2.0, b.y() + b.height() / 2.0), nw, nh, "Bicubic")
    if p.get("rotate_degrees"):
        n.rotateNode(math.radians(_num(p, "rotate_degrees", 0, -3600, 3600)))
    if "shear_x_degrees" in p or "shear_y_degrees" in p:
        n.shearNode(_num(p, "shear_x_degrees", 0, -80, 80), _num(p, "shear_y_degrees", 0, -80, 80))
    _refresh(doc)
    return node_info(n)


@handler("layer.import_image")
def layer_import_image(p):
    """Place a raster image (from the workspace or base64) on a NEW paint layer."""
    doc = active_doc()
    img = _load_qimage(p)
    W, H, iw, ih = doc.width(), doc.height(), img.width(), img.height()
    explicit = any(k in p for k in ("x", "y", "width", "height"))
    fit = p.get("fit") or ("none" if explicit else "contain")
    if fit == "contain":
        s = min(W / float(iw), H / float(ih)); nw, nh = int(iw * s), int(ih * s)
    elif fit == "cover":
        s = max(W / float(iw), H / float(ih)); nw, nh = int(iw * s), int(ih * s)
    elif fit == "stretch":
        nw, nh = W, H
    elif fit == "none":
        nw, nh = iw, ih
    else:
        raise BridgeError("fit must be contain, cover, stretch or none")
    if "width" in p or "height" in p:
        nw = _num(p, "width", nw, 1, MAX_DIM, True)
        nh = _num(p, "height", nh, 1, MAX_DIM, True)
    nw, nh = max(1, nw), max(1, nh)
    if nw * nh > MAX_PIXELS:
        raise BridgeError("result too large")
    if (nw, nh) != (iw, ih):
        img = img.scaled(nw, nh, _enum(Qt, "AspectRatioMode", "IgnoreAspectRatio"), SMOOTH)
    x = _num(p, "x", (W - nw) // 2, integer=True)
    y = _num(p, "y", (H - nh) // 2, integer=True)
    parent = doc.rootNode() if not p.get("parent") else find_layer(doc, p["parent"])
    node = doc.createNode(str(p.get("name") or "Imported image")[:120], "paintlayer")
    parent.addChildNode(node, None)
    write_image(node, img, x, y)
    doc.setActiveNode(node)
    _refresh(doc)
    return node_info(node)


# ---------------------------------------------------------------- drawing
def _points(s, key="points", minimum=2):
    pts = s.get(key)
    if not isinstance(pts, list) or len(pts) < minimum:
        raise BridgeError("shape needs %r with at least %d [x, y] points" % (key, minimum))
    try:
        return [(float(a), float(b)) for a, b in pts]
    except (TypeError, ValueError):
        raise BridgeError("%r must be a list of [x, y] pairs" % key)


def _gradient(g):
    kind = g.get("type", "linear")
    if kind == "linear":
        grad = QLinearGradient(QPointF(float(g["x1"]), float(g["y1"])), QPointF(float(g["x2"]), float(g["y2"])))
    elif kind == "radial":
        grad = QRadialGradient(QPointF(float(g["cx"]), float(g["cy"])), float(g["r"]))
    else:
        raise BridgeError("gradient type must be linear or radial")
    for pos, col in g.get("stops") or [[0, "#000000"], [1, "#ffffff"]]:
        grad.setColorAt(max(0.0, min(1.0, float(pos))), _qcolor(col))
    return QBrush(grad)


def _draw_shape(pt, s):
    if not isinstance(s, dict):
        raise BridgeError("each shape must be an object")
    t = s.get("type")
    col = s.get("color", "#000000")
    pt.save()
    try:
        pt.setOpacity(max(0.0, min(1.0, float(s.get("opacity", 1)))))
        width = float(s.get("stroke_width", 2))
        if t in ("line", "polyline", "bezier"):
            pen = QPen(_qcolor(s.get("stroke", col)), width)
            pen.setCapStyle(CAP_ROUND); pen.setJoinStyle(JOIN_ROUND)
            pt.setPen(pen); pt.setBrush(NO_BRUSH)
            if t == "line":
                pt.drawLine(QPointF(float(s["x1"]), float(s["y1"])), QPointF(float(s["x2"]), float(s["y2"])))
            elif t == "polyline":
                path = QPainterPath()
                pts = _points(s)
                path.moveTo(*pts[0])
                for q in pts[1:]:
                    path.lineTo(*q)
                pt.drawPath(path)
            else:  # bezier: start + n * (c1, c2, end)
                pts = _points(s, minimum=4)
                if (len(pts) - 1) % 3:
                    raise BridgeError("bezier needs 1 + 3n points (start, then c1, c2, end per segment)")
                path = QPainterPath()
                path.moveTo(*pts[0])
                for i in range(1, len(pts), 3):
                    path.cubicTo(QPointF(*pts[i]), QPointF(*pts[i + 1]), QPointF(*pts[i + 2]))
                pt.drawPath(path)
            return
        if t == "text":
            f = QFont(str(s.get("font", "Sans Serif")))
            f.setPixelSize(int(float(s.get("size", 32))))
            f.setBold(bool(s.get("bold", False)))
            pt.setFont(f)
            pt.setPen(QPen(_qcolor(col)))
            pt.drawText(QPointF(float(s["x"]), float(s["y"])), str(s.get("text", ""))[:2000])
            return
        # closed shapes
        fill = s.get("fill", col if "stroke" not in s else None)
        stroke = s.get("stroke")
        if "gradient" in s:
            pt.setBrush(_gradient(s["gradient"]))
        elif fill is not None:
            pt.setBrush(QBrush(_qcolor(fill)))
        else:
            pt.setBrush(NO_BRUSH)
        if stroke is not None:
            pen = QPen(_qcolor(stroke), width)
            pen.setJoinStyle(JOIN_ROUND)
            pt.setPen(pen)
        else:
            pt.setPen(NO_PEN)
        if t == "rect":
            x, y, w, h = (float(s[k]) for k in ("x", "y", "width", "height"))
            r = float(s.get("radius", 0))
            if r > 0:
                pt.drawRoundedRect(QRectF(x, y, w, h), r, r)
            else:
                pt.drawRect(QRectF(x, y, w, h))
        elif t == "ellipse":
            pt.drawEllipse(QPointF(float(s["cx"]), float(s["cy"])), float(s["rx"]), float(s["ry"]))
        elif t == "polygon":
            pt.drawPolygon(QPolygonF([QPointF(a, b) for a, b in _points(s, minimum=3)]))
        else:
            raise BridgeError("unknown shape type %r (line, polyline, bezier, rect, ellipse, polygon, text)" % t)
    except KeyError as e:
        raise BridgeError("shape %r is missing field %s" % (t, e))
    finally:
        pt.restore()


@handler("draw.shapes")
def draw_shapes(p):
    doc = active_doc()
    shapes = p.get("shapes")
    if not isinstance(shapes, list) or not shapes:
        raise BridgeError("'shapes' must be a non-empty list")
    if len(shapes) > MAX_SHAPES:
        raise BridgeError("too many shapes (max %d per call)" % MAX_SHAPES)
    node = paint_layer(doc, p.get("layer"))
    W, H = doc.width(), doc.height()
    img = read_image(node, 0, 0, W, H)
    pt = QPainter(img)
    try:
        pt.setRenderHint(AA, True); pt.setRenderHint(AA_TEXT, True); pt.setRenderHint(AA_PIX, True)
        for s in shapes:
            _draw_shape(pt, s)
    finally:
        pt.end()
    write_image(node, img, 0, 0)
    _refresh(doc)
    return {"drawn": len(shapes), "layer": node_info(node)}


# -------------------------------------------------------------- selection
@handler("selection.set")
def selection_set(p):
    doc = active_doc()
    kind, mode = p.get("kind", "rect"), p.get("mode", "replace")
    if kind == "none":
        doc.setSelection(None)
        return {"selection": None}
    sel = Selection()
    if kind == "all":
        sel.select(0, 0, doc.width(), doc.height(), 255)
    elif kind == "rect":
        sel.select(_num(p, "x", None, integer=True), _num(p, "y", None, integer=True),
                   _num(p, "width", None, 1, MAX_DIM, True), _num(p, "height", None, 1, MAX_DIM, True), 255)
    elif kind in ("ellipse", "polygon"):
        mask = QImage(doc.width(), doc.height(), FMT_ALPHA8)
        mask.fill(TRANSPARENT)
        pt = QPainter(mask)
        try:
            pt.setRenderHint(AA, bool(p.get("antialias", True)))
            pt.setPen(NO_PEN)
            pt.setBrush(QBrush(QColor(0, 0, 0, 255)))
            if kind == "ellipse":
                pt.drawEllipse(QRectF(_num(p, "x", None), _num(p, "y", None),
                                      _num(p, "width", None, 1, MAX_DIM), _num(p, "height", None, 1, MAX_DIM)))
            else:
                pt.drawPolygon(QPolygonF([QPointF(a, b) for a, b in _points(p, minimum=3)]))
        finally:
            pt.end()
        sel = _mask_selection(mask, 0, 0)
    elif kind == "layer":  # everything the layer covers, weighted by its alpha
        n = paint_layer(doc, p.get("layer"), need_unlocked=False)
        raw = bytes(n.pixelData(0, 0, doc.width(), doc.height()))
        sel.setPixelData(QByteArray(raw[3::4]), 0, 0, doc.width(), doc.height())
    else:
        raise BridgeError("kind must be rect, ellipse, polygon, layer, all or none")
    cur = doc.selection()
    if cur is not None and mode != "replace":
        if mode not in ("add", "subtract", "intersect"):
            raise BridgeError("mode must be replace, add, subtract or intersect")
        getattr(cur, mode)(sel)
        sel = cur
    doc.setSelection(sel)
    return {"selection": [sel.x(), sel.y(), sel.width(), sel.height()]}


@handler("selection.modify")
def selection_modify(p):
    doc = active_doc()
    sel = doc.selection()
    if sel is None:
        raise BridgeError("There is no selection")
    op, amt = p.get("operation"), _num(p, "amount", 1, 0, 500, True)
    if op == "invert":
        sel.invert()
    elif op == "grow":
        sel.grow(amt, amt)
    elif op == "shrink":
        sel.shrink(amt, amt, False)
    elif op == "feather":
        sel.feather(amt)
    elif op == "border":
        sel.border(amt, amt)
    elif op == "smooth":
        sel.smooth()
    else:
        raise BridgeError("operation must be invert, grow, shrink, feather, border or smooth")
    doc.setSelection(sel)
    return {"selection": [sel.x(), sel.y(), sel.width(), sel.height()]}


@handler("selection.fill")
def selection_fill(p):
    """Fill the selection with a colour, or erase it when 'color' is omitted/null."""
    doc = active_doc()
    sel = doc.selection()
    if sel is None:
        raise BridgeError("There is no selection")
    node = paint_layer(doc, p.get("layer"))
    x, y, w, h = sel.x(), sel.y(), sel.width(), sel.height()
    if w <= 0 or h <= 0:
        raise BridgeError("selection is empty")
    mask = QImage(bytes(sel.pixelData(x, y, w, h)), w, h, w, FMT_ALPHA8).copy()
    layer_img = read_image(node, x, y, w, h)
    pt = QPainter(layer_img)
    try:
        if p.get("color") is None:
            pt.setCompositionMode(CM_OUT)
            pt.drawImage(0, 0, mask)
        else:
            col = QImage(w, h, FMT_ARGB32)
            col.fill(_qcolor(p["color"]))
            cp = QPainter(col)
            cp.setCompositionMode(CM_IN)
            cp.drawImage(0, 0, mask)
            cp.end()
            pt.drawImage(0, 0, col)
    finally:
        pt.end()
    write_image(node, layer_img, x, y)
    _refresh(doc)
    return {"filled": [x, y, w, h]}


# ---------------------------------------------------------------- filters
def _plain(v):
    return v if isinstance(v, (str, int, float, bool)) or v is None else str(v)


@handler("filter.list")
def filter_list(p):
    return {"filters": sorted(app().filters())}


@handler("filter.describe")
def filter_describe(p):
    f = app().filter(str(p.get("name")))
    if f is None:
        raise BridgeError("Unknown filter. Call filter_list.")
    props = f.configuration().properties()
    return {"name": f.name(), "default_properties": {k: _plain(v) for k, v in props.items()}}


@handler("filter.apply")
def filter_apply(p):
    doc = active_doc()
    f = _configured_filter(p.get("name"), p.get("params"))
    node = find_layer(doc, p.get("layer"))
    if node.type() not in ("paintlayer",):
        raise BridgeError("Filters apply to paint layers (use a filterlayer or filtermask for others)")
    if node.locked():
        raise BridgeError("Layer is locked")
    sel = doc.selection()
    if sel is not None and sel.width() > 0 and not p.get("ignore_selection"):
        x, y, w, h = sel.x(), sel.y(), sel.width(), sel.height()
    else:
        x, y, w, h = 0, 0, doc.width(), doc.height()
    f.apply(node, x, y, w, h)
    _refresh(doc)
    return node_info(node)


# ----------------------------------------------------------- brush engine
def _find_preset(name):
    presets = app().resources("preset")
    if name in presets:
        return presets[name]
    hits = [k for k in presets if name.lower() in k.lower()]
    if len(hits) == 1:
        return presets[hits[0]]
    if not hits:
        raise BridgeError("No brush preset matches %r; call list_brush_presets" % name)
    raise BridgeError("%r matches several presets: %s" % (name, ", ".join(sorted(hits)[:15])))


@handler("brush.presets")
def brush_presets(p):
    names = sorted(app().resources("preset"))
    q = str(p.get("filter") or "").lower()
    if q:
        names = [n for n in names if q in n.lower()]
    limit = _num(p, "limit", 200, 1, 5000, True)
    return {"count": len(names), "presets": names[:limit]}


def _polyline_path(pts):
    path = QPainterPath(QPointF(*pts[0]))
    for q in pts[1:]:
        path.lineTo(QPointF(*q))
    return path


def _smooth_path(pts):
    """Catmull-Rom spline through the points, as cubic Beziers."""
    if len(pts) < 3:
        return _polyline_path(pts)
    path = QPainterPath(QPointF(*pts[0]))
    ext = [pts[0]] + pts + [pts[-1]]
    for i in range(1, len(ext) - 2):
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        path.cubicTo(QPointF(x1 + (x2 - x0) / 6.0, y1 + (y2 - y0) / 6.0),
                     QPointF(x2 - (x3 - x1) / 6.0, y2 - (y3 - y1) / 6.0), QPointF(x2, y2))
    return path


@handler("brush.stroke")
def brush_stroke(p):
    """Paint with Krita's real brush engine (presets, texture, pressure) onto a paint layer.

    Uses the view's brush settings temporarily and restores the user's afterwards.
    """
    doc = active_doc()
    node = find_layer(doc, p.get("layer"))
    if node.locked():
        raise BridgeError("Layer %r is locked" % node.name())
    if node.paintAbility() != "PAINT":
        raise BridgeError("Layer %r (%s) cannot be painted on" % (node.name(), node.type()))
    raw = p.get("points")
    if not isinstance(raw, list) or len(raw) < 2 or len(raw) > 20000:
        raise BridgeError("'points' must be a list of 2..20000 [x, y] or [x, y, pressure] entries")
    try:
        pts = [(float(q[0]), float(q[1]), max(0.0, min(1.0, float(q[2]))) if len(q) > 2 else None)
               for q in raw]
    except (TypeError, ValueError, IndexError):
        raise BridgeError("each point must be [x, y] or [x, y, pressure 0..1]")
    closed, fill = bool(p.get("closed")), bool(p.get("fill"))
    view = _active_view(doc)
    doc.setActiveNode(node)
    saved = (view.currentBrushPreset(), view.brushSize(), view.foregroundColor(), view.paintingOpacity(),
             view.paintingFlow(), view.eraserMode())
    try:
        if p.get("preset"):
            view.setCurrentBrushPreset(_find_preset(str(p["preset"])))
        if p.get("size") is not None:
            view.setBrushSize(_num(p, "size", None, 0.5, 1000))
        if p.get("color") is not None:
            view.setForeGroundColor(ManagedColor.fromQColor(_qcolor(p["color"])))
        if p.get("opacity") is not None:
            view.setPaintingOpacity(_num(p, "opacity", None, 0, 1))
        if p.get("flow") is not None:
            view.setPaintingFlow(_num(p, "flow", None, 0, 1))
        view.setEraserMode(bool(p.get("eraser")))
        if any(q[2] is not None for q in pts) and not (closed or fill):
            # pressure needs per-segment dabs; each segment becomes its own undo step
            for a, b in zip(pts, pts[1:]):
                node.paintLine(QPoint(round(a[0]), round(a[1])), QPoint(round(b[0]), round(b[1])),
                               1.0 if a[2] is None else a[2], 1.0 if b[2] is None else b[2],
                               "ForegroundColor")
            strokes = len(pts) - 1
        else:
            xy = [(q[0], q[1]) for q in pts]
            path = (_smooth_path if p.get("smooth", True) else _polyline_path)(xy)
            if closed or fill:
                path.closeSubpath()
            node.paintPath(path, "ForegroundColor" if p.get("outline", True) else "None",
                           "ForegroundColor" if fill else "None")
            strokes = 1
    finally:
        preset, size, fg, opacity, flow, eraser = saved
        if preset is not None:
            view.setCurrentBrushPreset(preset)
        view.setBrushSize(size)
        if fg is not None:
            view.setForeGroundColor(fg)
        view.setPaintingOpacity(opacity)
        view.setPaintingFlow(flow)
        view.setEraserMode(eraser)
    _refresh(doc)
    return {"strokes": strokes, "layer": node_info(node)}


# ------------------------------------------------------------ undo / redo
def _history_view():
    """The History docker's list view (its model mirrors the active document's undo stack)."""
    for d in app().dockers():
        if d.objectName() == "History":
            for v in d.findChildren(QAbstractItemView):
                if v.model() is not None:
                    return v
    return None


def _history(limit=30):
    v = _history_view()
    if v is None:
        return None
    m = v.model()
    names = [str(m.data(m.index(i, 0))) for i in range(m.rowCount())]
    cur = v.selectionModel().currentIndex().row() if v.selectionModel() else len(names) - 1
    steps = [{"index": i, "name": n, "done": i <= cur} for i, n in enumerate(names) if i > 0]
    return steps[-limit:]


def _action_text(name):
    a = app().action(name)
    return a.text().replace("&", "") if a is not None else None


@handler("history.list")
def history_list(p):
    active_doc()
    steps = _history(_num(p, "limit", 30, 1, 500, True))
    if steps is None:
        raise BridgeError("Krita's History docker is not available")
    return {"steps": steps, "next_undo": _action_text("edit_undo"), "next_redo": _action_text("edit_redo")}


def _replay(action, steps):
    _active_view(active_doc())  # undo works on the document in the active view
    done = []
    for _ in range(steps):
        a = app().action(action)
        if a is None or not a.isEnabled():
            break
        done.append(a.text().replace("&", ""))
        a.trigger()
    _refresh(active_doc())
    return done


@handler("history.undo")
def history_undo(p):
    return {"undone": _replay("edit_undo", _num(p, "steps", 1, 1, 50, True))}


@handler("history.redo")
def history_redo(p):
    return {"redone": _replay("edit_redo", _num(p, "steps", 1, 1, 50, True))}
