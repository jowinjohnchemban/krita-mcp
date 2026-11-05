"""Exercise handlers.py against a *fake* `krita` module using real Qt.

This validates the Qt side (BGRA pixel layout, Qt5/Qt6 enum handling, QPainter drawing,
compositing, base64 import) but NOT the real Krita API - that needs a manual smoke test.
"""
import base64
import os
import sys
import types
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.environ.get("KRITA_TEST_QT") == "5":
    sys.modules["PyQt6"] = None  # force the Krita-5 code path
    pytest.importorskip("PyQt5.QtGui", reason="PyQt5 not installed")
    from PyQt5.QtCore import QBuffer, QIODevice, QRect  # noqa: E402
    from PyQt5.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402
else:
    pytest.importorskip("PyQt6.QtGui", reason="PyQt6 not installed")
    from PyQt6.QtCore import QBuffer, QIODevice, QRect  # noqa: E402
    from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402


def _e(owner, group, member):
    g = getattr(owner, group, None)
    return getattr(g, member) if g is not None and hasattr(g, member) else getattr(owner, member)


ARGB32 = _e(QImage, "Format", "Format_ARGB32")
WRITE_ONLY = _e(QIODevice, "OpenModeFlag", "WriteOnly")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "pykrita"))


_QAPP = None


class FakeNode:
    def __init__(self, name, kind, w, h):
        self._name, self._kind, self.w, self.h = name, kind, w, h
        self.buf = bytearray(w * h * 4)  # BGRA, like Krita RGBA/U8
        self._id = uuid.uuid4()
        self.children, self._parent = [], None
        self._vis, self._lock, self._op = True, False, 255
        self._alpha_lock, self._clip = False, False
        self.pos, self.content = (0, 0), (0, 0, w, h)

    def uniqueId(self): return types.SimpleNamespace(toString=lambda: "{%s}" % self._id)
    def name(self): return self._name
    def setName(self, n): self._name = n
    def type(self): return self._kind
    def colorModel(self): return "RGBA"
    def colorDepth(self): return "U8"
    def visible(self): return self._vis
    def setVisible(self, v): self._vis = v
    def locked(self): return self._lock
    def setLocked(self, v): self._lock = v
    def opacity(self): return self._op
    def setOpacity(self, v): self._op = v
    def blendingMode(self): return "normal"
    def setBlendingMode(self, m): pass
    def alphaLocked(self): return self._alpha_lock
    def setAlphaLocked(self, v): self._alpha_lock = v
    def inheritAlpha(self): return self._clip
    def setInheritAlpha(self, v): self._clip = v
    def position(self): return types.SimpleNamespace(x=lambda: self.pos[0], y=lambda: self.pos[1])
    def move(self, x, y): self.pos = (x, y)

    def bounds(self):  # content box (layer-local) shifted by the layer offset, like Krita
        cx, cy, cw, ch = self.content
        return QRect(cx + self.pos[0], cy + self.pos[1], cw, ch)

    def projectionPixelData(self, x, y, w, h): return self.pixelData(x, y, w, h)
    def colorLabel(self): return 0
    def setColorLabel(self, v): pass

    def childNodes(self): return list(self.children)

    def addChildNode(self, n, above):  # like Krita: above=None puts the node on top
        self.children.insert(len(self.children) if above is None else self.children.index(above) + 1, n)
        n._parent = self
        return True

    def remove(self):
        self._parent.children.remove(self)
        self._parent = None
        return True

    def parentNode(self): return self._parent

    def pixelData(self, x, y, w, h):
        out = bytearray()
        for row in range(y, y + h):
            out += self.buf[(row * self.w + x) * 4:(row * self.w + x + w) * 4]
        return bytes(out)

    def setPixelData(self, data, x, y, w, h):
        data = bytes(data)
        for r in range(h):
            for c in range(w):
                tx, ty = x + c, y + r
                if 0 <= tx < self.w and 0 <= ty < self.h:
                    o = (ty * self.w + tx) * 4
                    self.buf[o:o + 4] = data[(r * w + c) * 4:(r * w + c) * 4 + 4]


class FakeDoc:
    def __init__(self, w, h, name):
        self._w, self._h, self._name = w, h, name
        self.root = FakeNode("root", "grouplayer", w, h)
        self._active, self._sel = None, None

    def width(self): return self._w
    def height(self): return self._h
    def name(self): return self._name
    def fileName(self): return ""
    def xRes(self): return 72.0
    def yRes(self): return 72.0
    def colorModel(self): return "RGBA"
    def colorDepth(self): return "U8"
    def modified(self): return False
    def rootNode(self): return self.root
    def createNode(self, name, kind): return FakeNode(name, kind, self._w, self._h)
    def activeNode(self): return self._active
    def setActiveNode(self, n): self._active = n
    def refreshProjection(self): pass
    def waitForDone(self): pass
    def selection(self): return self._sel
    def setSelection(self, s): self._sel = s
    def setBatchmode(self, v): pass

    def projection(self, x, y, w, h):  # composite = just stack nodes bottom->top
        out = QImage(w, h, ARGB32)
        out.fill(QColor(0, 0, 0, 0))
        p = QPainter(out)
        for n in self.root.children:
            img = QImage(bytes(n.pixelData(x, y, w, h)), w, h, w * 4, ARGB32)
            p.drawImage(0, 0, img)
        p.end()
        return out

    def pixelData(self, x, y, w, h):  # composite as BGRA bytes, like Document.pixelData
        img = self.projection(x, y, w, h)
        return img.constBits().asstring(img.sizeInBytes())


class FakeSelection:
    """Alpha8 mask over a 1000x1000 area; enough for bounding-box checks."""
    N = 1000

    def __init__(self): self.mask = bytearray(self.N * self.N)
    def duplicate(self):
        d = FakeSelection()
        d.mask[:] = self.mask
        return d
    def pixelData(self, x, y, w, h):
        return b"".join(bytes(self.mask[(y + r) * self.N + x:(y + r) * self.N + x + w]) for r in range(h))
    def select(self, x, y, w, h, v):
        for r in range(y, y + h):
            self.mask[r * self.N + x:r * self.N + x + w] = bytes([v]) * w
    def setPixelData(self, data, x, y, w, h):
        data = bytes(data)
        assert len(data) == w * h, "selection data must be tightly packed (w*h bytes)"
        for r in range(h):
            self.mask[(y + r) * self.N + x:(y + r) * self.N + x + w] = data[r * w:(r + 1) * w]

    def _box(self):
        pts = [(i % self.N, i // self.N) for i, v in enumerate(self.mask) if v]
        if not pts:
            return 0, 0, 0, 0
        xs, ys = [q[0] for q in pts], [q[1] for q in pts]
        return min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
    def x(self): return self._box()[0]
    def y(self): return self._box()[1]
    def width(self): return self._box()[2]
    def height(self): return self._box()[3]


class FakeKrita:
    _inst = None
    def __init__(self): self.doc = None
    @classmethod
    def instance(cls):
        cls._inst = cls._inst or FakeKrita()
        return cls._inst
    def createDocument(self, w, h, name, *a): return FakeDoc(w, h, name)
    def activeWindow(self): return None
    def setActiveDocument(self, d): self.doc = d
    def activeDocument(self): return self.doc
    def documents(self): return [self.doc] if self.doc else []
    def version(self): return "fake"
    def dockers(self): return []  # no History docker: Krita-side steps count as 0
    def addExtension(self, ext): self.ext = ext


@pytest.fixture(scope="module")
def H(tmp_path_factory):
    os.environ["KRITA_MCP_WORKSPACE"] = str(tmp_path_factory.mktemp("ws"))
    mod = types.ModuleType("krita")
    mod.Krita, mod.InfoObject, mod.Selection, mod.ManagedColor = FakeKrita, object, FakeSelection, object
    mod.Extension = type('Extension', (), {'__init__': lambda self, parent=None: None})
    sys.modules["krita"] = mod
    global _QAPP
    _QAPP = QGuiApplication.instance() or QGuiApplication([])  # must stay referenced; text needs it
    sys.modules.pop("krita_mcp_bridge", None)
    import importlib
    return importlib.import_module("krita_mcp_bridge.handlers")


def px(doc, x, y):
    return doc.projection(x, y, 1, 1).pixelColor(0, 0).getRgb()


def test_new_document_is_white_and_has_background_layer(H):
    info = H.dispatch("document.new", {"width": 64, "height": 48, "background": "white"})
    assert (info["width"], info["height"]) == (64, 48)
    doc = H.app().activeDocument()
    assert px(doc, 5, 5) == (255, 255, 255, 255)


def test_draw_shapes_pixel_values(H):
    H.dispatch("document.new", {"width": 100, "height": 100, "background": "white"})
    H.dispatch("layer.create", {"name": "art"})
    shapes = [
        {"type": "rect", "x": 10, "y": 10, "width": 30, "height": 30, "fill": "#ff0000"},
        {"type": "ellipse", "cx": 70, "cy": 70, "rx": 15, "ry": 15, "fill": "#0000ff"},
        {"type": "rect", "x": 0, "y": 80, "width": 100, "height": 20,
         "gradient": {"type": "linear", "x1": 0, "y1": 0, "x2": 100, "y2": 0, "stops": [[0, "#000000"], [1, "#ffffff"]]}},
        {"type": "line", "x1": 0, "y1": 50, "x2": 100, "y2": 50, "color": "#00ff00", "stroke_width": 4},
        {"type": "bezier", "points": [[0, 0], [10, 0], [20, 10], [30, 10]], "color": "#123456"},
        {"type": "polygon", "points": [[50, 5], [60, 5], [55, 15]], "fill": "#ffff00"},
        {"type": "text", "x": 5, "y": 98, "text": "Hi", "size": 12, "color": "#000000"},
    ]
    assert H.dispatch("draw.shapes", {"shapes": shapes})["drawn"] == len(shapes)
    doc = H.app().activeDocument()
    assert px(doc, 20, 20) == (255, 0, 0, 255)       # red rect: proves BGRA <-> ARGB32 mapping
    assert px(doc, 70, 70) == (0, 0, 255, 255)       # blue ellipse
    assert px(doc, 55, 8)[:3] == (255, 255, 0)       # polygon
    assert px(doc, 50, 50)[1] > 200 and px(doc, 50, 50)[0] < 50  # green line
    left, right = px(doc, 3, 90), px(doc, 96, 90)
    assert left[0] < 40 and right[0] > 215           # gradient direction
    assert px(doc, 95, 20) == (255, 255, 255, 255)   # untouched area shows white background


def test_semi_transparent_shape_blends(H):
    H.dispatch("document.new", {"width": 20, "height": 20, "background": "white"})
    H.dispatch("layer.create", {"name": "ov"})
    H.dispatch("draw.shapes", {"shapes": [{"type": "rect", "x": 0, "y": 0, "width": 20, "height": 20,
                                           "fill": "#000000", "opacity": 0.5}]})
    r, g, b, a = px(H.app().activeDocument(), 10, 10)
    assert 110 <= r <= 145 and a == 255


def test_shape_validation(H):
    for bad in ([{"type": "nope"}], [{"type": "rect", "x": 1}], [{"type": "bezier", "points": [[0, 0]] * 5}],
                [], "x", [{"type": "rect", "x": 0, "y": 0, "width": 1, "height": 1, "fill": "#zzzzzz"}]):
        with pytest.raises(H.BridgeError):
            H.dispatch("draw.shapes", {"shapes": bad})
    with pytest.raises(H.BridgeError):
        H.dispatch("draw.shapes", {"shapes": [{"type": "line", "x1": 0, "y1": 0, "x2": 1, "y2": 1}] * 5001})


def test_import_image_base64_and_fit(H):
    H.dispatch("document.new", {"width": 100, "height": 100, "background": "white"})
    src = QImage(40, 20, ARGB32)
    src.fill(QColor(255, 0, 0))
    buf = QBuffer(); buf.open(WRITE_ONLY); src.save(buf, "PNG")
    b64 = base64.b64encode(bytes(buf.data())).decode()
    info = H.dispatch("layer.import_image", {"data_base64": b64, "name": "gen"})  # default: contain, centred
    assert info["name"] == "gen"
    doc = H.app().activeDocument()
    assert px(doc, 50, 50) == (255, 0, 0, 255)       # centre is covered
    assert px(doc, 50, 10) == (255, 255, 255, 255)   # 2:1 image letterboxed -> top stays white
    with pytest.raises(H.BridgeError):
        H.dispatch("layer.import_image", {"data_base64": base64.b64encode(b"not an image").decode()})
    with pytest.raises(H.BridgeError):
        H.dispatch("layer.import_image", {"path": "../../etc/passwd"})


def test_canvas_snapshot_returns_valid_png(H):
    H.dispatch("document.new", {"width": 300, "height": 200, "background": "#336699"})
    r = H.dispatch("canvas.snapshot", {"max_size": 100})
    img = QImage.fromData(base64.b64decode(r["png_base64"]))
    assert not img.isNull() and max(img.width(), img.height()) == 100
    assert img.pixelColor(10, 10).getRgb()[:3] == (0x33, 0x66, 0x99)


def test_layer_ops(H):
    H.dispatch("document.new", {"width": 10, "height": 10})
    a = H.dispatch("layer.create", {"name": "A"})
    H.dispatch("layer.update", {"layer": "A", "opacity": 0.5, "visible": False, "name": "A2"})
    tree = H.dispatch("layer.list", {})["layers"]
    assert [l["name"] for l in tree][-1] == "A2" and abs(tree[-1]["opacity"] - 0.5) < 0.01 and tree[-1]["visible"] is False
    with pytest.raises(H.BridgeError):
        H.dispatch("layer.update", {"layer": "does-not-exist"})
    with pytest.raises(H.BridgeError):
        H.dispatch("layer.create", {"type": "evil"})
    with pytest.raises(H.BridgeError):
        H.dispatch("document.new", {"width": 99999})
    with pytest.raises(H.BridgeError):
        H.dispatch("document.export", {"path": "/tmp/outside.png"})


def test_sample_color(H):
    H.dispatch("document.new", {"width": 20, "height": 20, "background": "#336699"})
    assert H.dispatch("canvas.sample", {"x": 3, "y": 4})["rgba"] == [0x33, 0x66, 0x99, 255]
    H.dispatch("layer.create", {"name": "top"})
    r = H.dispatch("canvas.sample", {"x": 3, "y": 4, "layer": "top"})
    assert r["rgba"][3] == 0 and r["hex"] == "#00000000"
    with pytest.raises(H.BridgeError):
        H.dispatch("canvas.sample", {"x": 20, "y": 0})


def test_move_targets_content_not_offset(H):
    H.dispatch("document.new", {"width": 50, "height": 50})
    H.dispatch("layer.create", {"name": "m"})
    n = H.find_layer(H.active_doc(), "m")
    n.content = (8, 8, 10, 10)  # content does not start at the layer origin
    assert H.dispatch("layer.update", {"layer": "m", "x": 28, "y": 18})["bounds"][:2] == [28, 18]
    assert H.dispatch("layer.update", {"layer": "m", "dx": -5, "dy": 2})["bounds"][:2] == [23, 20]
    n.content = (0, 0, 0, 0)  # empty layer: x/y set the offset
    H.dispatch("layer.update", {"layer": "m", "x": 3, "y": 4})
    assert n.pos == (3, 4)


def test_ellipse_and_polygon_selection(H):
    H.dispatch("document.new", {"width": 37, "height": 23})  # width not a multiple of 4: row padding
    r = H.dispatch("selection.set", {"kind": "ellipse", "x": 5, "y": 3, "width": 20, "height": 10})
    x, y, w, h = r["selection"]
    assert abs(x - 5) <= 1 and abs(y - 3) <= 1 and abs(w - 20) <= 2 and abs(h - 10) <= 2
    r = H.dispatch("selection.set", {"kind": "polygon", "points": [[1, 1], [30, 1], [15, 20]],
                                     "antialias": False})
    x, y, w, h = r["selection"]
    assert (x, y) == (1, 1) and abs(w - 29) <= 1 and abs(h - 19) <= 1
    with pytest.raises(H.BridgeError):
        H.dispatch("selection.set", {"kind": "polygon", "points": [[0, 0], [1, 1]]})


def test_arrange_layers(H):
    H.dispatch("document.new", {"width": 10, "height": 10})
    for nm in ("A", "B", "C"):
        H.dispatch("layer.create", {"name": nm})
    order = lambda: [l["name"] for l in H.dispatch("layer.list", {})["layers"]]  # noqa: E731
    assert order() == ["Background", "A", "B", "C"]
    assert H.dispatch("layer.arrange", {"layer": "C", "to": "bottom"})["order"] == ["C", "Background", "A", "B"]
    H.dispatch("layer.arrange", {"layer": "C", "to": "up"})
    assert order() == ["Background", "C", "A", "B"]
    H.dispatch("layer.arrange", {"layer": "C", "to": "top"})
    assert order() == ["Background", "A", "B", "C"]
    H.dispatch("layer.arrange", {"layer": "C", "to": "down"})
    assert order() == ["Background", "A", "C", "B"]
    H.dispatch("layer.arrange", {"layer": "B", "above": "Background"})
    assert order() == ["Background", "B", "A", "C"]
    H.dispatch("layer.create", {"name": "G", "type": "grouplayer"})
    H.dispatch("layer.arrange", {"layer": "A", "parent": "G"})
    tree = H.dispatch("layer.list", {})["layers"]
    assert [c["name"] for c in tree[-1]["children"]] == ["A"]
    with pytest.raises(H.BridgeError):
        H.dispatch("layer.arrange", {"layer": "G", "parent": "G"})


def test_smooth_path_passes_through_points(H):
    pts = [(0, 0), (10, 5), (20, 0), (30, 8)]
    path = H._smooth_path(pts)
    ends = [(path.elementAt(i).x, path.elementAt(i).y) for i in range(0, path.elementCount(), 3)]
    assert ends == pts


# ------------------------------------------------------------------ undo / redo
def test_undo_redo_pixel_edits(H):
    H.dispatch("document.new", {"width": 30, "height": 30, "background": "white"})
    H.dispatch("layer.create", {"name": "ink"})
    rect = {"type": "rect", "x": 0, "y": 0, "width": 10, "height": 10, "fill": "#ff0000"}
    H.dispatch("draw.shapes", {"shapes": [rect], "layer": "ink"})
    H.dispatch("draw.shapes", {"shapes": [dict(rect, x=15, fill="#0000ff")], "layer": "ink"})
    doc = H.app().activeDocument()
    assert px(doc, 20, 5) == (0, 0, 255, 255)
    r = H.dispatch("history.undo", {})
    assert r["undone"][0]["command"] == "draw.shapes"
    assert px(doc, 20, 5) == (255, 255, 255, 255) and px(doc, 5, 5) == (255, 0, 0, 255)
    H.dispatch("history.redo", {})
    assert px(doc, 20, 5) == (0, 0, 255, 255)
    H.dispatch("history.undo", {"steps": 2})
    assert px(doc, 5, 5) == (255, 255, 255, 255)
    H.dispatch("history.redo", {"steps": 5})  # only 2 available
    assert px(doc, 5, 5) == (255, 0, 0, 255) and px(doc, 20, 5) == (0, 0, 255, 255)


def test_undo_props_move_and_selection(H):
    H.dispatch("document.new", {"width": 30, "height": 30})
    H.dispatch("layer.create", {"name": "p"})
    H.dispatch("layer.update", {"layer": "p", "opacity": 0.25, "name": "q", "dx": 4})
    n = H.find_layer(H.active_doc(), "q")
    H.dispatch("history.undo", {})
    assert n.name() == "p" and n.opacity() == 255 and n.pos == (0, 0)
    H.dispatch("selection.set", {"kind": "rect", "x": 1, "y": 1, "width": 5, "height": 5})
    H.dispatch("selection.set", {"kind": "rect", "x": 10, "y": 10, "width": 5, "height": 5})
    H.dispatch("history.undo", {})
    sel = H.active_doc().selection()
    assert (sel.x(), sel.y()) == (1, 1)
    with pytest.raises(H.BridgeError):
        H.dispatch("document.new", {"width": 5, "height": 5}) and H.dispatch("history.undo", {})


def test_new_edit_clears_redo(H):
    H.dispatch("document.new", {"width": 10, "height": 10, "background": "white"})
    H.dispatch("draw.shapes", {"shapes": [{"type": "rect", "x": 0, "y": 0, "width": 3, "height": 3}]})
    H.dispatch("history.undo", {})
    H.dispatch("draw.shapes", {"shapes": [{"type": "rect", "x": 5, "y": 5, "width": 3, "height": 3}]})
    with pytest.raises(H.BridgeError):
        H.dispatch("history.redo", {})


# ------------------------------------------------------- reading / generative
def _doc_with_shapes(H):
    H.dispatch("document.new", {"width": 60, "height": 40, "background": "#ffffff"})
    H.dispatch("layer.create", {"name": "art"})
    H.dispatch("draw.shapes", {"shapes": [
        {"type": "rect", "x": 0, "y": 0, "width": 20, "height": 20, "fill": "#ff0000"},
        {"type": "rect", "x": 40, "y": 20, "width": 20, "height": 20, "fill": "#ff0000"},
        {"type": "rect", "x": 25, "y": 5, "width": 10, "height": 10, "fill": "#0000ff"}]})


def test_analyze_and_read_pixels(H):
    _doc_with_shapes(H)
    a = H.dispatch("image.analyze", {"grid": 2})
    hexes = [c["hex"] for c in a["palette"]]
    assert hexes[0] == "#ffffff" and "#ff0000" in hexes and "#0000ff" in hexes
    assert a["layout"][0][0]["hex"] != a["layout"][0][1]["hex"]
    assert a["coverage"] == 1.0 and 0 < a["contrast"] < 1
    r = H.dispatch("image.read_pixels", {"x": 18, "y": 0, "width": 4, "height": 1})
    assert r["rows"] == [["#ff0000", "#ff0000", "#ffffff", "#ffffff"]]
    with pytest.raises(H.BridgeError):
        H.dispatch("image.read_pixels", {"width": 40, "height": 40})
    lay = H.dispatch("image.analyze", {"layer": "art"})
    assert lay["content_bounds"] == [0, 0, 60, 40] and lay["coverage"] < 1


def test_find_color(H):
    _doc_with_shapes(H)
    r = H.dispatch("image.find_color", {"color": "#ff0000", "tolerance": 10})
    assert r["regions_found"] == 2
    assert sorted(m["bbox"] for m in r["matches"]) == [[0, 0, 20, 20], [40, 20, 20, 20]]


def test_color_and_magic_wand_selection(H):
    _doc_with_shapes(H)
    r = H.dispatch("selection.set", {"kind": "color", "color": "#ff0000", "tolerance": 5})
    assert r["selection"] == [0, 0, 60, 40]  # both red squares
    r = H.dispatch("selection.set", {"kind": "color", "x": 45, "y": 25, "contiguous": True, "tolerance": 5})
    assert r["selection"] == [40, 20, 20, 20]  # only the square under the seed
    with pytest.raises(H.BridgeError):
        H.dispatch("selection.set", {"kind": "color", "color": "#00ff00", "tolerance": 0})


def test_import_into_selection_and_mask_export(H, tmp_path):
    H.dispatch("document.new", {"width": 40, "height": 40, "background": "white"})
    H.dispatch("selection.set", {"kind": "rect", "x": 10, "y": 10, "width": 10, "height": 10})
    patch = QImage(5, 5, ARGB32)
    patch.fill(QColor(0, 255, 0))
    buf = QBuffer(); buf.open(WRITE_ONLY); patch.save(buf, "PNG")
    info = H.dispatch("layer.import_image", {"data_base64": base64.b64encode(bytes(buf.data())).decode(),
                                             "fit": "selection"})
    assert info["name"] == "Inpainted"
    doc = H.app().activeDocument()
    assert px(doc, 15, 15) == (0, 255, 0, 255) and px(doc, 25, 25) == (255, 255, 255, 255)
    out = H.dispatch("selection.export_mask", {"path": "mask.png"})
    m = QImage(out["path"])
    assert m.pixelColor(15, 15).red() == 255 and m.pixelColor(2, 2).red() == 0
    r = H.dispatch("document.export", {"path": "crop.png", "x": 10, "y": 10, "width": 10, "height": 10})
    c = QImage(r["path"])
    assert (c.width(), c.height()) == (10, 10) and c.pixelColor(5, 5).getRgb() == (0, 255, 0, 255)


def test_snapshot_grid_overlay(H):
    H.dispatch("document.new", {"width": 200, "height": 100, "background": "white"})
    r = H.dispatch("canvas.snapshot", {"grid": 50})
    img = QImage.fromData(base64.b64decode(r["png_base64"]))
    assert img.pixelColor(50, 60).getRgb() != (255, 255, 255, 255)  # vertical line at x=50
    assert img.pixelColor(30, 30).getRgb() == (255, 255, 255, 255)
