"""Free asset libraries (no account needed):

Poly Haven  - CC0 photo textures (rock, wood, fabric, skin, ground...) and HDRI skies (as photographic panoramas).
Openverse   - openly licensed photos (Creative Commons / public domain) with attribution text, for reference
              or compositing.
"""
import urllib.parse

from . import http
from .http import ProviderError

UA = {"User-Agent": "krita-mcp (https://github.com/jowinjohnchemban/krita-mcp)"}
POLYHAVEN_URL = "https://api.polyhaven.com"
OPENVERSE_URL = "https://api.openverse.org/v1"


def _ph():
    return http.env("KRITA_MCP_POLYHAVEN_URL", POLYHAVEN_URL).rstrip("/")


def _ov():
    return http.env("KRITA_MCP_OPENVERSE_URL", OPENVERSE_URL).rstrip("/")
KINDS = {"textures": "textures", "texture": "textures", "hdris": "hdris", "hdri": "hdris", "skies": "hdris",
         "photos": "photos", "photo": "photos", "reference": "photos"}


def _get(url):
    return http.get_json(url, UA, timeout=30)


def search(query="", kind="textures", limit=12, licence=None):
    kind = KINDS.get(kind, kind)
    limit = max(1, min(int(limit), 40))
    words = [w for w in query.lower().replace(",", " ").split() if w]
    if kind in ("textures", "hdris"):
        assets = _get("%s/assets?t=%s" % (_ph(), kind))

        def score(item):
            a = item[1]
            hay = " ".join([item[0], a.get("name", "")] + a.get("tags", []) + a.get("categories", [])).lower()
            return sum(w in hay for w in words)
        ranked = sorted(assets.items(), key=lambda it: (-score(it), it[0]))
        if words:
            ranked = [it for it in ranked if score(it) > 0]
        return {"source": "polyhaven", "kind": kind, "licence": "CC0 (no attribution required)", "results": [
            {"id": k, "name": a.get("name"), "tags": a.get("tags", [])[:8], "categories": a.get("categories", [])[:5],
             "authors": list((a.get("authors") or {}).keys())} for k, a in ranked[:limit]]}
    if kind == "photos":
        q = {"q": query or "texture", "page_size": limit, "mature": "false"}
        if licence:
            q["license"] = licence  # e.g. cc0,pdm  or  by,by-sa
        data = _get("%s/images/?%s" % (_ov(), urllib.parse.urlencode(q)))
        return {"source": "openverse", "kind": "photos", "results": [
            {"id": r["id"], "name": r.get("title"), "creator": r.get("creator"),
             "licence": "%s %s" % (r.get("license", "").upper(), r.get("license_version") or ""),
             "size": [r.get("width"), r.get("height")], "attribution": r.get("attribution"),
             "page": r.get("foreign_landing_url")} for r in data.get("results", [])]}
    raise ProviderError("kind must be textures, hdris or photos")


def fetch(asset_id, kind="textures", resolution="2k", texture_map="diffuse"):
    """(image bytes, name, attribution) for a search result."""
    kind = KINDS.get(kind, kind)
    if kind in ("textures", "hdris"):
        files = _get("%s/files/%s" % (_ph(), urllib.parse.quote(asset_id)))
        info = _get("%s/info/%s" % (_ph(), urllib.parse.quote(asset_id)))
        name = info.get("name") or asset_id
        if kind == "hdris":
            url = (files.get("tonemapped") or {}).get("url")
            if not url:
                raise ProviderError("no photographic preview for HDRI %r" % asset_id)
        else:
            maps = {"diffuse": "Diffuse", "normal": "nor_gl", "roughness": "Rough", "ao": "AO",
                    "displacement": "Displacement"}
            key = maps.get(texture_map, texture_map)
            res = files.get(key) or {}
            pick = res.get(resolution) or res.get("2k") or res.get("1k") or next(iter(res.values()), {})
            url = (pick.get("jpg") or pick.get("png") or {}).get("url")
            if not url:
                raise ProviderError("texture %r has no %s map (maps: %s)" % (asset_id, texture_map, ", ".join(files)))
        return http.download(url, headers=UA), name, "Poly Haven: %s (CC0)" % name
    if kind == "photos":
        r = _get("%s/images/%s/" % (_ov(), urllib.parse.quote(asset_id)))
        if r.get("mature"):
            raise ProviderError("that image is marked as mature content")
        return http.download(r["url"], headers=UA), r.get("title") or asset_id, r.get("attribution")
    raise ProviderError("kind must be textures, hdris or photos")
