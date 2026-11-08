"""Find something in a picture by description ("the sky", "the person on the left") and return where it is, for
select_by_description. Three ways, best first:

    gemini   Gemini's segmentation: a mask per object, pixel-accurate (needs a Gemini key)
    fal      a text-prompted segmentation model on fal.ai (needs a fal key; KRITAPILOT_FAL_SEGMENT_MODEL)
    vision   any vision chat model - including the free Pollinations ones - outlines it as polygons (coarser)

Result: {"masks": [{"box": [x0, y0, x1, y1], "png": bytes, "use_brightness": bool}], "polygons": [[[x, y], ...]],
"method": ...}, all coordinates as fractions (0..1) of the picture.
"""
import base64
import json
import re

from . import gemini, http, vision
from .http import ProviderError, env, secret
from .images import GEMINI_BASE

METHODS = ("gemini", "fal", "vision")


def _json(text):
    """The JSON in a model's answer (it may come in a ```json block, or with words around it)."""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    raw = m.group(1) if m else text
    pairs = sorted((("[", "]"), ("{", "}")), key=lambda se: raw.find(se[0]) if se[0] in raw else len(raw))
    for start, end in pairs:  # whichever bracket opens first holds the answer
        i, j = raw.find(start), raw.rfind(end)
        if 0 <= i < j:
            try:
                return json.loads(raw[i:j + 1])
            except ValueError:
                continue
    raise ProviderError("the model's answer had no usable JSON: %s" % text[:200])


def _clamp(v):
    return max(0.0, min(1.0, float(v)))


def with_gemini(png, what, model=None):
    key = secret("KRITAPILOT_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
    if not key:
        raise ProviderError("Gemini is not configured")
    base = env("KRITAPILOT_GEMINI_BASE_URL", GEMINI_BASE).rstrip("/")
    model = model or env("KRITAPILOT_GEMINI_SEGMENT_MODEL") or gemini.pick(base, key, "chat")
    prompt = ("Give the segmentation masks for: %s. Output a JSON list of segmentation masks where each entry "
              "contains the 2D bounding box in the key \"box_2d\", the segmentation mask in key \"mask\", and the "
              "text label in the key \"label\". If there is none, output []." % what)
    body = {"contents": [{"parts": [{"inline_data": {"mime_type": "image/png", "data": base64.b64encode(png).decode()}},
                                    {"text": prompt}]}]}
    r, model = gemini.post(base, key, model, "generateContent", body, timeout=120)
    parts = ((r.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    found = _json("".join(p.get("text", "") for p in parts))
    masks = []
    for item in found if isinstance(found, list) else []:
        box, mask = item.get("box_2d"), item.get("mask") or ""
        if not box or len(box) != 4:
            continue
        y0, x0, y1, x1 = [_clamp(v / 1000.0) for v in box]  # Gemini: [y0, x0, y1, x1], 0..1000
        if x1 <= x0 or y1 <= y0:
            continue
        data = mask.split(",", 1)[1] if mask.startswith("data:") else mask
        if data:
            masks.append({"box": [x0, y0, x1, y1], "png": base64.b64decode(data), "use_brightness": True,
                          "label": item.get("label")})
        else:  # no mask: the box itself
            masks.append({"box": [x0, y0, x1, y1], "png": None, "label": item.get("label")})
    return {"masks": masks, "polygons": [], "method": "gemini", "model": model}


def with_fal(png, what, model=None):
    from .images import Fal
    fal = Fal()
    if not fal.key:
        raise ProviderError("fal is not configured")
    model = model or env("KRITAPILOT_FAL_SEGMENT_MODEL", "fal-ai/evf-sam")
    out = fal.run(model, {"prompt": what, "image_url": http.data_uri(png), "mask_only": True})
    url = ((out.get("image") or {}).get("url") if isinstance(out.get("image"), dict) else None) or \
        next((i.get("url") for i in out.get("images") or [] if isinstance(i, dict)), None)
    if not url:
        raise ProviderError("fal returned no mask: %s" % str(out)[:200])
    return {"masks": [{"box": [0, 0, 1, 1], "png": http.download(url), "use_brightness": True}], "polygons": [],
            "method": "fal", "model": model}


def with_vision(png, what, width, height, provider=None, model=None):
    question = ("Find %s in this picture. Reply with JSON only, no other words: {\"polygons\": [[[x, y], ...], ...]} "
                "- one polygon per object, 12 to 40 points following its outline closely, x and y as fractions of "
                "the picture's width and height (0,0 = top-left, 1,1 = bottom-right). If it is not in the picture, "
                "reply {\"polygons\": []}." % what)
    text, meta = vision.ask(png, question, provider, model)
    found = _json(text)
    polys = found.get("polygons", []) if isinstance(found, dict) else found
    out = []
    for poly in polys or []:
        pts = [(p[0], p[1]) for p in poly if isinstance(p, (list, tuple)) and len(p) >= 2]
        if len(pts) < 3:
            continue
        if any(float(x) > 1.5 or float(y) > 1.5 for x, y in pts):  # the model answered in pixels
            pts = [(float(x) / max(1, width), float(y) / max(1, height)) for x, y in pts]
        out.append([[_clamp(x), _clamp(y)] for x, y in pts])
    return {"masks": [], "polygons": out, "method": "vision:" + meta.get("provider", "?"), "model": meta.get("model")}


def segment(png, what, width, height, method="auto", provider=None, model=None):
    """Find `what` in `png` (width x height); see the module notes. method: auto, gemini, fal or vision."""
    if method not in ("auto",) + METHODS:
        raise ProviderError("method must be auto, gemini, fal or vision")
    order = METHODS if method == "auto" else (method,)
    tried = []
    for m in order:
        try:
            if m == "gemini":
                if method == "auto" and not secret("KRITAPILOT_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
                    continue
                res = with_gemini(png, what, model)
            elif m == "fal":
                if method == "auto" and not secret("KRITAPILOT_FAL_KEY", "FAL_KEY"):
                    continue
                res = with_fal(png, what, model)
            else:
                res = with_vision(png, what, width, height, provider, model)
        except ProviderError as e:
            if method != "auto":
                raise
            tried.append("%s: %s" % (m, str(e)[:160]))
            continue
        if res["masks"] or res["polygons"] or method != "auto":
            res["tried"] = tried
            return res
        tried.append("%s: found nothing" % m)
    raise ProviderError("could not find %r in the picture (%s)" % (what, "; ".join(tried) or "no method available"))
