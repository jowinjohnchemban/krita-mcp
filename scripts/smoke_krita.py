#!/usr/bin/env python3
"""Smoke-test every bridge command against a REAL running Krita (plugin enabled).

Usage:  python scripts/smoke_krita.py [--out DIR] [--only substring]

Creates its own documents and closes them again; never touches other open documents.
Prints one PASS/FAIL line per step and writes canvas snapshots into --out for eyeballing.
"""
import argparse
import base64
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from krita_mcp.bridge_client import BridgeClient, KritaError  # noqa: E402

client = BridgeClient(timeout=60)
RESULTS = []
STATE = {}
OUT = Path(".")


def call(method, **params):
    return client.call(method, {k: v for k, v in params.items() if v is not None})


def snap(name, **kw):
    r = call("canvas.snapshot", **kw)
    (OUT / ("%s.png" % name)).write_bytes(base64.b64decode(r["png_base64"]))
    return r


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def near(c1, c2, tol=6):
    return all(abs(a - b) <= tol for a, b in zip(c1, c2))


def sample(x, y, layer=None):
    """RGBA at a canvas pixel (composite, or one layer) - needs the canvas.sample command."""
    return tuple(call("canvas.sample", x=x, y=y, layer=layer)["rgba"])


STEPS = []


def step(fn):
    STEPS.append(fn)
    return fn


# ---------------------------------------------------------------- steps
@step
def app_info():
    r = call("app.info")
    STATE["ws"] = Path(r["workspace"])
    STATE["ndocs"] = len(r["documents"])
    return "Krita %s, workspace %s, %d docs" % (r["krita_version"], r["workspace"], len(r["documents"]))


@step
def document_new():
    r = call("document.new", width=400, height=300, name="smoke", background="#336699")
    expect((r["width"], r["height"]) == (400, 300), r)
    expect(r["color_model"] == "RGBA" and r["color_depth"] == "U8", r)
    return r


@step
def snapshot_background():
    r = snap("01_new")
    expect((r["width"], r["height"]) == (400, 300), r)
    c = sample(200, 150)
    expect(near(c, (0x33, 0x66, 0x99, 255)), "background is %r" % (c,))
    return c


@step
def layer_list():
    r = call("layer.list")
    expect(len(r["layers"]) >= 1 and r["active"], r)
    STATE["bg"] = r["layers"][0]["id"]
    return [l["name"] for l in r["layers"]]


@step
def layer_create_paint():
    r = call("layer.create", name="Shapes")
    STATE["shapes"] = r["id"]
    expect(r["type"] == "paintlayer", r)
    return r


@step
def draw_shapes():
    shapes = [
        {"type": "rect", "x": 10, "y": 10, "width": 100, "height": 60, "fill": "#ff0000"},
        {"type": "rect", "x": 120, "y": 10, "width": 100, "height": 60, "radius": 12, "stroke": "#ffffff",
         "stroke_width": 4},
        {"type": "ellipse", "cx": 300, "cy": 50, "rx": 40, "ry": 30,
         "gradient": {"type": "radial", "cx": 300, "cy": 50, "r": 40, "stops": [[0, "#ffff00"], [1, "#ff8800"]]}},
        {"type": "line", "x1": 10, "y1": 100, "x2": 390, "y2": 100, "color": "#00ff00", "stroke_width": 3},
        {"type": "polyline", "points": [[10, 120], [60, 160], [110, 120]], "color": "white"},
        {"type": "bezier", "points": [[150, 160], [180, 100], [220, 220], [260, 140]], "color": "#ffcc00",
         "stroke_width": 5},
        {"type": "polygon", "points": [[300, 120], [350, 200], [250, 200]], "fill": "#00ffff", "opacity": 0.5},
        {"type": "text", "x": 10, "y": 280, "text": "krita-mcp", "size": 36, "bold": True, "color": "#ffffff"},
    ]
    r = call("draw.shapes", shapes=shapes, layer=STATE["shapes"])
    expect(r["drawn"] == len(shapes), r)
    snap("02_shapes")
    c = sample(50, 40)
    expect(near(c, (255, 0, 0, 255)), "red rect pixel is %r" % (c,))
    return r["layer"]["bounds"]


@step
def snapshot_region():
    r = snap("03_region", x=0, y=0, width=120, height=80, max_size=64)
    expect(max(r["width"], r["height"]) <= 64, r)
    return r["region"]


@step
def layer_update():
    r = call("layer.update", layer=STATE["shapes"], name="Shapes2", opacity=0.5, blend_mode="multiply",
             visible=True)
    expect(r["name"] == "Shapes2" and abs(r["opacity"] - 0.5) < 0.01 and r["blend_mode"] == "multiply", r)
    r = call("layer.update", layer=STATE["shapes"], opacity=1, blend_mode="normal")
    return r


@step
def layer_move():
    before = call("layer.update", layer=STATE["shapes"])["bounds"]
    r = call("layer.update", layer=STATE["shapes"], x=before[0] + 20, y=before[1] + 10)
    after = r["bounds"]
    expect((after[0] - before[0], after[1] - before[1]) == (20, 10), "moved %r -> %r" % (before, after))
    call("layer.update", layer=STATE["shapes"], x=before[0], y=before[1])
    return after


@step
def layer_duplicate():
    r = call("layer.duplicate", layer=STATE["shapes"], name="Dup")
    STATE["dup"] = r["id"]
    expect(r["name"] == "Dup", r)
    return r


@step
def layer_transform():
    b0 = call("layer.update", layer=STATE["dup"])["bounds"]
    r = call("layer.transform", layer=STATE["dup"], scale_x=0.5, scale_y=0.5)
    b1 = r["bounds"]
    expect(abs(b1[2] - b0[2] * 0.5) < 6, "scale: %r -> %r" % (b0, b1))
    r = call("layer.transform", layer=STATE["dup"], rotate_degrees=45)
    r = call("layer.transform", layer=STATE["dup"], shear_x_degrees=10)
    snap("04_transform")
    return r["bounds"]


@step
def layer_create_group_vector():
    g = call("layer.create", name="Group", type="grouplayer")
    STATE["group"] = g["id"]
    v = call("layer.create", name="Vec", type="vectorlayer", parent=g["id"])
    p = call("layer.create", name="Filled", parent=g["id"], fill="#00ff0080", above=v["id"])
    tree = call("layer.list")["layers"]
    grp = [l for l in tree if l["id"] == g["id"]][0]
    expect([c["name"] for c in grp["children"]] == ["Vec", "Filled"], grp)
    STATE["filled"] = p["id"]
    return [c["name"] for c in grp["children"]]


@step
def import_image_base64():
    png = base64.b64decode(call("canvas.snapshot", max_size=100)["png_base64"])
    r = call("layer.import_image", data_base64=base64.b64encode(png).decode(), name="Imported", fit="none",
             x=5, y=5)
    STATE["imported"] = r["id"]
    expect(r["bounds"][0] == 5 and r["bounds"][1] == 5, r)
    return r["bounds"]


@step
def import_image_path():
    p = STATE["ws"] / "smoke_in.png"
    p.write_bytes(base64.b64decode(call("canvas.snapshot", max_size=80)["png_base64"]))
    r = call("layer.import_image", path="smoke_in.png", name="FromFile", fit="contain")
    return r["bounds"]


@step
def path_sandbox():
    try:
        call("layer.import_image", path="C:/Windows/win.ini")
    except KritaError as e:
        expect("outside" in str(e) or "Unsupported" in str(e), e)
        return str(e)[:80]
    raise AssertionError("sandbox escape accepted")


@step
def selection_ops():
    out = []
    out.append(call("selection.set", kind="rect", x=10, y=10, width=100, height=100)["selection"])
    out.append(call("selection.set", kind="rect", x=50, y=50, width=100, height=100, mode="add")["selection"])
    expect(out[-1] == [10, 10, 140, 140], out)
    out.append(call("selection.set", kind="rect", x=0, y=0, width=60, height=60, mode="subtract")["selection"])
    out.append(call("selection.set", kind="rect", x=0, y=0, width=120, height=120, mode="intersect")["selection"])
    out.append(call("selection.set", kind="all")["selection"])
    expect(out[-1] == [0, 0, 400, 300], out)
    out.append(call("selection.set", kind="none")["selection"])
    expect(out[-1] is None, out)
    expect(call("document.info")["selection"] is None, "selection still set after kind=none")
    return out


@step
def selection_modify():
    call("selection.set", kind="rect", x=100, y=100, width=100, height=80)
    out = {}
    for op in ("grow", "shrink", "feather", "smooth", "border", "invert"):
        out[op] = call("selection.modify", operation=op, amount=4)["selection"]
    call("selection.set", kind="none")
    return out


@step
def selection_fill_and_erase():
    lay = call("layer.create", name="FillTarget")["id"]
    call("selection.set", kind="rect", x=200, y=150, width=50, height=50)
    call("selection.fill", color="#ff00ff", layer=lay)
    c = sample(225, 175, layer=lay)
    expect(near(c, (255, 0, 255, 255)), "filled pixel %r" % (c,))
    c = sample(190, 175, layer=lay)
    expect(c[3] == 0, "outside fill should be transparent, got %r" % (c,))
    call("selection.set", kind="rect", x=200, y=150, width=25, height=50)
    call("selection.fill", layer=lay)
    c = sample(210, 175, layer=lay)
    expect(c[3] == 0, "erased pixel %r" % (c,))
    call("selection.set", kind="none")
    snap("05_fill")
    return "ok"


@step
def filters():
    names = call("filter.list")["filters"]
    expect("blur" in names, names[:10])
    d = call("filter.describe", name="blur")
    call("filter.apply", name="blur", layer=STATE["shapes"], params={"halfWidth": 6, "halfHeight": 6})
    snap("06_blur")
    return {"n": len(names), "blur_props": d["default_properties"]}


@step
def filter_in_selection():
    call("selection.set", kind="rect", x=0, y=0, width=200, height=150)
    call("filter.apply", name="invert", layer=STATE["bg"])
    c_in, c_out = sample(5, 5, layer=STATE["bg"]), sample(390, 290, layer=STATE["bg"])
    call("selection.set", kind="none")
    expect(near(c_in, (0xcc, 0x99, 0x66, 255)), "inverted %r" % (c_in,))
    expect(near(c_out, (0x33, 0x66, 0x99, 255)), "untouched %r" % (c_out,))
    return c_in, c_out


@step
def undo_redo():
    lay = call("layer.create", name="UndoMe")["id"]
    call("draw.shapes", layer=lay, shapes=[{"type": "rect", "x": 0, "y": 0, "width": 50, "height": 50,
                                             "fill": "#000000"}])
    a = sample(10, 10, layer=lay)
    call("history.undo")
    b = sample(10, 10, layer=lay)
    call("history.redo")
    c = sample(10, 10, layer=lay)
    expect(a[3] == 255 and b[3] == 0 and c[3] == 255, "undo/redo alpha %r %r %r" % (a, b, c))
    return a, b, c


@step
def layer_snapshot_and_sample():
    r = call("canvas.snapshot", layer=STATE["shapes"], max_size=200)
    (OUT / "08_layer_only.png").write_bytes(base64.b64decode(r["png_base64"]))
    return r["width"], r["height"]


@step
def layer_props():
    r = call("layer.update", layer=STATE["dup"], alpha_locked=True, clipped=True, color_label=3)
    expect(r.get("alpha_locked") is True and r.get("clipped") is True, r)
    r = call("layer.update", layer=STATE["dup"], alpha_locked=False, clipped=False)
    expect(r.get("alpha_locked") is False and r.get("clipped") is False, r)
    return r


@step
def layer_move_relative():
    b0 = call("layer.update", layer=STATE["dup"])["bounds"]
    b1 = call("layer.update", layer=STATE["dup"], dx=-7, dy=4)["bounds"]
    expect((b1[0] - b0[0], b1[1] - b0[1]) == (-7, 4), (b0, b1))
    return b1


@step
def layer_arrange():
    def order():
        return [l["name"] for l in call("layer.list")["layers"]]
    a = call("layer.create", name="A")["id"]
    call("layer.create", name="B")
    o = order()
    expect(o[-2:] == ["A", "B"], o)
    call("layer.arrange", layer=a, to="up")
    expect(order()[-1] == "A", order())
    call("layer.arrange", layer=a, to="bottom")
    expect(order()[0] == "A", order())
    call("layer.arrange", layer=a, to="up")
    expect(order()[1] == "A", order())
    call("layer.arrange", layer=a, to="top")
    expect(order()[-1] == "A", order())
    call("layer.arrange", layer=a, parent=STATE["group"])
    grp = [l for l in call("layer.list")["layers"] if l["id"] == STATE["group"]][0]
    expect(grp["children"][-1]["name"] == "A", grp)
    call("layer.arrange", layer=a, above=STATE["bg"], parent=None)
    expect(order()[1] == "A", order())
    call("layer.delete", layer=a)
    call("layer.delete", layer="B")
    return order()


@step
def special_layers():
    out = {}
    out["fill"] = call("layer.create", name="FillL", type="filllayer", fill="#ff000040")["type"]
    out["filter"] = call("layer.create", name="FiltL", type="filterlayer", filter="desaturate")["type"]
    out["clone"] = call("layer.create", name="CloneL", type="clonelayer", source=STATE["shapes"])["type"]
    out["fmask"] = call("layer.create", name="BlurMask", type="filtermask", parent=STATE["dup"], filter="blur",
                        filter_params={"halfWidth": 3, "halfHeight": 3})["type"]
    call("selection.set", kind="ellipse", x=0, y=0, width=200, height=150)
    out["tmask"] = call("layer.create", name="TMask", type="transparencymask", parent=STATE["imported"])["type"]
    call("selection.set", kind="none")
    snap("09_special_layers")
    for nm in ("FillL", "FiltL", "CloneL"):
        call("layer.delete", layer=nm)
    return out


@step
def selection_shapes():
    out = {}
    out["ellipse"] = call("selection.set", kind="ellipse", x=100, y=50, width=200, height=100)["selection"]
    expect(abs(out["ellipse"][2] - 200) <= 2, out)
    out["polygon"] = call("selection.set", kind="polygon", points=[[10, 10], [110, 10], [60, 90]])["selection"]
    expect(abs(out["polygon"][2] - 100) <= 2, out)
    out["layer"] = call("selection.set", kind="layer", layer=STATE["imported"])["selection"]
    call("selection.set", kind="none")
    return out


@step
def brush_engine():
    presets = call("brush.presets", limit=5000)
    lay = call("layer.create", name="Brush")["id"]
    STATE["brush"] = lay
    basic = [n for n in presets["presets"] if "basic" in n.lower()]
    preset = basic[0] if basic else None
    call("brush.stroke", layer=lay, preset=preset, size=20, color="#ff00ff",
         points=[[30, 220], [120, 180], [200, 240], [300, 200]])
    c = sample(30, 220, layer=lay)
    expect(c[3] > 0, "stroke start pixel empty: %r" % (c,))
    call("brush.stroke", layer=lay, size=30, color="#00ff00", points=[[50, 50, 0.1], [150, 60, 0.6], [250, 50, 1]])
    call("brush.stroke", layer=lay, size=4, color="#ffffff", fill=True,
         points=[[300, 120], [380, 120], [380, 180], [300, 180]], smooth=False)
    c2 = sample(340, 150, layer=lay)
    expect(c2[3] > 200, "filled brush shape centre %r" % (c2,))
    call("brush.stroke", layer=lay, size=40, eraser=True, points=[[100, 190], [140, 200]])
    snap("10_brush")
    return {"presets": presets["count"], "used": preset, "fill": c2}


@step
def merge_down_layer():
    top = call("layer.create", name="MergeTop")["id"]
    call("draw.shapes", layer=top, shapes=[{"type": "rect", "x": 0, "y": 0, "width": 5, "height": 5}])
    r = call("layer.merge_down", layer=top)
    expect(r["name"] == "Brush", r)
    STATE["brush"] = r["id"]
    return r["name"]


@step
def flip_layer_and_image():
    lay = call("layer.create", name="FlipMe")["id"]
    call("draw.shapes", layer=lay, shapes=[{"type": "rect", "x": 0, "y": 0, "width": 20, "height": 20,
                                             "fill": "#123456"}])
    call("document.flip", target="layer", layer=lay, direction="horizontal")
    c = sample(390, 10, layer=lay)
    expect(c[3] == 255, "layer flip: right edge %r" % (c,))
    call("document.flip", target="image", direction="vertical")
    c = sample(390, 290, layer=lay)
    expect(c[3] == 255, "image flip: bottom-right %r" % (c,))
    call("document.flip", target="image", direction="vertical")
    call("layer.delete", layer=lay)
    return "ok"


@step
def history_probe():
    """Which operations does Krita itself record? (informational)"""
    def top():
        h = call("history.list", limit=3)
        ks = h["krita_steps"] or []
        return ks[-1]["name"] if ks else None
    seen = {}
    lay = call("layer.create", name="Probe")["id"]
    seen["layer.create"] = top()
    for name, fn in [
        ("draw.shapes", lambda: call("draw.shapes", layer=lay, shapes=[{"type": "rect", "x": 0, "y": 0,
                                                                         "width": 9, "height": 9}])),
        ("filter.apply", lambda: call("filter.apply", name="invert", layer=lay)),
        ("layer.update", lambda: call("layer.update", layer=lay, opacity=0.4)),
        ("brush.stroke", lambda: call("brush.stroke", layer=lay, points=[[10, 10], [60, 40]], size=5)),
        ("layer.transform", lambda: call("layer.transform", layer=lay, rotate_degrees=10)),
        ("selection.set", lambda: call("selection.set", kind="rect", x=1, y=1, width=5, height=5)),
        ("selection.none", lambda: call("selection.set", kind="none")),
        ("layer.delete", lambda: call("layer.delete", layer=lay)),
    ]:
        before = top()
        fn()
        after = top()
        seen[name] = after if after != before else "(not recorded by Krita)"
    for k, v in seen.items():
        print("     %-16s -> %s" % (k, v))
    return "see above"


def state(layer_props=True):
    snapshot = call("canvas.snapshot", max_size=160)["png_base64"]
    def flat(ls):
        out = []
        for l in ls:
            out.append((l["name"], l["type"], l["visible"], l["opacity"], l["blend_mode"], tuple(l["bounds"])
                        if layer_props else None))
            out.extend(flat(l.get("children", [])))
        return out
    return snapshot, flat(call("layer.list")["layers"])


def undo_check(label, edit, prep=None):
    if prep:
        prep()
    before = state()
    edit()
    after = state()
    expect(after != before, "%s changed nothing" % label)
    r = call("history.undo")
    undone = state()
    expect(undone == before, "%s: undo did not restore the previous state (%r)" % (label, r))
    call("history.redo")
    expect(state() == after, "%s: redo did not re-apply the edit" % label)
    return r["undone"][0]


@step
def undo_matrix():
    lay = call("layer.create", name="UndoTarget")["id"]
    call("draw.shapes", layer=lay, shapes=[{"type": "rect", "x": 20, "y": 20, "width": 80, "height": 60,
                                             "fill": "#884422"}])
    rect = {"type": "ellipse", "cx": 150, "cy": 150, "rx": 40, "ry": 30, "fill": "#22cc88"}
    cases = [
        ("draw.shapes", lambda: call("draw.shapes", layer=lay, shapes=[rect])),
        ("filter.apply", lambda: call("filter.apply", name="invert", layer=lay)),
        ("layer.update", lambda: call("layer.update", layer=lay, opacity=0.3, blend_mode="multiply", dx=15)),
        ("selection.fill", lambda: call("selection.fill", color="#0000ff", layer=lay),
         lambda: call("selection.set", kind="ellipse", x=30, y=30, width=50, height=40)),
        ("brush.stroke", lambda: call("brush.stroke", layer=lay, size=25, color="#ff0000",
                                      points=[[200, 50], [260, 120], [320, 60]])),
        ("layer.transform", lambda: call("layer.transform", layer=lay, rotate_degrees=30)),
        ("layer.create", lambda: call("layer.create", name="Tmp", fill="#00000040")),
        ("layer.delete", lambda: call("layer.delete", layer="Tmp")),
        ("layer.arrange", lambda: call("layer.arrange", layer=lay, to="bottom")),
        ("flip layer", lambda: call("document.flip", target="layer", layer=lay)),
        ("import", lambda: call("layer.import_image", data_base64=call("canvas.snapshot", max_size=64)[
            "png_base64"], fit="none", x=300, y=200)),
    ]
    out = {}
    for label, edit, *prep in cases:
        call("selection.set", kind="none")
        out[label] = undo_check(label, edit, *prep)["krita_steps"]
    print("     krita steps per undone edit:", out)
    call("layer.delete", layer=lay)
    return "all restored"


@step
def undo_refuses_after_outside_edit():
    call("layer.create", name="Outside")
    call("history.undo", native=True)  # stands in for the user pressing Ctrl+Z in Krita
    try:
        call("history.undo")
    except KritaError as e:
        expect("changed in Krita" in str(e), e)
        call("history.redo", native=True)
        call("layer.delete", layer="Outside")
        return "refused: " + str(e)[:60]
    raise AssertionError("AI undo ran although Krita's history changed underneath it")


@step
def read_image_tools():
    a = call("image.analyze", grid=3)
    expect(len(a["palette"]) >= 3 and len(a["layout"]) == 3, a)
    px = call("image.read_pixels", x=0, y=0, width=4, height=2)
    expect(len(px["rows"]) == 2 and len(px["rows"][0]) == 4, px)
    lay = call("layer.create", name="Finder")["id"]
    call("draw.shapes", layer=lay, shapes=[{"type": "rect", "x": 300, "y": 220, "width": 40, "height": 30,
                                             "fill": "#00ff7f"}])
    f = call("image.find_color", color="#00ff7f", tolerance=8)
    expect(f["matches"] and abs(f["matches"][0]["bbox"][0] - 300) <= 3, f)
    STATE["finder"] = lay
    g = call("canvas.snapshot", grid=100)
    (OUT / "11_grid.png").write_bytes(base64.b64decode(g["png_base64"]))
    return {"palette": [c["hex"] for c in a["palette"][:4]], "found": f["matches"][0]["bbox"]}


@step
def color_selection_and_inpaint():
    r = call("selection.set", kind="color", x=310, y=230, contiguous=True, tolerance=8)
    expect(abs(r["selection"][2] - 40) <= 2, r)
    call("selection.modify", operation="grow", amount=6)
    call("selection.modify", operation="feather", amount=4)
    e1 = call("document.export", path="inpaint_region.png", x=280, y=200, width=80, height=70)
    e2 = call("selection.export_mask", path="inpaint_mask.png")
    patch = base64.b64encode((OUT / "11_grid.png").read_bytes()).decode()
    lay = call("layer.import_image", data_base64=patch, fit="selection", name="Inpainted")
    expect(abs(lay["bounds"][0] - r["selection"][0] + 6) <= 8, (lay, r))
    call("selection.set", kind="none")
    snap("12_inpaint")
    return [e1["bytes"], e2["path"], lay["bounds"]]


@step
def workspace_files():
    files = call("file.list")
    names = [f["path"] for f in files["files"]]
    expect("inpaint_mask.png" in names, names[:20])
    v = call("file.view", path="inpaint_mask.png", max_size=64)
    expect(v["png_base64"] and v["width"] == 400, v)
    return len(names)


@step
def vector_svg():
    r = call("vector.add_svg", name="Vec2", svg=(
        '<rect id="box" x="20" y="200" width="120" height="60" rx="10" fill="#3366ff" stroke="#ffffff" '
        'stroke-width="4"/><circle id="dot" cx="200" cy="230" r="25" fill="#ffcc00"/>'
        '<text id="label" x="240" y="240" font-size="24" fill="#ffffff">Hello</text>'))
    expect(len(r["added"]) >= 2, r)
    dot = [s for s in r["added"] if s["name"] == "dot"]  # unstroked: Krita pads stroked bboxes
    expect(dot and [round(v) for v in dot[0]["bbox"]] == [175, 205, 50, 50], r["added"])
    lst = call("vector.list", layer="Vec2", include_svg=True)
    try:
        call("vector.add_svg", layer="Vec2", svg='<image href="file:///C:/Windows/win.ini"/>')
        raise AssertionError("external href accepted")
    except KritaError:
        pass
    snap("13_vector")
    call("vector.remove", layer="Vec2", names=[lst["shapes"][0]["name"]])
    n_after = len(call("vector.list", layer="Vec2")["shapes"])
    call("history.undo")
    n_undo = len(call("vector.list", layer="Vec2")["shapes"])
    expect(n_undo == n_after + 1, (n_after, n_undo))
    return [s["name"] for s in lst["shapes"]], [s["bbox"] for s in lst["shapes"]]


@step
def resources_palettes_guides():
    pats = call("resources.list", type="pattern", limit=5)
    pals = call("resources.list", type="palette", limit=50)
    cols = call("palette.colors", name=pals["names"][0]) if pals["names"] else {"colors": []}
    g = call("document.guides", horizontal=[100, 200], vertical=[133.3], visible=True)
    expect(len(g["horizontal"]) == 2, g)
    call("document.guides", clear=True)
    fl = call("layer.create", name="Pat", type="filllayer", pattern=pats["names"][0]) if pats["names"] else None
    if fl:
        call("layer.delete", layer="Pat")
    fil = call("layer.create", name="FileL", type="filelayer", path="inpaint_mask.png")
    call("layer.delete", layer="FileL")
    return {"patterns": pats["count"], "palette": pals["names"][:1], "colors": cols["colors"][:3],
            "file_layer": fil["type"]}


@step
def animation():
    lay = call("layer.create", name="Anim")["id"]
    call("animation.set", fps=12, start=0, end=3)
    call("animation.keyframe", layer=lay, frame=0)
    call("draw.shapes", layer=lay, shapes=[{"type": "ellipse", "cx": 50, "cy": 50, "rx": 20, "ry": 20,
                                             "fill": "#ff0000"}])
    call("animation.keyframe", layer=lay, frame=2)
    call("draw.shapes", layer=lay, shapes=[{"type": "ellipse", "cx": 150, "cy": 50, "rx": 20, "ry": 20,
                                             "fill": "#ff0000"}])
    info = call("animation.info")
    ex = call("animation.export_frames", folder="frames", start=0, end=3, prefix="smoke")
    call("animation.set", frame=0)
    a0 = sample(50, 50, layer=lay)
    call("animation.set", frame=2)
    a2 = sample(50, 50, layer=lay)
    call("animation.set", frame=0)
    expect(a0[3] == 255 and a2[3] == 0, "frame 0 %r / frame 2 %r" % (a0, a2))
    call("layer.delete", layer=lay)
    return {"fps": info["fps"], "frames": ex["frames"]}


@step
def undo_reports_names():
    call("layer.create", name="UndoNames")
    r = call("history.undo")
    expect(r["undone"][0]["command"] == "layer.create", r)
    redo = call("history.redo")
    expect(redo["redone"][0]["command"] == "layer.create", redo)
    call("layer.delete", layer="UndoNames")
    h = call("history.list", limit=5)
    return r, [e["command"] for e in h["ai_edits"]]


@step
def photo_adjustments():
    call("document.new", width=160, height=120, name="photo", background="#808080")
    lay = call("layer.import_image", data_base64=call("canvas.snapshot")["png_base64"], name="Photo")["id"]
    call("draw.shapes", layer=lay, shapes=[{"type": "rect", "x": 0, "y": 0, "width": 80, "height": 120,
                                             "gradient": {"type": "linear", "x1": 0, "y1": 0, "x2": 80, "y2": 0,
                                                          "stops": [[0, "#203040"], [1, "#d0a070"]]}}])
    ops = {
        "levels": {"in_black": 30, "in_white": 220}, "curves": {"points": [[0, 0], [128, 170], [255, 255]]},
        "brightness_contrast": {"brightness": 20, "contrast": 30}, "exposure": {"stops": 0.5},
        "gamma": {"gamma": 1.4}, "temperature": {"temperature": 40, "tint": -10},
        "hue_saturation": {"hue": 40, "saturation": 30}, "color_balance": {"midtones_cyan_red": 40},
        "sharpen": {"amount": 1.5, "radius": 2}, "blur": {"radius": 3}, "motion_blur": {"angle": 30, "length": 8},
        "lens_blur": {"radius": 4}, "denoise": {"strength": 10}, "desaturate": {}, "auto_contrast": {},
        "normalize": {}, "invert": {}, "posterize": {"steps": 4}, "threshold": {"level": 100},
        "pixelize": {"size": 6}, "oil_paint": {"brush_size": 2}, "emboss": {}, "edge_detect": {},
        "high_pass": {"radius": 3}, "noise": {"amount": 30}, "color_to_alpha": {"color": "#808080"},
        "dodge": {"exposure": 0.6}, "burn": {"exposure": 0.6}, "wave": {"amplitude": 6},
        "round_corners": {"radius": 20},
    }
    changed, same = [], []
    for op, settings in ops.items():
        before = call("canvas.snapshot", max_size=80)["png_base64"]
        call("photo.adjust", operation=op, layer=lay, **settings)
        after = call("canvas.snapshot", max_size=80)["png_base64"]
        (changed if after != before else same).append(op)
        call("history.undo")
        expect(call("canvas.snapshot", max_size=80)["png_base64"] == before, "%s: undo did not restore" % op)
    expect(len(same) <= 2, "no visible change from: %s" % same)
    m = call("photo.adjust", operation="hue_saturation", layer=lay, non_destructive=True, saturation=-100)
    expect(m["mask"]["type"] == "filtermask", m)
    call("photo.auto_enhance", layer=lay)
    v = call("photo.vignette", strength=0.7)
    c = sample(2, 2)
    expect(c[0] < 100, "vignette corner %r" % (c,))
    snap("14_photo")
    cs = call("document.color_space", depth="U16")
    expect(cs["color_depth"] == "U16", cs)
    cs = call("document.color_space", depth="U8")
    expect(cs["color_depth"] == "U8", cs)
    call("document.close")
    return {"changed": len(changed), "unchanged": same, "vignette": v["name"]}


@step
def generators():
    call("document.new", width=200, height=120, name="gen", background="white")
    out = {}
    for gen, cfg in [("seexpr", {"script": "$c = noise(8*$u, 8*$v); [$c, $c*0.5, 1-$c]"}),
                     ("screentone", {"pattern": 0, "shape": 0, "size_mode": 1, "size_x": 8, "size_y": 8,
                                     "foreground_color": "#ff0000", "background_color": "#ffffff"}),
                     ("multigrid", {})]:
        lay = call("layer.create", name=gen, type="filllayer", generator=gen, generator_config=cfg)
        a = call("image.analyze", layer=lay["id"], palette_size=3)
        out[gen] = [c["hex"] for c in a["palette"]]
        expect(a["coverage"] > 0.5, "%s produced nothing: %r" % (gen, a))
        snap("15_gen_" + gen)
        call("layer.update", layer=lay["id"], visible=False)
    call("document.close")
    return out


@step
def curated_actions():
    import socket
    call("document.new", width=120, height=90, name="acts", background="white")
    call("layer.create", name="L1", fill="#336699")
    names = call("krita.action_list")["actions"]
    skip = {"duplicate_image_as_new_document", "play_pause", "stop_playback", "toggle_canvas_only"}
    fast = BridgeClient(timeout=20)
    ok, unavailable = [], {}
    for name in names:
        if name in skip:
            continue
        if name.startswith(("fill_with", "clear_selection", "copy_selection", "cut_selection", "selection_to",
                            "trim_to_selection")):
            call("selection.set", kind="rect", x=10, y=10, width=40, height=30)
        try:
            fast.call("krita.action", {"name": name})
            ok.append(name)
        except KritaError as e:
            if "Cannot reach" in str(e) or "timed out" in str(e).lower():
                raise AssertionError("%s blocked Krita (dialog?)" % name)
            unavailable[name] = str(e)[:80]
        except socket.timeout:
            raise AssertionError("%s blocked Krita (dialog?)" % name)
    call("krita.action", name="duplicate_image_as_new_document")
    expect(len(call("app.info")["documents"]) >= 2, "create_copy did not open a document")
    call("document.close")
    call("document.close")
    return {"ran": len(ok), "unavailable": unavailable}


@step
def user_controls():
    call("document.new", width=200, height=150, name="ctl", background="white")
    st = call("user.tool_state")
    new = call("user.tool_state", foreground="#ff8800", size=33)
    expect(new["foreground"] == "#ff8800" and abs(new["size"] - 33) < 0.6, new)
    call("user.tool_state", foreground=st["foreground"], size=st["size"])
    v = call("view.control", zoom=2.0, rotation=15, mirror=True)
    expect(abs(abs(v["rotation"]) - 15) < 0.5 and v["mirror"] is True, v)  # mirrored views report -angle
    call("view.control", reset=True, fit=True)
    call("user.notify", message="krita-mcp smoke test", seconds=2)
    call("document.close")
    return {"view": v}


@step
def keyframes_video_and_sheet():
    call("document.new", width=160, height=90, name="anim", background="white")
    lay = call("layer.create", name="Ball")["id"]
    call("animation.set", fps=12, start=0, end=11)
    for f in range(0, 12, 3):
        call("animation.frame", layer=lay, frame=f, kind="blank")
        call("draw.shapes", layer=lay, shapes=[{"type": "ellipse", "cx": 15 + f * 12, "cy": 45, "rx": 12, "ry": 12,
                                                 "fill": "#e04040"}])
    kf = call("animation.keyframes", layer=lay)["keyframes"]
    expect(kf == [0, 3, 6, 9], kf)
    call("animation.frame", layer=lay, frame=9, kind="duplicate")
    call("animation.frame", layer=lay, frame=10, kind="remove")
    s1 = call("canvas.snapshot", frame=6, max_size=80)
    sheet = call("animation.contact_sheet", start=0, end=11, step=3, columns=4, cell_size=120)
    (OUT / "16_contact_sheet.png").write_bytes(base64.b64decode(sheet["png_base64"]))
    r = {}
    for ext in ("mp4", "gif", "webm"):
        out = call("video.render", path="smoke_anim.%s" % ext)
        expect(out["bytes"] > 1000, out)
        r[ext] = out["bytes"]
    info = call("video.info", path="smoke_anim.mp4")
    expect(info["width"] == 160 and info["frames"] >= 11, info)
    call("document.close")
    imp = call("video.import", path="smoke_anim.mp4", max_frames=20)
    expect(imp["frames"] >= 11, imp)
    doc = call("document.info")
    # grade every frame the way for_each_frame does
    for f in range(imp["range"][0], imp["range"][1] + 1):
        call("animation.set", frame=f)
        call("photo.adjust", operation="temperature", temperature=-60, layer=doc["layers"][-1]["id"])
    call("video.render", path="smoke_graded.gif")
    call("document.close")
    return {"keyframes": kf, "rendered": r, "imported": imp["frames"], "snapshot_frame": s1["width"]}


@step
def colorize():
    call("document.new", width=200, height=150, name="lineart", background="white")
    line = call("layer.create", name="Lines")["id"]
    call("draw.shapes", layer=line, shapes=[
        {"type": "ellipse", "cx": 60, "cy": 75, "rx": 45, "ry": 45, "stroke": "#000000", "stroke_width": 4},
        {"type": "rect", "x": 120, "y": 30, "width": 60, "height": 90, "stroke": "#000000", "stroke_width": 4}])
    r = call("lineart.colorize", layer=line, regions=[
        {"color": "#ff6040", "points": [[50, 70], [70, 80]]},
        {"color": "#40a0ff", "rect": [140, 60, 20, 20]},
        {"color": "#ffffff", "points": [[5, 5], [195, 5]]}])
    snap("17_colorize")
    c1, c2 = sample(60, 60), sample(150, 100)
    call("document.close")
    expect(c1[0] > 200 and c1[2] < 120 and c2[2] > 200, "fills %r %r" % (c1, c2))
    return r["mask"]["type"], c1, c2


@step
def generative_round_trip():
    """gen.* + background removal + colour matching, without any AI service (images are made locally)."""
    call("document.new", width=200, height=150, name="genai", background="white")
    try:
        return _generative_round_trip()
    finally:
        call("document.close")


def _generative_round_trip():
    photo = call("layer.create", name="Photo", fill="#f0f0f0")["id"]
    call("draw.shapes", layer=photo, shapes=[{"type": "rect", "x": 70, "y": 40, "width": 60, "height": 70,
                                               "fill": "#c02020"}])
    r = call("image.remove_background", layer=photo, mode="select", feather=0)
    expect(r["foreground_bounds"] == [70, 40, 60, 70], r)
    ctx = call("gen.context", padding=10, target=128, multiple=8, need_mask=True, mask_alpha=True)
    expect(ctx["region"] == [60, 30, 80, 90] and ctx["size"][1] == 128 and ctx["size"][0] % 8 == 0, ctx)
    expect(all(k in ctx for k in ("image_png", "mask_png", "mask_alpha_png")), list(ctx))
    patch = call("canvas.snapshot", max_size=64, x=0, y=0, width=8, height=8)["png_base64"]  # white
    other = base64.b64encode((OUT / "11_grid.png").read_bytes()).decode() if (OUT / "11_grid.png").exists()         else patch
    before = state()
    placed = call("gen.place", images=[patch], region=ctx["region"], mask=True, name="Gen", sheet=True)
    expect(placed["layers"][0]["bounds"][:2] == [70, 40], placed)  # cut to the selection
    got = sample(100, 75, layer=placed["layers"][0]["id"])
    expect(near(got[:3], (0xf0, 0xf0, 0xf0)) and got[3] == 255, "patch pixels %r" % (got,))
    expect(sample(65, 35, layer=placed["layers"][0]["id"])[3] == 0, "outside the selection must stay empty")
    call("history.undo")
    expect(state() == before, "undo of gen.place")
    call("history.redo")
    v = call("gen.place", images=[patch, other, patch], region=[0, 0, 200, 150], name="Vars", sheet=True)
    expect(len(v["layers"]) == 3 and [l["visible"] for l in v["layers"]] == [True, False, False], v)
    (OUT / "20_variants_sheet.png").write_bytes(base64.b64decode(v["sheet_png"]))
    call("gen.pick", group=v["group"]["id"], index=2)
    kept = call("gen.pick", group=v["group"]["id"], index=2, keep_only=True)["kept"]
    names = [l["name"] for l in call("layer.list")["layers"]]
    expect("Vars" in names and not any(n.startswith("Variant") for n in names), names)
    call("layer.delete", layer=kept["id"])
    call("selection.set", kind="none")
    m = call("photo.match_colors", layer=placed["layers"][0]["id"], reference_layer=photo, strength=1.0)
    rb = call("image.remove_background", layer=photo, mode="mask")
    expect(rb["mask"]["type"] == "transparencymask" and rb["background_color"] == "#f0f0f0", rb)
    corner, raw = sample(5, 5), sample(5, 5, layer=photo)  # the mask hides it; the layer pixels stay intact
    expect(near(corner[:3], (255, 255, 255)) and near(raw[:3], (0xf0, 0xf0, 0xf0)), (corner, raw))
    ex = call("gen.expand", left=40, top=20, overlap=10)
    expect(ex["canvas"] == [240, 170] and ex["old_image_at"] == [40, 20, 200, 150], ex)
    expect(near(sample(40 + 100, 20 + 75, layer=photo)[:3], (0xc0, 0x20, 0x20), 10), "content must move with the expansion")
    snap("20_expanded")
    call("history.undo")
    info = call("document.info")
    expect((info["width"], info["height"]) == (200, 150), info)
    return {"region": ctx["region"], "match": m["reference"]["mean"], "bg": rb["background_fraction"]}


@step
def camera_move_animation():
    """camera.move: a still becomes an animated shot in a new document, then renders to mp4."""
    call("document.new", width=320, height=180, name="still", background="#204060")
    lay = call("layer.create", name="Subject")["id"]
    call("draw.shapes", layer=lay, shapes=[{"type": "ellipse", "cx": 160, "cy": 90, "rx": 30, "ry": 30,
                                            "fill": "#ffcc00"}])
    try:
        r = call("camera.move", move="dolly_in", seconds=1, fps=12, width=320)
        expect(r["frames"] == 12 and r["size"] == [320, 180], r)
        info = call("animation.info")
        expect(info["fps"] == 12, info)
        call("animation.set", frame=0)
        first = sample(125, 90)
        call("animation.set", frame=11)
        last = sample(125, 90)  # the sun grows: this point turns yellow by the end of the push-in
        expect(near(first[:3], (0x20, 0x40, 0x60), 12) and last[0] > 200, (first, last))
        out = call("video.render", path="camera_move.mp4")
        expect(out["bytes"] > 1000, out)
        call("document.close")
        for move in ("orbit_left", "crane_up", "handheld"):
            call("camera.move", move=move, seconds=0.5, fps=8, width=160)
            call("document.close")
        return {"frames": r["frames"], "first": first, "last": last, "mp4": out["bytes"]}
    finally:
        call("document.close")


@step
def paint_sunset_scenario():
    """The README prompt as an agent would do it: paint a sunset over mountains with real brushes (1200x800),
    look at it, improve it, and check the result numerically (scripts/paint_sunset.py)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import paint_sunset
    lines = []
    try:
        result = paint_sunset.paint(call, OUT, log=lines.append)
        problems = paint_sunset.check(result)
        expect(not problems, "; ".join(problems))
    finally:
        call("document.close")
    return {"layers": len(result["layers"]), "contrast": [round(result["before"]["contrast"], 2),
                                                          round(result["after"]["contrast"], 2)]}


@step
def export_layer():
    r = call("document.export", path="layer_only.png", layer=STATE["shapes"])
    r2 = call("document.export", path="q.jpg", quality=40)
    expect(r["bytes"] and r2["bytes"], (r, r2))
    return r["bytes"], r2["bytes"]


@step
def resize_and_scale():
    r = call("document.resize_canvas", width=500, height=350, x_offset=50, y_offset=25)
    expect((r["width"], r["height"]) == (500, 350), r)
    r = call("document.scale", width=250, height=175)
    expect((r["width"], r["height"]) == (250, 175), r)
    snap("07_scaled")
    return r


@step
def save_and_export():
    r1 = call("document.save", path="smoke.kra")
    expect(Path(r1["path"]).exists(), r1)
    call("document.save")  # re-save in place
    r3 = call("document.export", path="smoke.png")
    expect(Path(r3["path"]).exists() and r3["bytes"], r3)
    r4 = call("document.export", path="smoke.jpg")
    expect(Path(r4["path"]).exists(), r4)
    return [r1["path"], r3["bytes"], r4["bytes"]]


@step
def document_open_activate():
    r = call("document.open", path="smoke.png")
    expect((r["width"], r["height"]) == (250, 175), r)
    info = call("app.info")
    expect(len(info["documents"]) == STATE["ndocs"] + 2, info)
    idx = [d["index"] for d in info["documents"] if (d["file"] or "").endswith("smoke.kra")]
    r = call("document.activate", index=idx[0])
    return r["name"]


@step
def layer_delete():
    names = [l["name"] for l in call("layer.list")["layers"]]
    expect("UndoMe" in names, "active document %r has layers %s" % (call("document.info")["name"], names))
    before = len(names)
    call("layer.delete", layer="UndoMe")
    after = len(call("layer.list")["layers"])
    expect(after == before - 1, (before, after))
    return before, after


@step
def close_documents():
    call("document.close")
    call("document.close")
    n = len(call("app.info")["documents"])
    expect(n == STATE["ndocs"], "docs left: %d" % n)
    return n


@step
def document_ops():
    call("document.new", width=300, height=200, name="ops", background="white")
    call("layer.create", name="L2", fill="#ff000080")
    r = call("document.crop", x=10, y=20, width=100, height=50)
    expect((r["width"], r["height"]) == (100, 50), r)
    call("selection.set", kind="rect", x=0, y=0, width=40, height=30)
    r = call("document.crop")
    expect((r["width"], r["height"]) == (40, 30), r)
    r = call("document.rotate", degrees=90)
    expect((r["width"], r["height"]) == (30, 40), r)
    r = call("document.flatten")
    expect(len(r["layers"]) == 1, r)
    try:
        call("document.close", save=True)
        raise AssertionError("closing an unsaved doc with save=true should fail")
    except KritaError:
        pass
    call("document.save", path="ops.kra")
    call("layer.create", name="after-save")
    r = call("document.close", save=True)
    expect(r["saved"], r)
    r = call("document.open", path="ops.kra")
    names = [l["name"] for l in call("layer.list")["layers"]]
    call("document.close")
    expect("after-save" in names, names)
    return names


def main():
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("smoke_out"))
    ap.add_argument("--only")
    args = ap.parse_args()
    OUT = args.out
    OUT.mkdir(parents=True, exist_ok=True)
    for fn in STEPS:
        if args.only and args.only not in fn.__name__ and fn.__name__ != "app_info":
            continue
        try:
            detail = fn()
            RESULTS.append(True)
            print("PASS %-26s %s" % (fn.__name__, str(detail)[:150]))
        except Exception as e:  # noqa: BLE001
            RESULTS.append(False)
            print("FAIL %-26s %s: %s" % (fn.__name__, type(e).__name__, str(e)[:400]))
            if not isinstance(e, (KritaError, AssertionError)):
                traceback.print_exc()
    print("\n%d/%d passed" % (sum(RESULTS), len(RESULTS)))
    sys.exit(0 if all(RESULTS) else 1)


if __name__ == "__main__":
    main()
