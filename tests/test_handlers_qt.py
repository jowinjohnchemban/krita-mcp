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

    def duplicate(self):
        d = FakeNode(self._name, self._kind, self.w, self.h)
        d.buf[:] = self.buf
        d._vis, d._op = self._vis, self._op
        return d

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

    def createTransparencyMask(self, name):
        m = FakeNode(name, "transparencymask", self._w, self._h)
        m.setSelection = lambda sel: setattr(m, "sel", sel)
        return m

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

    def feather(self, n): pass

    def add(self, other):
        self.mask[:] = bytes(max(a, b) for a, b in zip(self.mask, other.mask))

    def subtract(self, other):
        self.mask[:] = bytes(max(0, a - b) for a, b in zip(self.mask, other.mask))

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
    mod.DockWidget = mod.DockWidgetFactory = type("Dock", (), {})
    mod.DockWidgetFactoryBase = types.SimpleNamespace(DockRight=1)
    sys.modules["krita"] = mod
    global _QAPP
    _QAPP = QGuiApplication.instance() or QGuiApplication([])  # must stay referenced; text needs it
    for name in [m for m in sys.modules if m.startswith("krita_mcp_bridge")]:  # import fresh, with this fake
        sys.modules.pop(name)
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
    H.dispatch("layer.create", {"name": "A"})
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


# ------------------------------------------------------- photo tools / access / docker
def test_adjust_levels_and_curves_are_exact(H):
    H.dispatch("document.new", {"width": 20, "height": 10, "background": "#808080"})
    H.dispatch("photo.adjust", {"operation": "levels", "layer": "Background", "in_black": 128, "in_white": 255})
    assert px(H.app().activeDocument(), 5, 5)[:3] == (0, 0, 0)
    H.dispatch("history.undo", {})
    H.dispatch("photo.adjust", {"operation": "curves", "points": [[0, 0], [128, 200], [255, 255]], "channel": "r"})
    assert px(H.app().activeDocument(), 5, 5)[:3] == (200, 128, 128)
    with pytest.raises(H.BridgeError):
        H.dispatch("photo.adjust", {"operation": "nope"})


def test_adjust_respects_selection(H):
    H.dispatch("document.new", {"width": 20, "height": 10, "background": "#808080"})
    H.dispatch("selection.set", {"kind": "rect", "x": 0, "y": 0, "width": 10, "height": 10})
    H.dispatch("photo.adjust", {"operation": "exposure", "stops": 1, "layer": "Background"})
    doc = H.app().activeDocument()
    assert px(doc, 5, 5)[0] == 255 and px(doc, 15, 5)[0] == 128


def test_access_modes_and_activity(H):
    H.dispatch("document.new", {"width": 10, "height": 10})
    try:
        H.ACCESS["mode"] = "read_only"
        assert H.dispatch("layer.list", {})["layers"]
        with pytest.raises(H.BridgeError, match="read-only"):
            H.dispatch("layer.create", {"name": "x"})
        H.ACCESS["mode"] = "paused"
        with pytest.raises(H.BridgeError, match="paused"):
            H.dispatch("layer.list", {})
    finally:
        H.ACCESS["mode"] = "full"
    t, method, ok, ms, detail = H.ACTIVITY[-1]
    assert method == "layer.list" and ok is False and "paused" in detail
    assert any(m == "layer.create" and not o for _, m, o, _, _ in H.ACTIVITY)


def test_docker_client_snippets(H):
    import importlib
    dock = importlib.import_module("krita_mcp_bridge.dock")
    assert dock.client_snippet("Claude Code", "krita-mcp", 5679) == "claude mcp add krita -- krita-mcp"
    assert "-e KRITA_MCP_PORT=6000" in dock.client_snippet("Claude Code", "krita-mcp", 6000)
    js = dock.client_snippet("Claude Desktop", '"C:/My Tools/krita-mcp.exe" --x', 5679)
    assert '"command": "C:/My Tools/krita-mcp.exe"' in js and '"--x"' in js
    assert "[mcp_servers.krita]" in dock.client_snippet("Codex CLI", "uvx krita-mcp", 5679)
    assert '"type": "stdio"' in dock.client_snippet("VS Code (Copilot)", "krita-mcp", 5679)


# ------------------------------------------------------------ generative round trips
def _png_b64(w, h, color):
    img = QImage(w, h, ARGB32)
    img.fill(QColor(color))
    buf = QBuffer(); buf.open(WRITE_ONLY); img.save(buf, "PNG")
    return base64.b64encode(bytes(buf.data())).decode()


def _decode(b64):
    img = QImage.fromData(base64.b64decode(b64))
    assert not img.isNull()
    return img


def test_gen_context_region_mask_and_sizes(H):
    H.dispatch("document.new", {"width": 100, "height": 80, "background": "white"})
    doc = H.active_doc()
    sel = FakeSelection(); sel.select(10, 10, 20, 20, 255); doc.setSelection(sel)
    r = H.dispatch("gen.context", {"padding": 5, "target": 64, "multiple": 8, "need_mask": True, "mask_alpha": True})
    assert r["region"] == [5, 5, 30, 30] and r["size"] == [64, 64] and r["has_selection"]
    mask = _decode(r["mask_png"])
    assert mask.pixelColor(32, 32).red() == 255 and mask.pixelColor(1, 1).red() == 0
    hole = _decode(r["mask_alpha_png"])
    assert hole.pixelColor(32, 32).alpha() == 0 and hole.pixelColor(1, 1).alpha() == 255
    r = H.dispatch("gen.context", {"region": "canvas", "sizes": ["1024x1024", "1536x1024", "1024x1536"]})
    assert r["region"] == [0, 0, 100, 80] and r["size"] == [1536, 1024]
    doc.setSelection(None)
    assert H.dispatch("gen.context", {"need_image": False})["has_selection"] is False


def test_gen_place_variants_sheet_and_pick(H):
    H.dispatch("document.new", {"width": 40, "height": 30, "background": "white"})
    r = H.dispatch("gen.place", {"images": [_png_b64(8, 6, "#ff0000"), _png_b64(8, 6, "#0000ff")],
                                 "region": [0, 0, 40, 30], "name": "gen", "labels": ["seed 1", "seed 2"],
                                 "sheet": True})
    assert r["group"]["name"] == "gen" and [l["name"] for l in r["layers"]] == ["Variant 1 seed 1", "Variant 2 seed 2"]
    assert [l["visible"] for l in r["layers"]] == [True, False]
    sheet = _decode(r["sheet_png"])
    assert sheet.width() > sheet.height()  # two cells side by side
    layer2 = H.find_layer(H.active_doc(), "Variant 2 seed 2")
    assert layer2.pixelData(20, 15, 1, 1) == bytes([255, 0, 0, 255])  # BGRA blue, stretched over the region
    assert H.dispatch("gen.pick", {"group": "gen", "index": 2})["visible"]["name"] == "Variant 2 seed 2"
    kept = H.dispatch("gen.pick", {"group": "gen", "index": 1, "keep_only": True})["kept"]
    names = [l["name"] for l in H.dispatch("layer.list", {})["layers"]]
    assert kept["name"] == "gen" and "Variant 2 seed 2" not in str(names)
    with pytest.raises(H.BridgeError):
        H.dispatch("gen.place", {"images": []})


def test_gen_place_is_cut_to_the_selection(H):
    H.dispatch("document.new", {"width": 40, "height": 40, "background": "white"})
    doc = H.active_doc()
    sel = FakeSelection(); sel.select(10, 10, 10, 10, 255); doc.setSelection(sel)
    r = H.dispatch("gen.place", {"images": [_png_b64(4, 4, "#00ff00")], "region": [0, 0, 40, 40], "mask": True})
    node = H.find_layer(doc, r["layers"][0]["id"])
    assert node.pixelData(15, 15, 1, 1)[3] == 255 and node.pixelData(2, 2, 1, 1)[3] == 0


def test_remove_background_color_method(H):
    H.dispatch("document.new", {"width": 60, "height": 60, "background": "transparent"})
    H.dispatch("layer.create", {"name": "photo", "fill": "#f0f0f0"})
    H.dispatch("draw.shapes", {"layer": "photo", "shapes": [
        {"type": "rect", "x": 20, "y": 20, "width": 20, "height": 20, "fill": "#cc2020"}]})
    r = H.dispatch("image.remove_background", {"layer": "photo", "mode": "erase", "feather": 0})
    node = H.find_layer(H.active_doc(), "photo")
    assert node.pixelData(5, 5, 1, 1)[3] == 0 and node.pixelData(30, 30, 1, 1) == bytes([0x20, 0x20, 0xcc, 255])
    assert r["background_color"] == "#f0f0f0" and 0.8 < r["background_fraction"] < 0.92
    r = H.dispatch("image.remove_background", {"layer": "photo", "mode": "mask"})
    assert r["mask"]["type"] == "transparencymask"


def test_match_colors_transfers_mean(H):
    H.dispatch("document.new", {"width": 20, "height": 20, "background": "transparent"})
    H.dispatch("layer.create", {"name": "ref", "fill": "#c8c8c8"})
    H.dispatch("layer.create", {"name": "elem", "fill": "#646464"})
    r = H.dispatch("photo.match_colors", {"layer": "elem", "reference_layer": "ref"})
    assert r["before"]["mean"][0] == 100 and r["reference"]["mean"][0] == 200
    assert H.find_layer(H.active_doc(), "elem").pixelData(5, 5, 1, 1)[:3] == bytes([200, 200, 200])


def test_gen_context_renders_control_guides(H):
    H.dispatch("document.new", {"width": 40, "height": 20, "background": "white"})
    H.dispatch("layer.create", {"name": "Sketch"})
    H.dispatch("draw.shapes", {"layer": "Sketch", "shapes": [
        {"type": "rect", "x": 0, "y": 0, "width": 20, "height": 20, "fill": "#000000"}]})
    r = H.dispatch("gen.context", {"region": "canvas", "need_image": False, "target": 80, "multiple": 8,
                                   "controls": [{"layer": "Sketch"}, {"layer": "Sketch", "invert": True}]})
    plain, inverted = (_decode(b) for b in r["control_pngs"])
    assert (plain.width(), plain.height()) == (80, 40) == tuple(r["size"])
    assert plain.pixelColor(10, 20).getRgb()[:3] == (0, 0, 0) and plain.pixelColor(70, 20).getRgb()[:3] == (255, 255, 255)
    assert inverted.pixelColor(10, 20).getRgb()[:3] == (255, 255, 255)
    with pytest.raises(H.BridgeError):
        H.dispatch("gen.context", {"controls": [{}] * 5})


def test_camera_moves_cover_the_frame(H):
    from krita_mcp_bridge import genai
    for move in genai.CAMERA_MOVES:
        for t in (0.0, 0.25, 0.5, 0.75, 1.0):
            s, dx, dy, ang = genai.camera_at(move, t)
            # a picture scaled by s and shifted by dx still covers the frame when s >= 1 + 2|shift|
            assert s >= 1 + 2 * max(abs(dx), abs(dy)) - 1e-9 or abs(ang) > 0, (move, t, s, dx, dy)
    assert genai.camera_at("dolly_in", 1.0)[0] > genai.camera_at("dolly_in", 0.0)[0]
    assert genai.camera_at("crash_zoom", 0.5)[0] < 1.1 < genai.camera_at("crash_zoom", 1.0)[0]
    with pytest.raises(H.BridgeError):
        genai.camera_at("barrel_roll", 0.5)


def test_import_image_tiles_a_texture(H):
    H.dispatch("document.new", {"width": 50, "height": 30, "background": "white"})
    r = H.dispatch("layer.import_image", {"data_base64": _png_b64(10, 10, "#336699"), "fit": "tile",
                                          "width": 20, "name": "Rock"})
    node = H.find_layer(H.active_doc(), r["id"])
    assert r["name"] == "Rock" and node.pixelData(45, 25, 1, 1) == bytes([0x99, 0x66, 0x33, 255])


def test_waiting_for_krita_never_blocks_its_event_loop(H):
    """The 'Scripted Brush Stroke' freeze: with Instant Preview, Krita finishes a stroke only when a timer on the
    GUI thread fires. A blocking waitForDone() never lets it fire; the bridge must wait while pumping events."""
    try:
        from PyQt6.QtCore import QTimer
    except ImportError:
        from PyQt5.QtCore import QTimer
    from krita_mcp_bridge import history

    class Busy:
        def __init__(self):
            self.done, self.unlocked = False, 0
            QTimer.singleShot(50, lambda: setattr(self, "done", True))  # the full-resolution stroke

        def tryBarrierLock(self):
            return self.done

        def unlock(self):
            self.unlocked += 1

        def waitForDone(self):  # blocks too: fine on an idle image, a freeze while a stroke runs
            assert self.done, "blocking wait while a stroke is running: Krita would freeze here"
            self.waited = True

        def refreshProjection(self):  # blocks until strokes finish - must only be called on an idle image
            assert self.done, "refreshProjection while a stroke is running: Krita would freeze here"
            self.refreshed = True

    doc = Busy()
    H._refresh(doc)
    assert doc.done and doc.refreshed and doc.waited and doc.unlocked == 1
    stroke = Busy()
    stroke.refreshProjection = lambda: (_ for _ in ()).throw(AssertionError("strokes need no refresh"))
    H._refresh(stroke, projection=False)
    assert stroke.done and stroke.unlocked == 1
    stuck = Busy()
    stuck.tryBarrierLock = lambda: False
    with pytest.raises(H.BridgeError, match="still busy"):
        history.wait_idle(stuck, timeout=0.2)  # never hangs forever either


def test_scripted_strokes_run_without_instant_preview(H, monkeypatch):
    """Instant Preview leaves the full-resolution part of a big stroke pending, which hangs the next filter,
    save or export; brush strokes switch it off and give it back afterwards."""
    class Action:
        def __init__(self, on):
            self.on = on

        def isCheckable(self):
            return True

        def isChecked(self):
            return self.on

        def setChecked(self, v):
            self.on = v

    act = Action(True)
    monkeypatch.setattr(H.Krita, "action", staticmethod(lambda name: act if name == "level_of_detail_mode" else None),
                        raising=False)
    got = H._instant_preview_off()
    assert got is act and act.on is False
    act.on = False  # already off (the user's choice): nothing to give back
    assert H._instant_preview_off() is None and act.on is False
