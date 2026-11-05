"""Qt-free pixel maths on raw 8-bit BGRA buffers (Krita's RGBA/U8 layout).

Heavy loops run in C via bytes.translate / slicing / big-int AND, so full-resolution
masks stay fast without numpy (which Krita does not ship).
"""
import math

from .util import BridgeError


def color_mask(raw, rgba, tolerance):
    """Alpha8 mask (bytes, 255 = match) of pixels within `tolerance` of `rgba` on every channel."""
    n = len(raw) // 4
    r, g, b, a = rgba
    tol = max(0, min(255, int(tolerance)))
    acc = None
    for offset, target in ((0, b), (1, g), (2, r), (3, a)):
        table = bytes(255 if abs(v - target) <= tol else 0 for v in range(256))
        chan = int.from_bytes(raw[offset::4].translate(table), "little")
        acc = chan if acc is None else acc & chan
    return acc.to_bytes(n, "little")


def flood_region(mask, w, h, x, y):
    """Contiguous (4-connected) part of `mask` that contains (x, y), as a new Alpha8 mask."""
    m = bytearray(mask)
    out = bytearray(w * h)
    if not (0 <= x < w and 0 <= y < h) or m[y * w + x] != 255:
        return bytes(out)
    stack = [(x, y)]
    while stack:
        sx, sy = stack.pop()
        row = sy * w
        if m[row + sx] != 255:
            continue
        k = m.rfind(b"\x00", row, row + sx)
        left = (k + 1 if k != -1 else row) - row
        j = m.find(b"\x00", row + sx, row + w)
        right = (j if j != -1 else row + w) - row
        m[row + left:row + right] = bytes(right - left)  # visited
        out[row + left:row + right] = b"\xff" * (right - left)
        for ny in (sy - 1, sy + 1):
            if 0 <= ny < h:
                nrow, pos = ny * w, left
                while pos < right:
                    s = m.find(b"\xff", nrow + pos, nrow + right)
                    if s == -1:
                        break
                    stack.append((s - nrow, ny))
                    e = m.find(b"\x00", s, nrow + right)
                    pos = (e if e != -1 else nrow + right) - nrow
    return bytes(out)


def mask_bounds(mask, w, h):
    """[x, y, w, h] of the non-zero part of an Alpha8 mask, or None."""
    rows = [r for r in range(h) if mask[r * w:(r + 1) * w].strip(b"\x00")]
    if not rows:
        return None
    left, right = w, 0
    for r in rows:
        line = mask[r * w:(r + 1) * w]
        left = min(left, len(line) - len(line.lstrip(b"\x00")))
        right = max(right, len(line.rstrip(b"\x00")))
    return [left, rows[0], right - left, rows[-1] - rows[0] + 1]


def _lum(r, g, b):
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def analyze(raw, w, h, palette_size=8, grid=3):
    """Statistics a model can reason about: colour, light, contrast, layout, palette."""
    if w <= 0 or h <= 0:
        raise BridgeError("empty region")
    n = w * h
    px = memoryview(raw)
    sums = [0, 0, 0, 0]
    lum_sum = lum_sq = 0.0
    opaque = 0
    bins = {}
    cells = {}
    gw, gh = max(1, grid), max(1, grid)
    lum_rows = []
    for y in range(h):
        lrow = []
        cy = y * gh // h
        for x in range(w):
            o = (y * w + x) * 4
            b, g, r, a = px[o], px[o + 1], px[o + 2], px[o + 3]
            sums[0] += r; sums[1] += g; sums[2] += b; sums[3] += a
            L = _lum(r, g, b)
            lrow.append(L)
            if a:
                opaque += 1
                lum_sum += L
                lum_sq += L * L
                key = (r >> 4, g >> 4, b >> 4)
                e = bins.get(key)
                if e is None:
                    bins[key] = [1, r, g, b]
                else:
                    e[0] += 1; e[1] += r; e[2] += g; e[3] += b
                c = cells.setdefault((cy, x * gw // w), [0, 0, 0, 0, 0.0])
                c[0] += 1; c[1] += r; c[2] += g; c[3] += b; c[4] += L
        lum_rows.append(lrow)
    # edge energy: mean absolute luminance step to right/down neighbours (0..255)
    edge = 0.0
    if w > 1 and h > 1:
        for y in range(h - 1):
            a_row, b_row = lum_rows[y], lum_rows[y + 1]
            for x in range(w - 1):
                edge += abs(a_row[x + 1] - a_row[x]) + abs(b_row[x] - a_row[x])
        edge /= 2.0 * (w - 1) * (h - 1)
    mean_l = lum_sum / opaque if opaque else 0.0
    std_l = math.sqrt(max(0.0, lum_sq / opaque - mean_l * mean_l)) if opaque else 0.0
    palette = []
    for count, r, g, b in sorted(bins.values(), key=lambda e: -e[0])[:max(1, palette_size)]:
        palette.append({"hex": "#%02x%02x%02x" % (r // count, g // count, b // count),
                        "share": round(count / float(n), 4)})
    layout = []
    for gy in range(gh):
        row = []
        for gx in range(gw):
            c = cells.get((gy, gx))
            if not c:
                row.append(None)
            else:
                k = c[0]
                row.append({"hex": "#%02x%02x%02x" % (c[1] // k, c[2] // k, c[3] // k),
                            "brightness": round(c[4] / k / 255.0, 3)})
        layout.append(row)
    return {
        "mean_rgba": [round(s / float(n)) for s in sums],
        "coverage": round(opaque / float(n), 4),
        "brightness": round(mean_l / 255.0, 3),
        "contrast": round(std_l / 255.0, 3),
        "sharpness": round(edge / 255.0, 4),
        "palette": palette,
        "layout": layout,
    }


def components(mask, w, h, limit=20):
    """Connected regions (4-connected) of an Alpha8 mask: bbox, area and centroid, largest first."""
    seen = bytearray(mask)
    regions = []
    for start in range(w * h):
        if seen[start] != 255:
            continue
        stack = [start]
        seen[start] = 0
        area = sx = sy = 0
        x0 = y0 = 1 << 30
        x1 = y1 = -1
        while stack:
            i = stack.pop()
            x, y = i % w, i // w
            area += 1; sx += x; sy += y
            x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            for j in ((i - 1) if x > 0 else -1, (i + 1) if x < w - 1 else -1,
                      i - w if y > 0 else -1, i + w if y < h - 1 else -1):
                if j >= 0 and seen[j] == 255:
                    seen[j] = 0
                    stack.append(j)
        regions.append({"bbox": [x0, y0, x1 - x0 + 1, y1 - y0 + 1], "area": area,
                        "centroid": [sx / float(area), sy / float(area)]})
    regions.sort(key=lambda r: -r["area"])
    return regions[:limit], len(regions)


# ---------------------------------------------------------------- tone curves
# A LUT is 256 output values for the 256 input values of one channel. They are applied with
# bytes.translate on the B, G, R planes of a BGRA buffer; alpha is never touched.

def _clamp(v):
    return 0 if v < 0 else 255 if v > 255 else int(round(v))


def identity_lut():
    return list(range(256))


def levels_lut(in_black=0, in_white=255, gamma=1.0, out_black=0, out_white=255):
    if in_white <= in_black:
        raise BridgeError("in_white must be greater than in_black")
    if gamma <= 0:
        raise BridgeError("gamma must be > 0")
    out = []
    for v in range(256):
        t = min(1.0, max(0.0, (v - in_black) / float(in_white - in_black)))
        out.append(_clamp(out_black + (out_white - out_black) * t ** (1.0 / gamma)))
    return out


def brightness_contrast_lut(brightness=0, contrast=0):
    """brightness and contrast in -100..100 (0 = unchanged)."""
    c = max(-100.0, min(100.0, contrast)) * 2.55
    f = (259.0 * (c + 255.0)) / (255.0 * (259.0 - c))
    b = max(-100.0, min(100.0, brightness)) * 1.275
    return [_clamp(f * (v - 128) + 128 + b) for v in range(256)]


def exposure_lut(stops):
    k = 2.0 ** stops
    return [_clamp(v * k) for v in range(256)]


def curve_lut(points):
    """Monotone cubic (Fritsch-Carlson) curve through [[in, out], ...] points in 0..255."""
    try:
        pts = sorted((float(a), float(b)) for a, b in points)
    except (TypeError, ValueError):
        raise BridgeError("curve points must be [[in, out], ...] pairs")
    if len(pts) < 2:
        raise BridgeError("a curve needs at least 2 points")
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    if len(set(xs)) != len(xs):
        raise BridgeError("curve points need distinct input values")
    n = len(pts)
    d = [(ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(n - 1)]
    m = [d[0]] + [0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2.0 for i in range(1, n - 1)] + [d[-1]]
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0.0
        else:
            a, b = m[i] / d[i], m[i + 1] / d[i]
            s = a * a + b * b
            if s > 9:
                t = 3.0 / math.sqrt(s)
                m[i], m[i + 1] = t * a * d[i], t * b * d[i]
    out = []
    for v in range(256):
        if v <= xs[0]:
            out.append(_clamp(ys[0]))
            continue
        if v >= xs[-1]:
            out.append(_clamp(ys[-1]))
            continue
        i = max(k for k in range(n - 1) if xs[k] <= v)
        h = xs[i + 1] - xs[i]
        t = (v - xs[i]) / h
        h00, h10 = 2 * t ** 3 - 3 * t ** 2 + 1, t ** 3 - 2 * t ** 2 + t
        h01, h11 = -2 * t ** 3 + 3 * t ** 2, t ** 3 - t ** 2
        out.append(_clamp(h00 * ys[i] + h10 * h * m[i] + h01 * ys[i + 1] + h11 * h * m[i + 1]))
    return out


def apply_luts(raw, r=None, g=None, b=None):
    """New BGRA bytes with per-channel LUTs applied (None = unchanged); alpha untouched."""
    buf = bytearray(raw)
    for offset, lut in ((0, b), (1, g), (2, r)):
        if lut is not None:
            buf[offset::4] = buf[offset::4].translate(bytes(lut))
    return bytes(buf)


def histogram(raw, offset):
    plane = raw[offset::4]
    return [plane.count(i) for i in range(256)]


def percentile_bounds(hist, clip=0.005):
    """(low, high) input levels that clip `clip` of the pixels at each end."""
    total = float(sum(hist)) or 1.0
    acc, low = 0, 0
    for i, c in enumerate(hist):
        acc += c
        if acc / total > clip:
            low = i
            break
    acc, high = 0, 255
    for i in range(255, -1, -1):
        acc += hist[i]
        if acc / total > clip:
            high = i
            break
    return low, max(high, low + 1)
