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
    r = call("filter.apply", name="blur", layer=STATE["shapes"],
             params={"halfWidth": 6, "halfHeight": 6})
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
def history_probe():
    """Which operations does Krita record as undo steps?"""
    def top():
        h = call("history.list", limit=3)
        return h["steps"][-1]["name"] if h["steps"] else None, h["next_undo"]
    seen = {}
    lay = call("layer.create", name="Probe")["id"]
    seen["layer.create"] = top()
    call("draw.shapes", layer=lay, shapes=[{"type": "rect", "x": 0, "y": 0, "width": 9, "height": 9}])
    seen["draw.shapes"] = top()
    call("filter.apply", name="invert", layer=lay)
    seen["filter.apply"] = top()
    call("layer.update", layer=lay, opacity=0.4)
    seen["layer.update"] = top()
    call("layer.update", layer=lay, dx=3)
    seen["layer.move"] = top()
    call("layer.delete", layer=lay)
    seen["layer.delete"] = top()
    for k, v in seen.items():
        print("     %-14s -> %s" % (k, v))
    return "see above"


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
    r = call("brush.stroke", layer=lay, preset=preset, size=20, color="#ff00ff",
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
def undo_reports_names():
    lay = call("layer.create", name="UndoNames")["id"]
    r = call("history.undo")
    redo = call("history.redo")
    expect(r["undone"] and redo["redone"], (r, redo))
    call("layer.delete", layer=lay)
    return r, redo


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
    r2 = call("document.save")
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
    idx = [d["index"] for d in info["documents"] if d["name"] == "smoke" or (d["file"] or "").endswith("smoke.kra")]
    r = call("document.activate", index=idx[0])
    return r["name"]


@step
def layer_delete():
    before = len(call("layer.list")["layers"])
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
