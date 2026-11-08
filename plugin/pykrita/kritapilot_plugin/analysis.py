"""Reading images: statistics, exact pixels, colour search, workspace files.

These let a model inspect a picture numerically (palette, light, layout, where a colour is)
instead of only eyeballing a downscaled snapshot.
"""
import os
import time

from .handlers import (FMT_ARGB32, IMAGE_EXTS, KEEP_AR, SMOOTH, BridgeError, QImage, _b64png, _num,
                       _refresh, _rgba_image, active_doc, find_layer, handler, parse_color, safe_path,
                       workspace_root)
from .pixels import analyze, color_mask, components, mask_bounds

ANALYZE_SIDE = 160  # statistics are computed on a downscaled copy
FIND_SIDE = 256


def region_image(p):
    """(QImage ARGB32, [x, y, w, h]) of the composite or one layer, clipped to the canvas."""
    doc = active_doc()
    W, H = doc.width(), doc.height()
    x = _num(p, "x", 0, 0, W - 1, True)
    y = _num(p, "y", 0, 0, H - 1, True)
    w = min(_num(p, "width", W - x, 1, None, True), W - x)
    h = min(_num(p, "height", H - y, 1, None, True), H - y)
    if doc.colorModel() != "RGBA" or doc.colorDepth() != "U8":
        raise BridgeError("image reading needs an RGBA 8-bit image")
    if p.get("layer"):
        img = _rgba_image(find_layer(doc, p["layer"]).projectionPixelData(x, y, w, h), w, h)
    else:
        _refresh(doc)
        img = doc.projection(x, y, w, h).convertToFormat(FMT_ARGB32)
    return img, [x, y, w, h]


def _bytes(img):
    img = img.convertToFormat(FMT_ARGB32)
    return img.constBits().asstring(img.sizeInBytes())


def _shrink(img, side):
    if max(img.width(), img.height()) <= side:
        return img, 1.0
    small = img.scaled(side, side, KEEP_AR, SMOOTH)
    return small, img.width() / float(small.width())


@handler("image.analyze")
def image_analyze(p):
    img, region = region_image(p)
    small, _ = _shrink(img, ANALYZE_SIDE)
    stats = analyze(_bytes(small), small.width(), small.height(),
                    _num(p, "palette_size", 8, 1, 32, True), _num(p, "grid", 3, 1, 8, True))
    alpha_bounds = _alpha_bounds(img)
    if alpha_bounds is not None:
        alpha_bounds = [alpha_bounds[0] + region[0], alpha_bounds[1] + region[1]] + alpha_bounds[2:]
    stats.update(region=region, content_bounds=alpha_bounds,
                 note="layout is a grid of mean colours, top row first; brightness/contrast/sharpness are 0..1")
    return stats


def _alpha_bounds(img):
    w, h = img.width(), img.height()
    return mask_bounds(_bytes(img)[3::4], w, h)


@handler("image.read_pixels")
def image_read_pixels(p):
    img, region = region_image(dict(p, width=p.get("width", 8), height=p.get("height", 8)))
    w, h = img.width(), img.height()
    if w * h > 1024:
        raise BridgeError("read at most 1024 pixels at once (e.g. 32x32); use analyze_image for big areas")
    raw = _bytes(img)
    rows = []
    for yy in range(h):
        row = []
        for xx in range(w):
            o = (yy * w + xx) * 4
            b, g, r, a = raw[o:o + 4]
            row.append("#%02x%02x%02x" % (r, g, b) + ("" if a == 255 else "%02x" % a))
        rows.append(row)
    return {"region": region, "rows": rows, "note": "rows top to bottom; #rrggbb, or #rrggbbaa when not opaque"}


@handler("image.find_color")
def image_find_color(p):
    target = parse_color(p.get("color"))
    if target is None:
        raise BridgeError("give a 'color'")
    img, region = region_image(p)
    small, scale = _shrink(img, FIND_SIDE)
    w, h = small.width(), small.height()
    mask = color_mask(_bytes(small), target, _num(p, "tolerance", 24, 0, 255, True))
    regions, total = components(mask, w, h, _num(p, "limit", 20, 1, 100, True))
    out = []
    for r in regions:
        bx, by, bw, bh = r["bbox"]
        out.append({"bbox": [int(region[0] + bx * scale), int(region[1] + by * scale),
                             max(1, int(round(bw * scale))), max(1, int(round(bh * scale)))],
                    "share": round(r["area"] / float(w * h), 4),
                    "centroid": [round(region[0] + (r["centroid"][0] + 0.5) * scale),
                                 round(region[1] + (r["centroid"][1] + 0.5) * scale)]})
    return {"matches": out, "regions_found": total,
            "coverage": round(mask.count(b"\xff") / float(w * h), 4), "searched": region,
            "precision_px": round(scale, 2)}


# ------------------------------------------------------------ workspace files
@handler("file.list")
def file_list(p):
    root = workspace_root()
    sub = safe_path(p["folder"]) if p.get("folder") else root
    if not sub.is_dir():
        raise BridgeError("not a folder: %s" % sub)
    limit = _num(p, "limit", 200, 1, 2000, True)
    out = []
    for dirpath, dirnames, files in os.walk(sub):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for f in sorted(files):
            fp = os.path.join(dirpath, f)
            st = os.stat(fp)
            out.append({"path": os.path.relpath(fp, root).replace(os.sep, "/"), "bytes": st.st_size,
                        "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))})
            if len(out) >= limit:
                return {"workspace": str(root), "files": out, "truncated": True}
        if not p.get("recursive", True):
            break
    return {"workspace": str(root), "files": out, "truncated": False}


@handler("file.view")
def file_view(p):
    """Return an image file from the workspace (downscaled) without importing it."""
    path = safe_path(p.get("path"), IMAGE_EXTS)
    img = QImage(str(path))
    if img.isNull():
        raise BridgeError("Could not read %s as an image" % path)
    w, h = img.width(), img.height()
    m = _num(p, "max_size", 1024, 64, 4096, True)
    if max(w, h) > m:
        img = img.scaled(m, m, KEEP_AR, SMOOTH)
    return {"png_base64": _b64png(img), "width": w, "height": h, "path": str(path)}
