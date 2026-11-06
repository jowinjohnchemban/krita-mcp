#!/usr/bin/env python3
"""Agent scenario: paint a sunset over mountains with Krita's real brushes, look at it, improve it.

    python scripts/paint_sunset.py [--out DIR]      # needs a running Krita with the plugin

Used as a step of scripts/smoke_krita.py too. It drives Krita exactly like an AI client would (only bridge
commands), keeps every element on its own layer, and checks the result numerically after each pass.
"""
import argparse
import base64
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from krita_mcp.bridge_client import BridgeClient  # noqa: E402

W, H = 1200, 800
HORIZON = 500
SUN = (760, 455, 62)  # x, y, radius

AIRBRUSH = "b) Airbrush Soft"
BASIC = "b) Basic-1"
BRISTLES = "f) Bristles-2 Flat Rough"
CHALK = "h) Chalk Soft"
BLENDER = "k) Blender Blur"


def ridge(seed, y0, amp, step=40):
    """A mountain silhouette: ridge points across the canvas, closed down to the bottom edge."""
    pts = []
    for i, x in enumerate(range(-40, W + 81, step)):
        y = (y0 - amp * (0.55 * math.sin(x / 170.0 + seed) + 0.3 * math.sin(x / 61.0 + 2 * seed)
                         + 0.15 * math.sin(x / 23.0 + 3 * seed)))
        pts.append([x, round(y, 1)])
    return pts


def paint(call, out=None, log=print):
    def snap(name):
        if out:
            r = call("canvas.snapshot", max_size=900)
            (Path(out) / ("%s.png" % name)).write_bytes(base64.b64decode(r["png_base64"]))

    def stroke(layer, points, **kw):
        return call("brush.stroke", layer=layer, points=points, **kw)

    call("document.new", width=W, height=H, name="Sunset over mountains", background="#1d1530")

    # ---------------------------------------------------------------- pass 1: block-in
    sky = call("layer.create", name="Sky")["id"]
    bands = [(0, "#241a46"), (90, "#3b2560"), (180, "#5e2f6e"), (260, "#8e3b6e"), (330, "#c24f62"),
             (390, "#e8704a"), (440, "#f6973f"), (480, "#ffc35a"), (520, "#ffd98a")]
    for y, color in bands:  # wide soft airbrush passes, overlapping so the bands melt together
        stroke(sky, [[-100, y], [W / 2, y + 6], [W + 100, y]], preset=AIRBRUSH, size=260, color=color, opacity=0.9)

    sun = call("layer.create", name="Sun")["id"]
    sx, sy, sr = SUN
    stroke(sun, [[sx, sy], [sx + 1, sy]], preset=AIRBRUSH, size=sr * 7, color="#ffb347", opacity=0.55)  # glow
    circle = [[sx + sr * math.cos(a / 24.0 * 2 * math.pi), sy + sr * math.sin(a / 24.0 * 2 * math.pi)]
              for a in range(24)]
    stroke(sun, circle, preset=BASIC, color="#fff3c4", closed=True, fill=True, size=4)

    far = call("layer.create", name="Far mountains")["id"]
    stroke(far, ridge(0.7, HORIZON - 20, 70) + [[W + 80, H + 40], [-40, H + 40]], preset=BASIC, size=6,
           color="#7d4c78", closed=True, fill=True)
    near_group = call("layer.create", name="Near range", type="grouplayer")["id"]  # rim light clips inside it
    near = call("layer.create", name="Near mountains", parent=near_group)["id"]
    stroke(near, ridge(2.1, HORIZON + 90, 110, step=30) + [[W + 80, H + 40], [-40, H + 40]], preset=BASIC, size=6,
           color="#3a2140", closed=True, fill=True)
    ground = call("layer.create", name="Foreground")["id"]
    stroke(ground, ridge(4.0, H - 90, 35, step=25) + [[W + 80, H + 40], [-40, H + 40]], preset=BASIC, size=6,
           color="#170c1d", closed=True, fill=True)
    snap("sunset_1_blockin")

    # ---------------------------------------------------------------- look
    before = call("image.analyze", palette_size=6, grid=3)
    sky_px = call("canvas.sample", x=200, y=300)["rgba"]
    probe_y = int(min(y for x, y in ridge(2.1, HORIZON + 90, 110, step=10) if 100 <= x <= 200)) - 18
    above_ridge = call("canvas.sample", x=150, y=probe_y)["rgba"]
    near_px = call("canvas.sample", x=200, y=H - 140)["rgba"]
    log("  look 1: contrast %.2f, brightness %.2f, sharpness %.2f; sky %s vs mountains %s"
        % (before["contrast"], before["brightness"], before["sharpness"], sky_px[:3], near_px[:3]))

    # ---------------------------------------------------------------- pass 2: improve
    # 1. Atmospheric perspective: a low, thin band of warm haze at the foot of the far range.
    mist = call("layer.create", name="Mist", above=far)["id"]
    stroke(mist, [[-100, HORIZON + 60], [W / 2, HORIZON + 52], [W + 100, HORIZON + 64]], preset=AIRBRUSH, size=90,
           color="#e9967a", opacity=0.22)

    # 2. Rim light: a thin warm edge just inside the near ridge. "Clipped" (inherit alpha) clips to the layers
    #    below *in the same group*, so the rim lives in the Near range group and cannot spill into the sky.
    rim = call("layer.create", name="Rim light", parent=near_group)["id"]
    call("layer.update", layer=rim, clipped=True, blend_mode="screen")
    #    A soft brush centred exactly on the edge: the clip removes the outer half, so the light fades inward.
    ridge_line = ridge(2.1, HORIZON + 90, 110, step=15)
    stroke(rim, ridge_line, preset=AIRBRUSH, size=16, color="#ff9a5a", opacity=0.45)
    sun_side = [[x, y] for x, y in ridge_line if sx - 330 <= x <= sx + 330]  # strongest where the sun is
    stroke(rim, sun_side, preset=BRISTLES, size=7, color="#ffc27a", opacity=0.5)

    # 3. Clouds: soft overlapping wisps (airbrush), a thin sun-lit underside (chalk), then blended.
    clouds = call("layer.create", name="Clouds", above=sky)["id"]
    for cy, x0, x1, body, lit in ((150, 80, 600, "#a5527a", "#e98a8a"), (215, 540, 1150, "#c4607a", "#f4a37a"),
                                  (300, 40, 460, "#d9736e", "#ffc07a"), (360, 780, 1180, "#e88a66", "#ffd08a")):
        span = x1 - x0
        for k, (dy, size, op) in enumerate(((0, 34, 0.45), (-9, 24, 0.35), (7, 20, 0.3))):
            a, b = x0 + span * 0.08 * k, x1 - span * 0.1 * (2 - k)
            stroke(clouds, [[a, cy + dy], [(a + b) / 2, cy + dy - 8], [b, cy + dy + 3]], preset=AIRBRUSH,
                   size=size, color=body, opacity=op)
        stroke(clouds, [[x0 + span * 0.15, cy + 12], [x1 - span * 0.2, cy + 14]], preset=CHALK, size=6,
               color=lit, opacity=0.55)
        stroke(clouds, [[x0, cy], [x1, cy + 4]], preset=BLENDER, size=40)

    # 4. Glow: one soft dab of light centred on the sun (a long stroke would pile up dabs at its ends).
    glow = call("layer.create", name="Glow", above=sun)["id"]
    call("layer.update", layer=glow, blend_mode="screen")
    stroke(glow, [[sx, sy + 8], [sx + 1, sy + 8]], preset=AIRBRUSH, size=640, color="#ff9248", opacity=0.4)

    # 5. Value structure: push the foreground darker so the light reads, and frame with a vignette.
    call("photo.adjust", operation="levels", layer=ground, in_black=0, in_white=255, gamma=0.8)
    call("photo.vignette", strength=0.3, size=0.85)
    snap("sunset_2_improved")

    # ---------------------------------------------------------------- look again
    after = call("image.analyze", palette_size=6, grid=3)
    sun_px = call("canvas.sample", x=sx, y=sy)["rgba"]
    sky_px2 = call("canvas.sample", x=200, y=300)["rgba"]
    above_ridge2 = call("canvas.sample", x=150, y=probe_y)["rgba"]
    near_px2 = call("canvas.sample", x=200, y=H - 140)["rgba"]
    log("  look 2: contrast %.2f, brightness %.2f, sharpness %.2f; sun %s"
        % (after["contrast"], after["brightness"], after["sharpness"], sun_px[:3]))
    def names(ls):
        return [n for l in ls for n in [l["name"]] + names(l.get("children", []))]
    layers = names(call("layer.list")["layers"])
    return {"before": before, "after": after, "sun": sun_px, "sky": sky_px2, "mountains": near_px2,
            "layers": layers, "above_ridge": (above_ridge, above_ridge2)}


def check(result):
    """What a good sunset needs, numerically."""
    lum = lambda c: 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]  # noqa: E731
    sun, sky, mtn = result["sun"], result["sky"], result["mountains"]
    problems = []
    if not (sun[0] > 230 and sun[1] > 200):
        problems.append("the sun is not bright and warm: %r" % (sun,))
    if not (sky[0] > sky[2]):
        problems.append("the sky near the horizon is not warm: %r" % (sky,))
    if not lum(mtn) < lum(sky) - 60:
        problems.append("mountains do not read as silhouettes against the sky: %r vs %r" % (mtn, sky))
    if not result["after"]["contrast"] >= result["before"]["contrast"]:
        problems.append("the improvement pass lowered contrast")
    a, b = result["above_ridge"]  # the sky just above the ridge: only glow/vignette may touch it, no rim light
    if max(abs(x - y) for x, y in zip(a[:3], b[:3])) > 45:
        problems.append("something spilled into the sky above the ridge: %r -> %r" % (a, b))
    expected = {"Sky", "Sun", "Glow", "Clouds", "Far mountains", "Mist", "Near range", "Near mountains", "Rim light",
                "Foreground"}
    if not expected <= set(result["layers"]):
        problems.append("missing layers: %s" % sorted(expected - set(result["layers"])))
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("."))
    ap.add_argument("--keep", action="store_true", help="leave the document open")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    client = BridgeClient(timeout=120)

    def call(method, **params):
        return client.call(method, {k: v for k, v in params.items() if v is not None})
    try:
        result = paint(call, args.out)
        problems = check(result)
        call("document.export", path="sunset.png")
        print("\n".join(problems) or "sunset OK: %s" % ", ".join(result["layers"]))
    finally:
        if not args.keep:
            call("document.close")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
