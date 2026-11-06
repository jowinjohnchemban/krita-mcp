"""Photo adjustments, video (via Krita's bundled ffmpeg) and animation helpers."""
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time

from . import history
from .handlers import (AA, CM_IN, FMT_ALPHA8, FMT_ARGB32, KEEP_AR, SMOOTH, BridgeError, QBrush, QColor,
                       QImage, QPainter, QPointF, QRadialGradient, _active_view, _b64png, _configured_filter,
                       _num, _refresh, active_doc, app, find_layer, handler, node_info, paint_layer,
                       read_image, safe_path, write_image)
from .pixels import (apply_luts, brightness_contrast_lut, curve_lut, exposure_lut, histogram, levels_lut,
                     percentile_bounds)

try:
    from PyQt6.QtCore import QCoreApplication, QEventLoop
    _NO_INPUT = QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
except ImportError:
    from PyQt5.QtCore import QCoreApplication, QEventLoop
    _NO_INPUT = QEventLoop.ExcludeUserInputEvents

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".gif", ".m4v", ".mpg", ".mpeg", ".wmv"}
RENDER_EXTS = {".mp4", ".webm", ".gif", ".mov", ".mkv", ".apng", ".png"}
MAX_VIDEO_FRAMES = 1000
FRAMES_DIR = ".krita-mcp-frames"


# ================================================================ photo adjustments
def _channels(p):
    ch = str(p.get("channel") or "rgb").lower()
    if ch not in ("rgb", "r", "g", "b"):
        raise BridgeError("channel must be rgb, r, g or b")
    return ch


def _lut_for(op, p):
    if op == "levels":
        return levels_lut(_num(p, "in_black", 0, 0, 254), _num(p, "in_white", 255, 1, 255),
                          _num(p, "gamma", 1.0, 0.05, 10), _num(p, "out_black", 0, 0, 255),
                          _num(p, "out_white", 255, 0, 255))
    if op == "curves":
        return curve_lut(p.get("points") or [])
    if op == "brightness_contrast":
        return brightness_contrast_lut(_num(p, "brightness", 0, -100, 100), _num(p, "contrast", 0, -100, 100))
    if op == "exposure":
        return exposure_lut(_num(p, "stops", 0, -8, 8))
    if op == "gamma":
        return levels_lut(gamma=_num(p, "gamma", 1.0, 0.05, 10))
    raise KeyError(op)


def _channel_luts(op, p):
    """(r, g, b) LUTs for the LUT-based operations."""
    if op == "temperature":  # warm (+) / cool (-), and green (-) / magenta (+) tint
        t, tint = _num(p, "temperature", 0, -100, 100) / 100.0, _num(p, "tint", 0, -100, 100) / 100.0
        r, b = exposure_lut(0.35 * t), exposure_lut(-0.35 * t)
        g = exposure_lut(-0.25 * tint)
        return r, g, b
    lut = _lut_for(op, p)
    ch = _channels(p)
    return (lut if ch in ("rgb", "r") else None, lut if ch in ("rgb", "g") else None,
            lut if ch in ("rgb", "b") else None)


LUT_OPS = ("levels", "curves", "brightness_contrast", "exposure", "gamma", "temperature")


def _selection_area(doc, p):
    sel = doc.selection()
    if sel is not None and sel.width() > 0 and not p.get("ignore_selection"):
        return sel, (sel.x(), sel.y(), sel.width(), sel.height())
    return None, (0, 0, doc.width(), doc.height())


def _apply_lut_op(doc, node, op, p):
    apply_channel_luts(doc, node, _channel_luts(op, p), p)


def apply_channel_luts(doc, node, luts, p):
    """Apply (r, g, b) LUTs to a paint layer, only inside the (soft) selection if there is one."""
    sel, (x, y, w, h) = _selection_area(doc, p)
    history.backup_layer(node, doc)
    raw = bytes(node.pixelData(x, y, w, h))
    out = apply_luts(raw, *luts)
    if sel is not None:  # keep the change only inside the (soft) selection
        new = QImage(out, w, h, w * 4, FMT_ARGB32).copy()
        mask = QImage(bytes(sel.pixelData(x, y, w, h)), w, h, w, FMT_ALPHA8).copy()
        pt = QPainter(new)
        pt.setCompositionMode(CM_IN)
        pt.drawImage(0, 0, mask)
        pt.end()
        base = QImage(raw, w, h, w * 4, FMT_ARGB32).copy()
        pt = QPainter(base)
        pt.drawImage(0, 0, new)
        pt.end()
        write_image(node, base, x, y)
    else:
        write_image(node, QImage(out, w, h, w * 4, FMT_ARGB32).copy(), x, y)


# friendly name -> (krita filter id, {friendly param: (krita property, default, lo, hi, cast)})
FILTER_OPS = {
    "hue_saturation": ("hsvadjustment", {"hue": ("h", 0, -180, 180, int), "saturation": ("s", 0, -100, 100, int),
                                         "lightness": ("v", 0, -100, 100, int), "colorize": ("colorize", False)}),
    "color_balance": ("colorbalance", {
        "shadows_cyan_red": ("cyan_red_shadows", 0, -100, 100, int),
        "shadows_magenta_green": ("magenta_green_shadows", 0, -100, 100, int),
        "shadows_yellow_blue": ("yellow_blue_shadows", 0, -100, 100, int),
        "midtones_cyan_red": ("cyan_red_midtones", 0, -100, 100, int),
        "midtones_magenta_green": ("magenta_green_midtones", 0, -100, 100, int),
        "midtones_yellow_blue": ("yellow_blue_midtones", 0, -100, 100, int),
        "highlights_cyan_red": ("cyan_red_highlights", 0, -100, 100, int),
        "highlights_magenta_green": ("magenta_green_highlights", 0, -100, 100, int),
        "highlights_yellow_blue": ("yellow_blue_highlights", 0, -100, 100, int),
        "preserve_luminosity": ("preserve_luminosity", True)}),
    "sharpen": ("unsharp", {"amount": ("amount", 0.5, 0, 10, float), "radius": ("halfSize", 1, 1, 100, int),
                            "threshold": ("threshold", 0, 0, 255, int), "lightness_only": ("lightnessOnly", True)}),
    "blur": ("gaussian blur", {"radius": ("horizRadius", 5, 0, 500, float)}),
    "motion_blur": ("motion blur", {"angle": ("blurAngle", 0, 0, 360, int), "length": ("blurLength", 5, 0, 500, int)}),
    "lens_blur": ("lens blur", {"radius": ("irisRadius", 5, 1, 200, int), "rotation": ("irisRotation", 0, 0, 360, int)}),
    "denoise": ("waveletnoisereducer", {"strength": ("threshold", 7.0, 0, 100, float)}),
    "denoise_gaussian": ("gaussiannoisereducer", {"threshold": ("threshold", 15, 0, 255, int),
                                                  "window": ("windowsize", 1, 1, 20, int)}),
    "desaturate": ("desaturate", {"method": ("type", 0, 0, 6, int)}),
    "auto_contrast": ("autocontrast", {}),
    "normalize": ("normalize", {}),
    "invert": ("invert", {}),
    "posterize": ("posterize", {"steps": ("steps", 16, 2, 255, int)}),
    "threshold": ("threshold", {"level": ("threshold", 128, 0, 255, int)}),
    "pixelize": ("pixelize", {"size": ("pixelWidth", 10, 1, 500, int)}),
    "oil_paint": ("oilpaint", {"brush_size": ("brushSize", 1, 1, 50, int), "smooth": ("smooth", 30, 1, 255, int)}),
    "emboss": ("emboss", {"depth": ("depth", 30, 1, 100, int)}),
    "edge_detect": ("edge detection", {"radius": ("horizRadius", 1, 1, 50, int)}),
    "high_pass": ("gaussianhighpass", {"radius": ("blurAmount", 1, 0, 100, float)}),
    "noise": ("noise", {"amount": ("level", 50, 0, 100, int), "opacity": ("opacity", 100, 0, 100, int),
                        "grayscale": ("grayscale", False)}),
    "color_to_alpha": ("colortoalpha", {"color": ("targetcolor", "#ffffff"), "threshold": ("threshold", 100, 0, 255, int)}),
    "dodge": ("dodge", {"exposure": ("exposure", 0.5, 0, 1, float), "range": ("type", 1, 0, 2, int)}),
    "burn": ("burn", {"exposure": ("exposure", 0.5, 0, 1, float), "range": ("type", 1, 0, 2, int)}),
    "wave": ("wave", {"amplitude": ("horizontalamplitude", 4, 0, 100, int),
                      "wavelength": ("horizontalwavelength", 50, 1, 500, int)}),
    "round_corners": ("roundcorners", {"radius": ("radius", 30, 1, 500, int)}),
}


def _filter_params(op, p):
    fid, spec = FILTER_OPS[op]
    props = {}
    for friendly, rule in spec.items():
        if friendly not in p:
            continue
        key = rule[0]
        if len(rule) == 2:  # bool or colour
            props[key] = p[friendly] if not isinstance(rule[1], bool) else bool(p[friendly])
        else:
            _, _, lo, hi, cast = rule
            props[key] = cast(_num(p, friendly, None, lo, hi))
    if op == "blur" and "horizRadius" in props:
        props["vertRadius"] = props["horizRadius"]
    if op == "pixelize" and "pixelWidth" in props:
        props["pixelHeight"] = props["pixelWidth"]
    if op == "edge_detect" and "horizRadius" in props:
        props["vertRadius"] = props["horizRadius"]
    if op == "wave":
        for k in ("amplitude", "wavelength"):
            if "horizontal" + k in props:
                props["vertical" + k] = props["horizontal" + k]
    return fid, props


ADJUST_OPS = LUT_OPS + tuple(FILTER_OPS)


@handler("photo.adjust", edit=True)
def photo_adjust(p):
    """One adjustment on a paint layer (inside the selection if there is one), or as a filter mask."""
    doc = active_doc()
    op = p.get("operation")
    if op not in ADJUST_OPS:
        raise BridgeError("operation must be one of: %s" % ", ".join(ADJUST_OPS))
    if op in LUT_OPS:
        if p.get("non_destructive"):
            raise BridgeError("%s is applied to pixels; non_destructive works with filter-based operations" % op)
        node = paint_layer(doc, p.get("layer"))
        _apply_lut_op(doc, node, op, p)
    else:
        fid, props = _filter_params(op, p)
        f = _configured_filter(fid, props)
        if p.get("non_destructive"):  # a filter mask on the layer, editable/removable later
            node = find_layer(doc, p.get("layer"))
            mask = doc.createFilterMask(op, f, doc.selection() or _full(doc))
            node.addChildNode(mask, None)
            _refresh(doc)
            return {"mask": node_info(mask), "layer": node_info(node), "operation": op}
        node = paint_layer(doc, p.get("layer"))
        _, (x, y, w, h) = _selection_area(doc, p)
        history.backup_layer(node, doc)
        f.apply(node, x, y, w, h)
    _refresh(doc)
    return {"layer": node_info(node), "operation": op}


def _full(doc):
    from krita import Selection
    s = Selection()
    s.select(0, 0, doc.width(), doc.height(), 255)
    return s


@handler("photo.auto_enhance", edit=True)
def photo_auto_enhance(p):
    """Stretch levels per channel (clipping a little at each end) - a quick fix for flat or tinted photos."""
    doc = active_doc()
    node = paint_layer(doc, p.get("layer"))
    sel, (x, y, w, h) = _selection_area(doc, p)
    img = read_image(node, x, y, w, h)
    small = img.scaled(512, 512, KEEP_AR, SMOOTH) if max(w, h) > 512 else img
    raw = small.constBits().asstring(small.sizeInBytes())
    clip = _num(p, "clip", 0.5, 0, 10) / 100.0
    per_channel = p.get("per_channel", True)
    luts = []
    if per_channel:
        for offset in (2, 1, 0):  # r, g, b planes in BGRA
            lo, hi = percentile_bounds(histogram(raw, offset), clip)
            luts.append(levels_lut(lo, hi))
    else:
        hist = [a + b + c for a, b, c in zip(histogram(raw, 0), histogram(raw, 1), histogram(raw, 2))]
        lo, hi = percentile_bounds(hist, clip)
        luts = [levels_lut(lo, hi)] * 3
    history.backup_layer(node, doc)
    full = bytes(node.pixelData(x, y, w, h))
    out = apply_luts(full, *luts)
    write_image(node, QImage(out, w, h, w * 4, FMT_ARGB32).copy(), x, y)
    _refresh(doc)
    return {"layer": node_info(node), "per_channel": bool(per_channel)}


@handler("photo.vignette", edit=True)
def photo_vignette(p):
    """Darken (or lighten) the edges on a new layer above everything; tweak or delete it later."""
    doc = active_doc()
    W, H = doc.width(), doc.height()
    strength = _num(p, "strength", 0.5, 0, 1)
    size = _num(p, "size", 0.65, 0.05, 2)
    color = QColor(p.get("color") or "#000000")
    img = QImage(W, H, FMT_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    r = math.hypot(W, H) / 2.0
    g = QRadialGradient(QPointF(W / 2.0, H / 2.0), r)
    inner = QColor(color)
    inner.setAlpha(0)
    outer = QColor(color)
    outer.setAlpha(int(255 * strength))
    g.setColorAt(0, inner)
    g.setColorAt(min(0.99, size * 0.75), inner)
    g.setColorAt(1, outer)
    pt = QPainter(img)
    pt.setRenderHint(AA, True)
    pt.fillRect(0, 0, W, H, QBrush(g))
    pt.end()
    node = doc.createNode(str(p.get("name") or "Vignette"), "paintlayer")
    doc.rootNode().addChildNode(node, None)
    write_image(node, img, 0, 0)
    node.setBlendingMode(str(p.get("blend_mode") or "normal"))
    _refresh(doc)
    return node_info(node)


@handler("document.color_space", edit=True)
def document_color_space(p):
    """Convert the whole image (e.g. to RGBA/U16 for heavy photo grading, back to U8 for AI pixel edits)."""
    doc = active_doc()
    model, depth = str(p.get("model") or "RGBA"), str(p.get("depth") or "U8")
    profile = str(p.get("profile") or "")
    if not profile:
        profiles = app().profiles(model, depth)
        srgb = [x for x in profiles if "srgb" in x.lower()]
        profile = (srgb or profiles or [""])[0]
    if not doc.setColorSpace(model, depth, profile):
        raise BridgeError("Krita could not convert to %s/%s (%s)" % (model, depth, profile))
    _refresh(doc)
    return {"color_model": doc.colorModel(), "color_depth": doc.colorDepth(), "profile": doc.colorProfile(),
            "note": "pixel tools (draw_shapes, adjust curves/levels, read_pixels...) need RGBA/U8"}


# ================================================================ external tools
def _tool(name):
    """ffmpeg / ffprobe: KRITA_MCP_FFMPEG dir, then the copy bundled with Krita, then PATH."""
    exe = name + (".exe" if sys.platform.startswith("win") else "")
    for d in (os.environ.get("KRITA_MCP_FFMPEG"), QCoreApplication.applicationDirPath()):
        if d and os.path.isfile(os.path.join(d, exe)):
            return os.path.join(d, exe)
    found = shutil.which(name)
    if not found:
        raise BridgeError("%s not found (Krita normally bundles it; or set KRITA_MCP_FFMPEG)" % name)
    return found


def _run(args, timeout=110):
    """Run a command without a shell while keeping Krita's window painting."""
    box = {}

    def work():
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            box["r"] = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=flags)
        except Exception as e:  # noqa: BLE001
            box["e"] = e
    t = threading.Thread(target=work, daemon=True)
    t.start()
    while t.is_alive():
        QCoreApplication.processEvents(_NO_INPUT)
        time.sleep(0.03)
    if "e" in box:
        raise BridgeError("%s failed: %s" % (os.path.basename(args[0]), box["e"]))
    r = box["r"]
    if r.returncode != 0:
        tail = r.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise BridgeError("%s failed: %s" % (os.path.basename(args[0]), " | ".join(tail)))
    return r


def _probe(path):
    r = _run([_tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_streams", "-show_format",
              str(path)], timeout=30)
    data = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if v is None:
        raise BridgeError("no video stream in %s" % path)
    num, _, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    dur = float(v.get("duration") or data.get("format", {}).get("duration") or 0)
    return {"width": int(v.get("width", 0)), "height": int(v.get("height", 0)), "fps": round(fps, 3),
            "duration": round(dur, 3), "frames": int(v.get("nb_frames") or round(dur * fps)),
            "codec": v.get("codec_name"),
            "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", []))}


@handler("video.info")
def video_info(p):
    path = safe_path(p.get("path"), VIDEO_EXTS)
    if not path.exists():
        raise BridgeError("File not found: %s" % path)
    return dict(_probe(path), path=str(path))


def _frames_dir(stem):
    from .util import workspace_root
    d = workspace_root() / FRAMES_DIR / ("%s_%d" % (stem, int(time.time() * 1000)))
    d.mkdir(parents=True, exist_ok=True)
    return d


@handler("video.import", edit=True)
def video_import(p):
    """Decode a video into frames and load them as an animated layer (new document by default)."""
    path = safe_path(p.get("path"), VIDEO_EXTS)
    if not path.exists():
        raise BridgeError("File not found: %s" % path)
    info = _probe(path)
    fps = _num(p, "fps", min(60, max(1, round(info["fps"] or 24))), 1, 120)
    max_frames = _num(p, "max_frames", 240, 1, MAX_VIDEO_FRAMES, True)
    start = _num(p, "start", 0, 0)
    width = _num(p, "width", min(info["width"] or 1280, 1920), 16, 8192, True)
    folder = _frames_dir(path.stem)
    args = [_tool("ffmpeg"), "-v", "error", "-y", "-ss", str(start)]
    if p.get("duration"):
        args += ["-t", str(_num(p, "duration", None, 0.05, 3600))]
    args += ["-i", str(path), "-vf", "fps=%g,scale=%d:-2" % (fps, width), "-frames:v", str(max_frames),
             str(folder / "f_%05d.png")]
    try:
        _run(args)
        files = sorted(str(f) for f in folder.glob("f_*.png"))
        if not files:
            raise BridgeError("ffmpeg produced no frames (check start/duration)")
        first = QImage(files[0])
        from .handlers import document_new
        if p.get("into_current"):
            doc = active_doc()
        else:
            document_new({"width": first.width(), "height": first.height(), "background": "white",
                          "name": str(p.get("name") or path.stem)[:120]})
            doc = active_doc()
        start_frame = _num(p, "first_frame", 0, 0, 100000, True)
        doc.setFramesPerSecond(int(round(fps)))
        if not doc.importAnimation(files, start_frame, 1):
            raise BridgeError("Krita could not import the frames")
        end = start_frame + len(files) - 1
        doc.setFullClipRangeStartTime(start_frame)
        doc.setFullClipRangeEndTime(end)
        doc.setPlayBackRange(start_frame, end)
        doc.setCurrentTime(start_frame)
        _refresh(doc)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return {"frames": len(files), "fps": fps, "range": [start_frame, end], "size": [first.width(), first.height()],
            "source": info, "layer": node_info(doc.activeNode()) if doc.activeNode() else None,
            "note": "draw/filter/adjust edit the current frame; use animation_set(frame=...) or for_each_frame"}


def _render_frames(doc, folder, start, end):
    keep = doc.currentTime()
    files = []
    try:
        for t in range(start, end + 1):
            doc.setCurrentTime(t)
            _refresh(doc)
            f = folder / ("f_%05d.png" % (t - start))
            if not doc.projection(0, 0, doc.width(), doc.height()).convertToFormat(FMT_ARGB32).save(str(f)):
                raise BridgeError("could not write %s" % f)
            files.append(f)
    finally:
        doc.setCurrentTime(keep)
        _refresh(doc)
    return files


@handler("video.render")
def video_render(p):
    """Render the animation (or a frame range) to mp4/webm/gif/mov/mkv/apng in the workspace."""
    doc = active_doc()
    out = safe_path(p.get("path"), RENDER_EXTS)
    start = _num(p, "start", doc.playBackStartTime(), 0, 100000, True)
    end = _num(p, "end", doc.playBackEndTime(), start, start + 5000, True)
    fps = _num(p, "fps", doc.framesPerSecond() or 24, 1, 120)
    scale = _num(p, "width", 0, 0, 8192, True)
    folder = _frames_dir("render")
    try:
        _render_frames(doc, folder, start, end)
        ext = out.suffix.lower()
        vf = "scale=%s:-2:flags=lanczos" % (scale or "trunc(iw/2)*2")
        args = [_tool("ffmpeg"), "-v", "error", "-y", "-framerate", "%g" % fps, "-i", str(folder / "f_%05d.png")]
        if ext == ".gif":
            args += ["-filter_complex",
                     "[0:v]%s,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer" % vf,
                     "-loop", "0"]
        elif ext in (".apng", ".png"):
            args += ["-vf", vf, "-plays", "0", "-f", "apng"]
        elif ext == ".webm":
            args += ["-vf", vf, "-c:v", "libvpx-vp9", "-b:v", "0", "-crf",
                     str(_num(p, "quality", 32, 0, 63, True)), "-pix_fmt", "yuv420p"]
        elif ext == ".mov":
            args += ["-vf", vf, "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"]
        else:  # mp4 / mkv: H.264 via OpenH264 (what Krita ships)
            args += ["-vf", vf + ",scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libopenh264",
                     "-b:v", "%dk" % _num(p, "bitrate_kbps", 6000, 100, 100000, True), "-pix_fmt", "yuv420p"]
        args.append(str(out))
        _run(args, timeout=600)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return {"path": str(out), "bytes": out.stat().st_size, "frames": end - start + 1, "fps": fps}


# ================================================================ animation helpers
@handler("animation.keyframes")
def animation_keyframes(p):
    doc = active_doc()
    n = find_layer(doc, p.get("layer"))
    start = _num(p, "start", doc.fullClipRangeStartTime(), 0, 100000, True)
    end = _num(p, "end", doc.fullClipRangeEndTime(), start, start + 20000, True)
    return {"layer": n.name(), "animated": n.animated(),
            "keyframes": [t for t in range(start, end + 1) if n.hasKeyframeAtTime(t)] if n.animated() else []}


@handler("animation.import_frames", edit=True)
def animation_import_frames(p):
    """Load image files (workspace paths, in order) as frames of a new animated layer."""
    doc = active_doc()
    paths = p.get("paths")
    if not isinstance(paths, list) or not paths or len(paths) > MAX_VIDEO_FRAMES:
        raise BridgeError("'paths' must be a list of 1..%d image files" % MAX_VIDEO_FRAMES)
    from .util import IMAGE_EXTS
    files = [str(safe_path(x, IMAGE_EXTS)) for x in paths]
    first = _num(p, "first_frame", 0, 0, 100000, True)
    step = _num(p, "step", 1, 1, 100, True)
    if not doc.importAnimation(files, first, step):
        raise BridgeError("Krita could not import those frames")
    end = first + (len(files) - 1) * step
    if doc.fullClipRangeEndTime() < end:
        doc.setFullClipRangeEndTime(end)
        doc.setPlayBackRange(doc.fullClipRangeStartTime(), end)
    _refresh(doc)
    return {"frames": len(files), "range": [first, end],
            "layer": node_info(doc.activeNode()) if doc.activeNode() else None}


@handler("animation.contact_sheet")
def animation_contact_sheet(p):
    """Several frames side by side in one image, so a model can review motion at a glance."""
    doc = active_doc()
    start = _num(p, "start", doc.playBackStartTime(), 0, 100000, True)
    end = _num(p, "end", doc.playBackEndTime(), start, start + 5000, True)
    step = _num(p, "step", max(1, (end - start + 1) // 12), 1, 1000, True)
    frames = list(range(start, end + 1, step))[:48]
    cols = _num(p, "columns", min(4, len(frames)), 1, 12, True)
    rows = (len(frames) + cols - 1) // cols
    cell = _num(p, "cell_size", 256, 32, 1024, True)
    W, H = doc.width(), doc.height()
    k = cell / float(max(W, H))
    cw, ch = max(1, int(W * k)), max(1, int(H * k))
    sheet = QImage(cols * cw, rows * (ch + 16), FMT_ARGB32)
    sheet.fill(QColor(40, 40, 40))
    keep = doc.currentTime()
    pt = QPainter(sheet)
    try:
        pt.setPen(QColor(255, 255, 255))
        for i, t in enumerate(frames):
            doc.setCurrentTime(t)
            _refresh(doc)
            img = doc.projection(0, 0, W, H).scaled(cw, ch, KEEP_AR, SMOOTH)
            cx, cy = (i % cols) * cw, (i // cols) * (ch + 16)
            pt.drawImage(cx, cy + 16, img)
            pt.drawText(cx + 4, cy + 12, "frame %d" % t)
    finally:
        pt.end()
        doc.setCurrentTime(keep)
        _refresh(doc)
    return {"png_base64": _b64png(sheet), "frames": frames, "width": sheet.width(), "height": sheet.height()}


FRAME_ACTIONS = {"blank": "add_blank_frame", "duplicate": "add_duplicate_frame", "remove": "remove_frames",
                 "remove_and_pull": "remove_frames_and_pull", "hold": "insert_hold_frame",
                 "unhold": "remove_hold_frame"}


@handler("animation.frame", edit=True)
def animation_frame(p):
    """Keyframe operations at a frame of a layer (same as the timeline's right-click menu)."""
    doc = active_doc()
    n = find_layer(doc, p.get("layer"))
    kind = p.get("kind", "blank")
    action = FRAME_ACTIONS.get(kind)
    if action is None:
        raise BridgeError("kind must be one of %s" % ", ".join(FRAME_ACTIONS))
    frame = _num(p, "frame", doc.currentTime(), 0, 100000, True)
    _active_view(doc)
    if not n.animated():
        if kind not in ("blank", "duplicate"):
            raise BridgeError("layer %r is not animated" % n.name())
        n.enableAnimation()
    doc.setActiveNode(n)
    doc.setCurrentTime(frame)
    _refresh(doc)
    act = app().action(action)
    if act is None:
        raise BridgeError("This Krita version has no %r action" % action)
    act.trigger()
    _refresh(doc)
    return {"layer": node_info(n), "frame": frame, "kind": kind, "keyframe_here": n.hasKeyframeAtTime(frame)}
