"""Generative-AI round trips: hand a region (+ mask) to an image model and composite the results back as
layers, plus the finishing tools a generative workflow needs (variations, colour matching, background
removal, outpainting canvas).

The image models themselves are called by the MCP server (it can block on the network); this side only
moves pixels in and out of Krita, so every placement is one undoable step.
"""
from . import history, pixels
from .handlers import (CM_IN, CM_OUT, FMT_ALPHA8, FMT_ARGB32, FMT_GRAY8, KEEP_AR, MAX_PIXELS, SMOOTH,
                       BridgeError, QByteArray, QColor, QFont, QImage, QPainter, Selection, _b64png, _enum,
                       _label, _load_qimage, _num, _refresh, _rgba_image, active_doc, find_layer, handler,
                       node_info, paint_layer, read_image, write_image, AA_TEXT, Qt)
from .media import apply_channel_luts

IGNORE_AR = _enum(Qt, "AspectRatioMode", "IgnoreAspectRatio")
FMT_RGB32 = _enum(QImage, "Format", "Format_RGB32")
MAX_GEN_SIDE = 4096
MAX_VARIANTS = 8


def _scaled(img, w, h):
    return img if (img.width(), img.height()) == (w, h) else img.scaled(w, h, IGNORE_AR, SMOOTH)


def _region(doc, p):
    """[x, y, w, h]: explicit, else the selection's bounds + padding, else the canvas."""
    W, H = doc.width(), doc.height()
    sel = doc.selection()
    has_sel = sel is not None and sel.width() > 0
    if all(p.get(k) is not None for k in ("x", "y", "width", "height")):
        x, y = _num(p, "x", 0, 0, W - 1, True), _num(p, "y", 0, 0, H - 1, True)
        return [x, y, min(_num(p, "width", 1, 1, None, True), W - x),
                min(_num(p, "height", 1, 1, None, True), H - y)], has_sel
    if has_sel and p.get("region", "auto") != "canvas":
        pad = _num(p, "padding", 32, 0, 2048, True)
        x0, y0 = max(0, sel.x() - pad), max(0, sel.y() - pad)
        x1, y1 = min(W, sel.x() + sel.width() + pad), min(H, sel.y() + sel.height() + pad)
        return [x0, y0, x1 - x0, y1 - y0], has_sel
    return [0, 0, W, H], has_sel


def _gen_size(w, h, p):
    """Size to generate at: exact allowed size, or longest side `target` rounded to `multiple`."""
    sizes = p.get("sizes")
    if sizes:
        import math
        r = w / float(h)
        best = min(sizes, key=lambda s: abs(math.log((int(s.split("x")[0]) / float(s.split("x")[1])) / r)))
        return int(best.split("x")[0]), int(best.split("x")[1])
    m = _num(p, "multiple", 8, 1, 128, True)
    target = p.get("target")
    if not target:  # native size, capped
        target = min(max(w, h), MAX_GEN_SIDE)
    target = _num(p, "target", target, 64, MAX_GEN_SIDE, True) if p.get("target") else target
    k = target / float(max(w, h))
    gw, gh = max(m, int(round(w * k / m)) * m), max(m, int(round(h * k / m)) * m)
    return gw, gh


@handler("gen.context")
def gen_context(p):
    """The pixels (and mask) to send to an image model, scaled to the size it should generate at."""
    doc = active_doc()
    _refresh(doc)
    (x, y, w, h), has_sel = _region(doc, p)
    gw, gh = _gen_size(w, h, p)
    out = {"region": [x, y, w, h], "size": [gw, gh], "has_selection": has_sel,
           "canvas": [doc.width(), doc.height()]}
    if p.get("need_image", True):
        if p.get("layer"):
            img = _rgba_image(find_layer(doc, p["layer"]).projectionPixelData(x, y, w, h), w, h)
        else:
            img = doc.projection(x, y, w, h).convertToFormat(FMT_ARGB32)
        if not p.get("keep_alpha"):  # models want opaque pixels: flatten over a neutral grey
            flat = QImage(w, h, FMT_ARGB32)
            flat.fill(QColor(p.get("matte") or "#808080"))
            pt = QPainter(flat)
            pt.drawImage(0, 0, img)
            pt.end()
            img = flat
        img = _scaled(img, gw, gh)
        out["image_png"] = _b64png(img)
    controls = p.get("controls") or []
    if not isinstance(controls, list) or len(controls) > 4:
        raise BridgeError("controls must be a list of at most 4 guides")
    if controls:  # guide images (sketch / lines / depth ...) over the same region, at the same size
        pngs = []
        for c in controls:
            if c.get("layer"):
                src = _rgba_image(find_layer(doc, c["layer"]).projectionPixelData(x, y, w, h), w, h)
            else:
                src = doc.projection(x, y, w, h).convertToFormat(FMT_ARGB32)
            flat = QImage(w, h, FMT_ARGB32)
            flat.fill(QColor(255, 255, 255))
            pt = QPainter(flat)
            pt.drawImage(0, 0, src)
            pt.end()
            if c.get("invert"):  # ControlNet line models want white lines on black
                flat.invertPixels()
            pngs.append(_b64png(_scaled(flat, gw, gh)))
        out["control_pngs"] = pngs
    if p.get("need_mask"):
        sel = doc.selection()
        if has_sel:
            m = QImage(bytes(sel.pixelData(x, y, w, h)), w, h, w, FMT_GRAY8).copy()
        else:
            m = QImage(w, h, FMT_GRAY8)
            m.fill(QColor(255, 255, 255))
        m = _scaled(m, gw, gh)
        out["mask_png"] = _b64png(m.convertToFormat(FMT_RGB32))  # RGB: what ComfyUI/A1111/Stability expect
        if p.get("mask_alpha") and "image_png" in out:  # OpenAI: same image, transparent where to edit
            a = QImage(img)
            alpha = QImage(_tight(m), gw, gh, gw, FMT_ALPHA8).copy()
            pt = QPainter(a)
            pt.setCompositionMode(CM_OUT)
            pt.drawImage(0, 0, alpha)
            pt.end()
            out["mask_alpha_png"] = _b64png(a)
    return out


def _tight(gray):
    g = gray.convertToFormat(FMT_GRAY8)
    w, h, bpl = g.width(), g.height(), g.bytesPerLine()
    bits = g.constBits().asstring(g.sizeInBytes())
    return b"".join(bits[r * bpl:r * bpl + w] for r in range(h))


def _selection_alpha(doc, x, y, w, h):
    sel = doc.selection()
    if sel is None or sel.width() <= 0:
        return None
    return QImage(bytes(sel.pixelData(x, y, w, h)), w, h, w, FMT_ALPHA8).copy()


def _masked(img, mask):
    if mask is None:
        return img
    img = QImage(img)
    pt = QPainter(img)
    pt.setCompositionMode(CM_IN)
    pt.drawImage(0, 0, mask)
    pt.end()
    return img


@handler("gen.place", edit=True)
def gen_place(p):
    """Put generated images into the document: each stretched over `region`, cut by the selection when
    mask=true. Several images become a group of variants (first one visible) plus a comparison sheet."""
    doc = active_doc()
    datas = p.get("images") or []
    if not isinstance(datas, list) or not 1 <= len(datas) <= MAX_VARIANTS:
        raise BridgeError("images must be a list of 1..%d base64 images" % MAX_VARIANTS)
    region = p.get("region") or [0, 0, doc.width(), doc.height()]
    x, y, w, h = [int(v) for v in region]
    if w <= 0 or h <= 0 or w * h > MAX_PIXELS:
        raise BridgeError("bad region")
    mask = _selection_alpha(doc, x, y, w, h) if p.get("mask") else None
    _refresh(doc)
    base = doc.projection(x, y, w, h).convertToFormat(FMT_ARGB32) if p.get("sheet") else None
    imgs = [_masked(_scaled(_load_qimage({"data_base64": d}), w, h), mask) for d in datas]
    name = str(p.get("name") or "Generated")[:100]
    labels = p.get("labels") or []
    root = doc.rootNode()
    above = find_layer(doc, p["above"]) if p.get("above") else None
    parent = above.parentNode() if above is not None else root
    if len(imgs) == 1:
        node = doc.createNode(name, "paintlayer")
        parent.addChildNode(node, above)
        write_image(node, imgs[0], x, y)
        layers, group = [node], None
    else:
        group = doc.createNode(name, "grouplayer")
        parent.addChildNode(group, above)
        layers = []
        for i, img in enumerate(imgs):
            label = labels[i] if i < len(labels) and labels[i] else ""
            node = doc.createNode(("Variant %d %s" % (i + 1, label)).strip()[:120], "paintlayer")
            group.addChildNode(node, layers[-1] if layers else None)
            write_image(node, img, x, y)
            node.setVisible(i == 0)
            layers.append(node)
    doc.setActiveNode(layers[0])
    _refresh(doc)
    out = {"layers": [node_info(n) for n in layers], "region": [x, y, w, h]}
    if group is not None:
        out["group"] = node_info(group)
    if base is not None:
        out["sheet_png"] = _b64png(_sheet(base, imgs, p.get("cell", 384)))
    return out


def _sheet(base, imgs, cell):
    """The variants composited over what was there, side by side and numbered."""
    cell = max(96, min(768, int(cell)))
    k = cell / float(max(base.width(), base.height()))
    cw, ch = max(1, int(base.width() * k)), max(1, int(base.height() * k))
    cols = min(4, len(imgs))
    rows = (len(imgs) + cols - 1) // cols
    sheet = QImage(cols * cw, rows * ch, FMT_ARGB32)
    sheet.fill(QColor(40, 40, 40))
    pt = QPainter(sheet)
    try:
        pt.setRenderHint(AA_TEXT, True)
        f = QFont("Sans Serif")
        f.setPixelSize(16)
        f.setBold(True)
        pt.setFont(f)
        for i, img in enumerate(imgs):
            comp = QImage(base)
            q = QPainter(comp)
            q.drawImage(0, 0, img)
            q.end()
            cx, cy = (i % cols) * cw, (i // cols) * ch
            pt.drawImage(cx, cy, comp.scaled(cw, ch, KEEP_AR, SMOOTH))
            _label(pt, cx + 6, cy + 20, str(i + 1))
    finally:
        pt.end()
    return sheet


@handler("gen.pick", edit=True)
def gen_pick(p):
    """Show one variant of a variants group (1-based index); keep_only deletes the others and replaces the
    group by the chosen layer."""
    doc = active_doc()
    group = find_layer(doc, p.get("group"))
    if group.type() != "grouplayer":
        raise BridgeError("%r is not a group of variants" % group.name())
    kids = [c for c in group.childNodes() if c.type() == "paintlayer"]
    i = _num(p, "index", 1, 1, max(1, len(kids)), True) - 1
    if not kids:
        raise BridgeError("the group has no variants")
    chosen = kids[i]
    if p.get("keep_only"):
        chosen.setVisible(True)
        keep = chosen.duplicate()
        keep.setName(group.name())
        group.parentNode().addChildNode(keep, group)
        group.remove()
        doc.setActiveNode(keep)
        _refresh(doc)
        return {"kept": node_info(keep)}
    for n in kids:
        history.backup_props(n)
        n.setVisible(n == chosen)
    doc.setActiveNode(chosen)
    _refresh(doc)
    return {"visible": node_info(chosen), "variants": len(kids)}


@handler("gen.expand", edit=True)
def gen_expand(p):
    """Grow the canvas for outpainting and select the new border (overlapping the old image a little) so a
    following inpaint fills it."""
    doc = active_doc()
    W, H = doc.width(), doc.height()
    left, right = _num(p, "left", 0, 0, 8192, True), _num(p, "right", 0, 0, 8192, True)
    top, bottom = _num(p, "top", 0, 0, 8192, True), _num(p, "bottom", 0, 0, 8192, True)
    if not (left or right or top or bottom):
        raise BridgeError("give at least one of left/right/top/bottom (pixels to add)")
    nw, nh = W + left + right, H + top + bottom
    if nw * nh > MAX_PIXELS:
        raise BridgeError("canvas too large")
    history.backup_selection(doc)
    doc.resizeImage(-left, -top, nw, nh)
    _refresh(doc)
    ov = _num(p, "overlap", 24, 0, 512, True)
    sel = Selection()
    sel.select(0, 0, nw, nh, 255)
    keep = Selection()
    kx, ky = left + (ov if left else 0), top + (ov if top else 0)
    kw = W - (ov if left else 0) - (ov if right else 0)
    kh = H - (ov if top else 0) - (ov if bottom else 0)
    if kw > 0 and kh > 0:
        keep.select(kx, ky, kw, kh, 255)
        sel.subtract(keep)
    if p.get("feather", 8):
        sel.feather(_num(p, "feather", 8, 0, 256, True))
    doc.setSelection(sel)
    _refresh(doc)
    return {"canvas": [nw, nh], "old_image_at": [left, top, W, H],
            "selection": [sel.x(), sel.y(), sel.width(), sel.height()]}


@handler("gen.alpha_mask", edit=True)
def gen_alpha_mask(p):
    """Use the alpha of a returned image (e.g. from a background-removal model) as a transparency mask on
    `layer` (non-destructive), or as the selection (mode=select), or erase destructively (mode=erase)."""
    doc = active_doc()
    node = find_layer(doc, p.get("layer"))
    x, y, w, h = [int(v) for v in (p.get("region") or [0, 0, doc.width(), doc.height()])]
    img = _scaled(_load_qimage({"data_base64": p.get("data_base64")}), w, h)
    alpha = img.convertToFormat(FMT_ARGB32).constBits().asstring(w * h * 4)[3::4]
    return _apply_foreground(doc, node, alpha, x, y, w, h, p.get("mode", "mask"), p.get("feather", 0))


def _apply_foreground(doc, node, alpha, x, y, w, h, mode, feather):
    sel = Selection()
    sel.setPixelData(QByteArray(alpha), x, y, w, h)
    if feather:
        sel.feather(int(feather))
    bounds = pixels.mask_bounds(alpha, w, h)
    if mode == "select":
        history.backup_selection(doc)
        doc.setSelection(sel)
        _refresh(doc)
        return {"selection": [sel.x(), sel.y(), sel.width(), sel.height()], "foreground_bounds": bounds}
    if mode == "erase":
        if node.type() != "paintlayer":
            raise BridgeError("mode=erase needs a paint layer; use mode=mask for other layers")
        history.backup_layer(node, doc)
        cut = QImage(bytes(sel.pixelData(x, y, w, h)), w, h, w, FMT_ALPHA8).copy()
        write_image(node, _masked(read_image(node, x, y, w, h), cut), x, y)
        _refresh(doc)
        return {"layer": node_info(node), "foreground_bounds": bounds}
    if mode != "mask":
        raise BridgeError("mode must be mask, erase or select")
    if x or y or w != doc.width() or h != doc.height():  # mask outside the region: keep nothing
        full = Selection()
        full.setPixelData(QByteArray(bytes(doc.width() * doc.height())), 0, 0, doc.width(), doc.height())
        full.add(sel)
        sel = full
    mask = doc.createTransparencyMask("Background removed")
    mask.setSelection(sel)
    node.addChildNode(mask, None)
    _refresh(doc)
    return {"mask": node_info(mask), "layer": node_info(node), "foreground_bounds": bounds}


@handler("image.remove_background", edit=True)
def image_remove_background(p):
    """Remove a plain/flat background without any AI model: flood from the image border through pixels
    close to the dominant border colour. Good for product shots, scans and line art on paper."""
    doc = active_doc()
    node = find_layer(doc, p.get("layer"))
    W, H = doc.width(), doc.height()
    raw = bytes(node.projectionPixelData(0, 0, W, H)) if node.type() != "paintlayer" \
        else bytes(node.pixelData(0, 0, W, H))
    color, share = pixels.dominant_border_color(raw, W, H)
    if p.get("color"):
        from .util import parse_color
        color = parse_color(p["color"])
    tol = _num(p, "tolerance", 32, 0, 255, True)
    cand = pixels.color_mask(raw, color, tol)
    # already-transparent pixels are background too
    clear = raw[3::4].translate(bytes(255 if v < 8 else 0 for v in range(256)))
    cand = (int.from_bytes(cand, "little") | int.from_bytes(clear, "little")).to_bytes(W * H, "little")
    bg = pixels.flood_seeds(cand, W, H, pixels.border_seeds(W, H))
    fg = pixels.invert_mask(bg)
    if not fg.strip(b"\x00"):
        raise BridgeError("everything matched the background; lower the tolerance")
    res = _apply_foreground(doc, node, fg, 0, 0, W, H, p.get("mode", "mask"), _num(p, "feather", 1, 0, 64, True))
    res.update(background_color="#%02x%02x%02x" % color[:3], border_share=round(share, 3),
               background_fraction=round(bg.count(b"\xff") / float(W * H), 3))
    return res


@handler("photo.match_colors", edit=True)
def photo_match_colors(p):
    """Give a layer the colour mood (mean + contrast per channel) of a reference: another layer, a region of
    the image, or an image file. Typical use: make a generated / pasted element sit in the scene."""
    doc = active_doc()
    node = paint_layer(doc, p.get("layer"))
    W, H = doc.width(), doc.height()
    side = 256
    src_img = _rgba_image(node.pixelData(0, 0, W, H), W, H).scaled(side, side, KEEP_AR, SMOOTH)
    src = pixels.channel_stats(src_img.constBits().asstring(src_img.sizeInBytes()))
    if src is None:
        raise BridgeError("the layer has no opaque pixels")
    if p.get("reference_path"):
        ref_img = _load_qimage({"path": p["reference_path"]})
    elif p.get("reference_layer"):
        ref_img = _rgba_image(find_layer(doc, p["reference_layer"]).projectionPixelData(0, 0, W, H), W, H)
    else:
        rx, ry, rw, rh = [int(v) for v in (p.get("reference_region") or [0, 0, W, H])]
        rx, ry = max(0, rx), max(0, ry)
        rw, rh = max(1, min(rw, W - rx)), max(1, min(rh, H - ry))
        _refresh(doc)
        ref_img = doc.projection(rx, ry, rw, rh).convertToFormat(FMT_ARGB32)
    ref_img = ref_img.scaled(side, side, KEEP_AR, SMOOTH).convertToFormat(FMT_ARGB32)
    ref = pixels.channel_stats(ref_img.constBits().asstring(ref_img.sizeInBytes()))
    if ref is None:
        raise BridgeError("the reference has no opaque pixels")
    strength = _num(p, "strength", 1.0, 0, 1)
    apply_channel_luts(doc, node, pixels.transfer_luts(src, ref, strength), p)
    _refresh(doc)
    fmt = lambda st: {"mean": [round(m, 1) for m, _ in st], "std": [round(s, 1) for _, s in st]}  # noqa: E731
    return {"layer": node_info(node), "before": fmt(src), "reference": fmt(ref), "strength": strength}


# ---------------------------------------------------------------- camera moves (no AI needed)
CAMERA_MOVES = ("static", "dolly_in", "dolly_out", "push_in", "pull_back", "zoom_in", "zoom_out", "crash_zoom",
                "pan_left", "pan_right", "tilt_up", "tilt_down", "orbit_left", "orbit_right", "crane_up",
                "crane_down", "handheld", "ken_burns")


def camera_at(move, t, k=1.0):
    """(scale, dx, dy, angle) of the virtual camera at time t in 0..1: scale >= 1 zooms in, dx/dy shift the
    picture by a fraction of its size, angle in degrees. Scales always cover the frame (no empty borders)."""
    import math
    e = t * t * (3 - 2 * t)  # ease in/out
    a = 0.08 * k
    if move == "static":
        return 1.0, 0.0, 0.0, 0.0
    if move in ("dolly_in", "push_in", "zoom_in"):
        return 1 + 0.35 * k * e, 0.0, 0.0, 0.0
    if move in ("dolly_out", "pull_back", "zoom_out"):
        return 1 + 0.35 * k * (1 - e), 0.0, 0.0, 0.0
    if move == "crash_zoom":
        return 1 + 1.2 * k * t ** 4, 0.0, 0.0, 0.0
    if move in ("pan_left", "pan_right"):
        d = -a + 2 * a * e
        return 1 + 2.2 * a, d if move == "pan_left" else -d, 0.0, 0.0
    if move in ("tilt_up", "tilt_down"):
        d = -a + 2 * a * e
        return 1 + 2.2 * a, 0.0, d if move == "tilt_up" else -d, 0.0
    if move in ("orbit_left", "orbit_right"):
        sgn = 1 if move == "orbit_left" else -1
        return 1.18 + 0.05 * k, sgn * 0.06 * k * (2 * e - 1), 0.0, sgn * 3.0 * k * (2 * e - 1)
    if move in ("crane_up", "crane_down"):
        u = e if move == "crane_up" else 1 - e
        return 1 + 2.2 * a + 0.17 * k * (1 - u), 0.0, -a + 2 * a * u, 0.0
    if move == "handheld":
        n = lambda f, ph: math.sin(2 * math.pi * (f * t + ph))  # noqa: E731
        return (1.07, 0.012 * k * (n(0.7, 0) + 0.5 * n(2.3, 0.3)), 0.01 * k * (n(0.9, 0.5) + 0.5 * n(3.1, 0.1)),
                0.5 * k * n(0.5, 0.2))
    if move == "ken_burns":
        return 1 + 0.25 * k * e, -0.05 * k * e, -0.03 * k * e, 0.0
    raise BridgeError("move must be one of %s" % ", ".join(CAMERA_MOVES))


@handler("camera.move", edit=True)
def camera_move(p):
    """Animate a still: render the picture through a moving virtual camera into frames of a new animated
    document (or a new animated layer here), ready for render_video."""
    import shutil
    from .handlers import document_new
    from .media import _frames_dir
    doc = active_doc()
    move = str(p.get("move") or "dolly_in")
    camera_at(move, 0.0)  # validate early
    fps = _num(p, "fps", 24, 1, 60, True)
    frames = max(2, min(600, int(round(_num(p, "seconds", 3.0, 0.2, 30) * fps))))
    k = _num(p, "intensity", 1.0, 0.1, 3.0)
    _refresh(doc)
    W, H = doc.width(), doc.height()
    src = doc.projection(0, 0, W, H).convertToFormat(FMT_ARGB32)
    ow = _num(p, "width", min(W, 1920), 16, 4096, True)
    ow -= ow % 2
    oh = max(2, int(round(H * ow / float(W))) // 2 * 2)
    folder = _frames_dir("camera")
    files = []
    try:
        for i in range(frames):
            s, dx, dy, ang = camera_at(move, i / float(frames - 1), k)
            img = QImage(ow, oh, FMT_ARGB32)
            img.fill(QColor(0, 0, 0))
            pt = QPainter(img)
            pt.setRenderHint(_enum(QPainter, "RenderHint", "SmoothPixmapTransform"), True)
            pt.translate(ow / 2.0 + dx * ow, oh / 2.0 + dy * oh)
            pt.rotate(ang)
            f = s * ow / float(W)
            pt.scale(f, f)
            pt.drawImage(int(-W / 2), int(-H / 2), src)
            pt.end()
            path = folder / ("c_%05d.png" % i)
            img.save(str(path), "PNG")
            files.append(str(path))
        if p.get("new_document", True):
            document_new({"width": ow, "height": oh, "background": "black",
                          "name": ("%s - %s" % (doc.name() or "Untitled", move))[:120]})
            doc = active_doc()
        doc.setFramesPerSecond(fps)
        if not doc.importAnimation(files, 0, 1):
            raise BridgeError("Krita could not import the camera frames")
        doc.setFullClipRangeStartTime(0)
        doc.setFullClipRangeEndTime(frames - 1)
        doc.setPlayBackRange(0, frames - 1)
        doc.setCurrentTime(0)
        node = doc.activeNode()
        if node is not None:
            node.setName(("Camera: %s" % move)[:120])
        _refresh(doc)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return {"move": move, "frames": frames, "fps": fps, "size": [ow, oh],
            "layer": node_info(doc.activeNode()) if doc.activeNode() else None,
            "next": "render_video(path='shot.mp4') to export it"}
