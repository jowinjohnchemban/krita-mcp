"""Gemini model choice that survives Google retiring models.

No model name is hard-coded and nothing is cached: when no model was chosen, the models the key can use
are listed (ListModels) every time and the newest of the right family is picked. If Google answers that a
model is gone and names a replacement ("... use models/X ..."), the call is retried with it.
"""
import re

from . import http
from .http import ProviderError

FAMILIES = {
    # family: (name pattern with the version as group 1, generation method)
    "chat": (r"gemini-(\d+(?:\.\d+)?)-flash", "generateContent"),
    "image": (r"gemini-(\d+(?:\.\d+)?)-flash-image(?:-preview)?", "generateContent"),
    "video": (r"veo-(\d+(?:\.\d+)?)-(?:fast-)?generate(?:-preview|-\d+)?", "predictLongRunning"),
}
LABEL = {"chat": "Gemini Flash model", "image": "Gemini image model", "video": "Veo video model"}


def list_models(base, key):
    """Every model the key can use, fetched fresh."""
    models, token = [], None
    for _ in range(10):  # pages
        url = "%s/models?pageSize=1000%s" % (base, "&pageToken=" + token if token else "")
        r = http.get_json(url, {"x-goog-api-key": key}, timeout=20)
        models += r.get("models") or []
        token = r.get("nextPageToken")
        if not token:
            break
    return models


def newest(models, family):
    pattern, method = FAMILIES[family]
    best = None
    for m in models:
        name = (m.get("name") or "").split("/", 1)[-1]
        mt = re.fullmatch(pattern, name)
        if not mt or method not in (m.get("supportedGenerationMethods") or [method]):
            continue
        version = tuple(int(p) for p in mt.group(1).split("."))
        if best is None or version > best[0]:
            best = (version, name)
    return best[1] if best else None


def pick(base, key, family):
    """The newest available model of a family (listed now)."""
    try:
        name = newest(list_models(base, key), family)
    except ProviderError as e:
        raise ProviderError("could not list Gemini models (%s); choose a model by name" % e) from None
    if not name:
        raise ProviderError("no %s is available to this key; choose a model by name" % LABEL[family])
    return name


def replacement(error):
    """The model Google suggests in a 'model is no longer available' error, if any."""
    text = str(error)
    m = re.search(r"use models/([A-Za-z0-9.\-_]+)", text)
    return m.group(1) if m and ("404" in text or "no longer available" in text) else None


def post(base, key, model, method, body, timeout=600):
    """POST models/<model>:<method>; if Google says the model is gone and names a replacement, retry with it.
    Returns (response, the model actually used)."""
    headers = {"x-goog-api-key": key}
    try:
        return http.post_json("%s/models/%s:%s" % (base, model, method), body, headers, timeout=timeout), model
    except ProviderError as e:
        new = replacement(e)
        if not new or new == model:
            raise
        return http.post_json("%s/models/%s:%s" % (base, new, method), body, headers, timeout=timeout), new
