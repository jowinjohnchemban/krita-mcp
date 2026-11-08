#!/usr/bin/env python3
"""Example: an AI agent paints "Jesus at the Sea of Galilee, dawn" in Krita, using only MCP tool calls.

    python examples/galilee_dawn.py [stage ...] [--out DIR]     stages: scene figure light finish (default: all)

It starts the KritaPilot server over stdio and talks MCP to it, exactly like Claude Desktop / Claude Code would.
Every pixel is made by Krita: procedural SeExpr layers (sky, clouds, water, rock), vector shapes with gradients
(hills, the figure), real brush strokes (rim light, folds, glints), filters, blend modes and masks. After each
stage it saves the canvas_snapshot that the agent looks at.
"""
import argparse
import asyncio
import base64
import json
import math
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
W, H = 1000, 1400
HZ = 800  # horizon (y)
SUN = (630, 585)  # the sun sits behind the head: a natural halo
SUN_R = 34

SESSION = None
OUT = Path(".")


async def tool(name, /, **args):
    r = await SESSION.call_tool(name, args)
    if getattr(r, "is_error", None) or getattr(r, "isError", False):
        raise RuntimeError("%s failed: %s" % (name, r.content[0].text if r.content else "?"))
    data = None
    for c in r.content:
        if c.type == "image":
            data = base64.b64decode(c.data)
        elif c.type == "text":
            try:
                data = json.loads(c.text)
            except ValueError:
                data = c.text
    return data


async def look(name, /, **args):
    png = await tool("canvas_snapshot", max_size=1000, **args)
    (OUT / ("%s.png" % name)).write_bytes(png)
    stats = await tool("analyze_image", palette_size=6)
    print("  look %-14s brightness %.2f contrast %.2f palette %s" % (
        name, stats["brightness"], stats["contrast"], [p["hex"] for p in stats["palette"][:5]]))
    return stats


def ridge(x0, x1, y_base, amp, rough, seed, step=6):
    """Fractal (midpoint displacement) ridgeline."""
    rnd = random.Random(seed)
    n = 1
    pts = [y_base + rnd.uniform(-amp, amp) * 0.5, y_base + rnd.uniform(-amp, amp) * 0.5]
    a = amp
    while (x1 - x0) / n > step:
        nxt = []
        for i in range(len(pts) - 1):
            nxt += [pts[i], (pts[i] + pts[i + 1]) / 2 + rnd.uniform(-a, a)]
        nxt.append(pts[-1])
        pts, n, a = nxt, n * 2, a * rough
    return [[x0 + (x1 - x0) * i / (len(pts) - 1), round(y, 1)] for i, y in enumerate(pts)]


# ------------------------------------------------------------------ KSeExpr scripts (run inside Krita)
def sky_gradient(var):
    """KSeExpr: dawn sky colour for vertical coordinate `var` (0 = top), into $c."""
    return """
$t = clamp(%(var)s / %(hz).5f, 0, 1);
$top = [0.06, 0.10, 0.25]; $mid = [0.40, 0.34, 0.54]; $low = [0.97, 0.66, 0.46]; $hor = [1.0, 0.86, 0.64];
$c = $t < 0.55 ? mix($top, $mid, $t / 0.55) : ($t < 0.86 ? mix($mid, $low, ($t - 0.55) / 0.31)
     : mix($low, $hor, ($t - 0.86) / 0.14));
""" % {"var": var, "hz": HZ / H}


SKY = sky_gradient("$v") + """
$dx = ($u - %(su).5f) * %(asp).5f; $dy = $v - %(sv).5f;
$d = sqrt($dx * $dx + $dy * $dy);
$c = $c + [1.0, 0.76, 0.42] * (0.55 * exp(-$d * 6.5) + 0.85 * exp(-$d * 26));
$n = fbm([$u * 3.1, $v * 13.5, 0.7], 8, 2.05, 0.56);
$band = smoothstep($v, 0.05, 0.2) * (1 - smoothstep($v, 0.40, 0.54));
$cl = smoothstep($n, 0.50, 0.80) * $band;
$edge = smoothstep($n, 0.50, 0.60) * (1 - smoothstep($n, 0.62, 0.80));
$lit = mix([0.80, 0.48, 0.55], [1.0, 0.84, 0.62], clamp(exp(-$d * 3.2) * 1.5, 0, 1));
$c = mix($c, $lit * (0.92 + 0.25 * $edge), $cl * 0.82);
$c = mix($c, [1.0, 0.98, 0.90], 1 - smoothstep($d, %(sr1).5f, %(sr2).5f));
clamp($c, 0, 1)
""" % {"su": SUN[0] / W, "sv": SUN[1] / H, "asp": W / H, "sr1": SUN_R / H * 0.9, "sr2": SUN_R / H * 1.15}

WATER = """
$k = clamp(($v - %(hz).5f) / %(span).5f, 0, 1);
$r = fbm([$u * 9 / ($k * 2.5 + 0.12), $v * 260 / ($k * 4 + 0.35), 0.4], 5, 2, 0.5);
$mv = 2 * %(hz).5f - $v + ($r - 0.5) * 0.05 * ($k + 0.1);
""" % {"hz": HZ / H, "span": 1 - HZ / H} + sky_gradient("$mv") + """
$dx = ($u - %(su).5f) * %(asp).5f; $dy = $mv - %(sv).5f;
$d = sqrt($dx * $dx + $dy * $dy);
$c = $c + [1.0, 0.74, 0.40] * (0.45 * exp(-$d * 6.5) + 0.5 * exp(-$d * 22));
$c = $c * (0.82 - 0.30 * $k);
$deep = [0.05, 0.08, 0.12];
$c = mix($c, $deep, $k * 0.45);
$path = exp(-abs($u - %(su).5f) * (55 - 40 * $k));
$glint = smoothstep($r, 0.56, 0.80) * $path;
$c = $c + [1.0, 0.84, 0.58] * $glint * 1.2;
$c = $c + [0.9, 0.7, 0.5] * smoothstep($r, 0.62, 0.9) * 0.12 * (1 - $k);
clamp($c, 0, 1)
""" % {"su": SUN[0] / W, "sv": SUN[1] / H, "asp": W / H}

PEBBLES = """
$n = fbm([$u * 70, $v * 95, 0.1], 5, 2, 0.6);
$cell = cellnoise([$u * 140, $v * 190, 0.3]);
$s = 0.30 + 0.45 * $n + 0.25 * $cell;
[$s * 0.55, $s * 0.47, $s * 0.40]
"""


# ------------------------------------------------------------------ stages
async def stage_scene():
    await tool("document_new", width=W, height=H, name="Galilee dawn", background="#101828")
    await tool("create_layer", name="Sky", type="filllayer", generator="seexpr", generator_config={"script": SKY})
    await tool("create_layer", name="Water", type="filllayer", generator="seexpr",
               generator_config={"script": WATER})
    await tool("set_selection", kind="rect", x=0, y=HZ, width=W, height=H - HZ)
    await tool("create_layer", name="Water edge", type="transparencymask", parent="Water")
    await tool("set_selection", kind="none")

    far = ridge(-20, W + 20, HZ - 38, 26, 0.55, 3)
    await tool("create_layer", name="Far hills")
    await tool("draw_shapes", layer="Far hills", shapes=[{
        "type": "polygon", "points": far + [[W + 20, HZ + 2], [-20, HZ + 2]],
        "gradient": {"type": "linear", "x1": 0, "y1": HZ - 70, "x2": 0, "y2": HZ,
                     "stops": [[0, "#8f7790"], [0.65, "#b08f96"], [1, "#d6ab92"]]}}])
    near_hills = [[x, HZ - (HZ - y) * min(1.0, max(0.0, (x - 560) / 180.0)) ** 1.5]  # rises out of the lake
                  for x, y in ridge(560, W + 30, HZ - 70, 40, 0.55, 11)]
    await tool("create_layer", name="Near hills")
    await tool("draw_shapes", layer="Near hills", shapes=[{
        "type": "polygon", "points": near_hills + [[W + 30, HZ + 2], [640, HZ + 2]],
        "gradient": {"type": "linear", "x1": 0, "y1": HZ - 120, "x2": 0, "y2": HZ,
                     "stops": [[0, "#6c5470"], [1, "#a27f86"]]}}])
    await tool("apply_filter", name="blur", params={"halfWidth": 1, "halfHeight": 1}, layer="Near hills")
    # mist where the hills meet the water
    await tool("create_layer", name="Mist")
    await tool("brush_stroke", layer="Mist", preset="b) Airbrush Soft", size=46, color="#f4c9a4", opacity=0.55,
               points=[[-40, HZ - 4], [W / 2, HZ - 8], [W + 40, HZ - 2]])

    # the shore in the foreground: shape, pebble texture clipped into it, a wet edge
    shore_top = ridge(-20, W + 20, 1215, 28, 0.6, 5, step=10)
    await tool("create_layer", name="Shore", type="grouplayer")
    await tool("create_layer", name="Ground", parent="Shore")
    await tool("draw_shapes", layer="Ground", shapes=[{
        "type": "polygon", "points": shore_top + [[W + 20, H + 10], [-20, H + 10]],
        "gradient": {"type": "linear", "x1": 0, "y1": 1180, "x2": 0, "y2": H,
                     "stops": [[0, "#4a3a31"], [0.5, "#2d221d"], [1, "#171210"]]}}])
    await tool("create_layer", name="Pebbles", type="filllayer", generator="seexpr",
               generator_config={"script": PEBBLES}, parent="Shore")
    await tool("update_layer", layer="Pebbles", clipped=True, blend_mode="overlay", opacity=0.85)
    await tool("create_layer", name="Wet edge", parent="Shore")
    await tool("update_layer", layer="Wet edge", clipped=True, blend_mode="screen")
    edge = shore_top[::2]
    for i in range(0, len(edge) - 6, 9):  # a broken sheen where the lake wets the stones, not an outline
        await tool("brush_stroke", layer="Wet edge", preset="b) Airbrush Soft", size=7, color="#e9a77a",
                   opacity=0.35, points=[[x, y + 3] for x, y in edge[i:i + 6]])
    await look("1_scene")


FIGURE_SVG = """
<defs>
  <linearGradient id="robe" x1="0" y1="0" x2="1" y2="0.15">
    <stop offset="0" stop-color="#655748"/><stop offset="0.3" stop-color="#4a3f35"/>
    <stop offset="1" stop-color="#261f1a"/></linearGradient>
  <linearGradient id="sleeve" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#4f453b"/><stop offset="1" stop-color="#2c2520"/></linearGradient>
  <linearGradient id="mantle" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#6e1f1d"/><stop offset="0.55" stop-color="#4a1314"/>
    <stop offset="1" stop-color="#28090a"/></linearGradient>
  <linearGradient id="hair" x1="0" y1="0" x2="1" y2="0.3">
    <stop offset="0" stop-color="#4a3323"/><stop offset="0.5" stop-color="#2c1e15"/>
    <stop offset="1" stop-color="#1c130d"/></linearGradient>
  <linearGradient id="skin" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#8a5a40"/><stop offset="1" stop-color="#43291b"/></linearGradient>
</defs>
<path id="arm_left" fill="url(#sleeve)" d="M 592 712 C 570 740 556 770 546 800 C 532 826 514 846 490 864
  L 512 896 C 534 884 556 864 572 842 C 586 820 598 792 606 768 Z"/>
<path id="arm_right" fill="url(#sleeve)" d="M 700 712 C 722 740 736 770 746 800 C 760 826 778 846 802 864
  L 780 896 C 758 884 736 864 720 842 C 706 820 694 792 686 768 Z"/>
<path id="hand_left" fill="url(#skin)" d="M 494 868 C 482 872 470 880 462 892 C 458 898 462 904 468 900
  C 466 908 470 914 476 908 C 478 914 484 916 488 908 C 494 912 500 908 500 900 C 506 896 512 890 510 882 Z"/>
<path id="hand_right" fill="url(#skin)" d="M 798 868 C 810 872 822 880 830 892 C 834 898 830 904 824 900
  C 826 908 822 914 816 908 C 814 914 808 916 804 908 C 798 912 792 908 792 900 C 786 896 780 890 782 882 Z"/>
<path id="tunic" fill="url(#robe)" d="M 588 702 C 606 690 682 688 704 700 C 716 740 726 800 732 880
  C 740 990 748 1110 758 1272 C 700 1282 600 1282 544 1272 C 552 1120 560 980 566 880 C 570 800 576 744 588 702 Z"/>
<path id="mantle" fill="url(#mantle)" d="M 640 700 C 672 690 700 694 712 708 C 726 760 736 830 742 910
  C 750 1010 756 1100 764 1180 C 738 1194 700 1186 670 1162 C 646 1094 616 1002 578 926 C 598 852 620 776 640 700 Z"/>
<path id="hair" fill="url(#hair)" d="M 606 600 C 600 566 618 548 644 548 C 674 549 688 574 686 606
  C 690 640 700 670 710 708 C 694 716 670 716 654 710 C 636 704 622 694 614 680 C 606 654 605 626 606 600 Z"/>
<path id="beard" fill="#2a1c13" d="M 610 618 C 600 632 598 652 606 666 C 614 676 626 676 632 668
  C 626 650 618 634 610 618 Z"/>
<path id="cheek" fill="url(#skin)" d="M 608 596 C 604 606 604 616 610 622 C 614 612 614 602 612 594 Z"/>
<path id="sandal_left" fill="#1c140f" d="M 596 1272 C 590 1282 600 1290 622 1288 C 632 1284 628 1274 618 1272 Z"/>
<path id="sandal_right" fill="#1c140f" d="M 680 1274 C 676 1284 688 1292 708 1288 C 716 1282 712 1274 702 1272 Z"/>
"""


async def stage_figure():
    await tool("create_layer", name="Figure", type="grouplayer")
    # cast shadow first (below the figure): the sun is behind him, so it falls toward us
    await tool("create_layer", name="Cast shadow", parent="Figure")
    await tool("draw_shapes", layer="Cast shadow", shapes=[{
        "type": "polygon", "points": [[592, 1284], [712, 1284], [790, H], [520, H]],
        "gradient": {"type": "linear", "x1": 0, "y1": 1280, "x2": 0, "y2": H,
                     "stops": [[0, "#0b0705d0"], [1, "#0b070500"]]}}])
    await tool("apply_filter", name="blur", params={"halfWidth": 6, "halfHeight": 6}, layer="Cast shadow")
    await tool("update_layer", layer="Cast shadow", blend_mode="multiply", opacity=0.8)
    await tool("create_layer", name="Body", type="vectorlayer", parent="Figure")
    await tool("add_svg", svg=FIGURE_SVG, layer="Body")
    await tool("krita_action", name="convert_to_paint_layer", layer="Body")
    # folds: soft curved shadows following the drape (clipped to the figure, multiply)
    await tool("create_layer", name="Folds", parent="Figure")
    await tool("update_layer", layer="Folds", clipped=True, blend_mode="multiply", opacity=0.5)
    folds = ([[612, 770], [604, 860], [598, 980], [590, 1120], [586, 1262]],
             [[640, 990], [632, 1080], [628, 1170], [630, 1266]],
             [[700, 1180], [712, 1220], [726, 1266]],
             [[606, 940], [630, 1000], [652, 1080], [668, 1150]],
             [[660, 760], [674, 860], [690, 960], [706, 1060], [724, 1150]],
             [[560, 812], [540, 836], [520, 856]], [[734, 812], [754, 836], [774, 856]])
    for pts in folds:
        await tool("brush_stroke", layer="Folds", points=pts, preset="b) Airbrush Soft", size=22,
                   color="#2a201b", opacity=0.45)
    # hair: strands flowing from the crown to the shoulders, a few lighter ones catching the light
    await tool("create_layer", name="Hair strands", parent="Figure")
    await tool("update_layer", layer="Hair strands", clipped=True)
    rnd = random.Random(9)
    for i in range(26):
        x0 = 616 + i * 2.6 + rnd.uniform(-2, 2)
        pts = [[x0, 556 + rnd.uniform(0, 8)], [x0 + 8 + rnd.uniform(-3, 3), 610], [x0 + 18 + rnd.uniform(-4, 4), 660],
               [x0 + 26 + rnd.uniform(-5, 5), 708]]
        light = i % 4 == 0
        await tool("brush_stroke", layer="Hair strands", points=pts, preset="b) Basic-1", size=1.6 if light else 2.2,
                   color="#6b4a33" if light else "#160e09", opacity=0.55 if light else 0.4)
    await look("2_figure")


async def stage_light():
    # rim light: the low sun behind him lights every edge facing it
    await tool("create_layer", name="Rim light", parent="Figure")
    await tool("update_layer", layer="Rim light", clipped=True, blend_mode="screen")
    rims = [
        ([[606, 600], [604, 576], [618, 556], [644, 549], [672, 556], [686, 582], [687, 610]], 10, 1.0),  # hair
        ([[688, 612], [694, 650], [704, 690]], 6, 0.7),                    # hair falling to the shoulder
        ([[588, 704], [604, 694], [640, 690]], 8, 0.85),                   # shoulder
        ([[592, 712], [570, 740], [556, 770], [546, 800], [530, 826], [512, 846], [490, 864]], 8, 0.9),  # left arm
        ([[700, 712], [722, 740], [736, 770], [746, 800], [762, 826], [780, 846], [802, 864]], 6, 0.6),  # right arm
        ([[576, 760], [568, 880], [560, 1000], [552, 1140], [546, 1268]], 7, 0.5),  # tunic edge
        ([[462, 892], [470, 880], [484, 870], [494, 868]], 5, 0.95),       # left hand
        ([[830, 892], [822, 880], [808, 870], [798, 868]], 5, 0.7),        # right hand
        ([[610, 620], [602, 640], [606, 664]], 5, 0.9),                    # beard
    ]
    for pts, size, op in rims:
        await tool("brush_stroke", layer="Rim light", points=pts, preset="b) Airbrush Soft", size=size,
                   color="#ffd79a", opacity=op)
    # the sun behind the head: a glow and a faint traditional halo ring BEHIND the figure ...
    await tool("create_layer", name="Halo", above="Mist")
    await tool("update_layer", layer="Halo", blend_mode="screen")
    await tool("draw_shapes", layer="Halo", shapes=[
        {"type": "ellipse", "cx": SUN[0], "cy": SUN[1], "rx": 150, "ry": 150, "gradient": {
            "type": "radial", "cx": SUN[0], "cy": SUN[1], "r": 150,
            "stops": [[0, "#fff4d8ff"], [0.28, "#ffd89a90"], [0.6, "#f0a06030"], [1, "#f0a06000"]]}},
        {"type": "ellipse", "cx": SUN[0] + 4, "cy": SUN[1] + 6, "rx": 66, "ry": 66, "stroke": "#ffe7b0",
         "stroke_width": 2.5, "opacity": 0.4}])
    await tool("apply_filter", name="blur", params={"halfWidth": 2, "halfHeight": 2}, layer="Halo")
    # ... and light wrapping around the hair in front of it (bright light bleeds over dark edges)
    await tool("create_layer", name="Light wrap")
    await tool("update_layer", layer="Light wrap", blend_mode="screen", opacity=0.6)
    await tool("draw_shapes", layer="Light wrap", shapes=[
        {"type": "ellipse", "cx": SUN[0], "cy": SUN[1] + 10, "rx": 85, "ry": 95, "gradient": {
            "type": "radial", "cx": SUN[0], "cy": SUN[1] + 10, "r": 95,
            "stops": [[0, "#ffe9c080"], [0.55, "#ffcf8a30"], [1, "#ffcf8a00"]]}}])
    # god rays through the clouds
    await tool("create_layer", name="God rays")
    await tool("update_layer", layer="God rays", blend_mode="screen", opacity=0.45)
    rays = []
    for ang in (-150, -128, -110, -96, -80, -62, -44, -30, -18):
        a1, a2 = math.radians(ang - 2.2), math.radians(ang + 2.2)
        L = 900
        rays.append({"type": "polygon", "points": [[SUN[0], SUN[1]],
                                                   [SUN[0] + L * math.cos(a1), SUN[1] + L * math.sin(a1)],
                                                   [SUN[0] + L * math.cos(a2), SUN[1] + L * math.sin(a2)]],
                     "gradient": {"type": "radial", "cx": SUN[0], "cy": SUN[1], "r": L,
                                  "stops": [[0, "#ffe2a860"], [0.5, "#ffd09018"], [1, "#ffd09000"]]}})
    await tool("draw_shapes", layer="God rays", shapes=rays)
    await tool("apply_filter", name="blur", params={"halfWidth": 14, "halfHeight": 14}, layer="God rays")
    # sun glitter on the water: under the shore and the figure, small soft sparkles
    await tool("create_layer", name="Glitter", above="Mist")
    await tool("update_layer", layer="Glitter", blend_mode="screen")
    rnd = random.Random(4)
    for _ in range(55):
        y = rnd.uniform(HZ + 6, 1190)
        k = (y - HZ) / (1190 - HZ)
        x = SUN[0] + rnd.gauss(0, 8 + 55 * k)
        w = 3 + 14 * k * rnd.random()
        await tool("brush_stroke", layer="Glitter", points=[[x - w, y], [x + w, y]], preset="b) Airbrush Soft",
                   size=2 + 3 * k, color="#fff1cf", opacity=0.35 + 0.35 * rnd.random())
    await look("3_light")


async def stage_finish():
    # film grain: a 50% grey layer with noise, in overlay mode - the classic photo trick
    await tool("create_layer", name="Grain", fill="#808080")
    await tool("adjust_image", operation="noise", settings={"amount": 18, "opacity": 100, "grayscale": True},
               layer="Grain")
    await tool("update_layer", layer="Grain", blend_mode="overlay", opacity=0.22)
    await tool("add_vignette", strength=0.4, size=0.8)
    await tool("export_image", path="galilee_dawn.png")
    await tool("save_document", path="galilee_dawn.kra")
    await look("4_final")


STAGES = {"scene": stage_scene, "figure": stage_figure, "light": stage_light, "finish": stage_finish}


async def main(stages):
    global SESSION
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=sys.executable, args=["-m", "kritapilot"],
                                   env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            SESSION = session
            for s in stages:
                print("stage", s)
                await STAGES[s]()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stages", nargs="*", default=list(STAGES))
    ap.add_argument("--out", type=Path, default=Path("."))
    a = ap.parse_args()
    OUT = a.out
    OUT.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(a.stages))
