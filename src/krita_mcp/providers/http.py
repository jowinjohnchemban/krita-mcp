"""Small HTTP helpers shared by the AI providers (stdlib only)."""
import base64
import ipaddress
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid

MAX_DOWNLOAD = 512 * 1024 * 1024  # videos can be big


class ProviderError(RuntimeError):
    """A provider failed; the message is safe to show to the model (no secrets)."""


def env(name, default=None):
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else default


def secret(own, *generic):
    """API key from KRITA_MCP_<own>; generic names (OPENAI_API_KEY, ...) only with KRITA_MCP_USE_ENV_KEYS=1,
    so a key that happens to be in the environment never sends the user's art somewhere by surprise."""
    v = env(own)
    if v:
        return v
    from . import keys  # signed in from Krita's AI Chat > Services
    service = keys.ENV_TO_SERVICE.get(own)
    v = keys.saved(service) if service else None
    if v:
        return v
    if env("KRITA_MCP_USE_ENV_KEYS") in ("1", "true", "yes"):
        for g in generic:
            v = env(g)
            if v:
                return v
    return None


def service_url(env_name, service, default):
    """A server URL: environment variable, else the one saved in Krita's Services dialog, else the default."""
    from . import keys
    return (env(env_name) or keys.saved(service, "base_url") or default).rstrip("/")


def is_loopback_url(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _redact(url):
    u = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qsl(u.query)
    q = [(k, "***" if k.lower() in ("key", "api_key", "token") else v) for k, v in q]
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, urllib.parse.urlencode(q), ""))


_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # local backends: never via a proxy
_DEFAULT = urllib.request.build_opener()


def request(method, url, body=None, headers=None, timeout=600, raw=False):
    """Returns (bytes, headers). body: dict -> JSON, bytes -> as is. Errors -> ProviderError."""
    hdrs = {"User-Agent": "krita-mcp"}
    hdrs.update(headers or {})
    data = None
    if isinstance(body, (dict, list)):
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    elif body is not None:
        data = body
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    opener = _DIRECT if is_loopback_url(url) else _DEFAULT
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.read(MAX_DOWNLOAD + 1), r.headers
    except urllib.error.HTTPError as e:
        detail = e.read(4000).decode("utf-8", "replace")
        hint = ""
        if e.code in (401, 403):
            hint = (" -- the service rejected the API key saved for Krita: sign out and sign in again "
                    "(AI Chat > Services...) with a new key")
        raise ProviderError("%s %s -> HTTP %d: %s%s" % (method, _redact(url), e.code, detail[:600], hint)) from None
    except (urllib.error.URLError, OSError) as e:
        raise ProviderError("cannot reach %s (%s)" % (_redact(url), getattr(e, "reason", e))) from None


def get_json(url, headers=None, timeout=60):
    data, _ = request("GET", url, headers=headers, timeout=timeout)
    return _loads(data, url)


def post_json(url, body, headers=None, timeout=600):
    data, _ = request("POST", url, body=body, headers=headers, timeout=timeout)
    return _loads(data, url)


def _loads(data, url):
    try:
        return json.loads(data or b"null")
    except ValueError:
        raise ProviderError("%s did not return JSON: %r" % (_redact(url), data[:200])) from None


def multipart(fields, files):
    """fields: [(name, value)], files: [(name, filename, bytes, content_type)] -> (body, content_type)."""
    b = uuid.uuid4().hex
    out = []
    for k, v in fields:
        if v is None:
            continue
        out.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (b, k, v)).encode())
    for k, fname, data, ctype in files:
        out.append(('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
                    'Content-Type: %s\r\n\r\n' % (b, k, fname, ctype)).encode())
        out.append(data)
        out.append(b"\r\n")
    out.append(("--%s--\r\n" % b).encode())
    return b"".join(out), "multipart/form-data; boundary=" + b


def data_uri(png):
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _public(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ProviderError("cannot resolve %s: %s" % (host, e)) from None
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ProviderError("refusing to download from non-public address %s" % info[4][0])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def download(url, headers=None, timeout=600, allow_local=False):
    """Fetch a result URL a cloud provider gave us (data: URIs too). Public hosts only (each redirect hop is
    re-checked) unless allow_local (results served by the user's own local backend)."""
    if url.startswith("data:"):
        try:
            return base64.b64decode(url.split(",", 1)[1])
        except Exception:  # noqa: BLE001
            raise ProviderError("bad data: URI from provider") from None
    opener = urllib.request.build_opener(_NoRedirect)
    for _ in range(5):
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ProviderError("provider returned an unsupported URL")
        if not allow_local:
            _public(u.hostname)
        req = urllib.request.Request(url, headers=dict({"User-Agent": "krita-mcp"}, **(headers or {})))
        try:
            with opener.open(req, timeout=timeout) as r:
                data = r.read(MAX_DOWNLOAD + 1)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                url = urllib.parse.urljoin(url, e.headers["Location"])
                headers = None  # never forward credentials to another host
                continue
            raise ProviderError("download failed: HTTP %d from %s" % (e.code, u.hostname)) from None
        except (urllib.error.URLError, OSError) as e:
            raise ProviderError("download failed (%s)" % getattr(e, "reason", e)) from None
        if len(data) > MAX_DOWNLOAD:
            raise ProviderError("result larger than %d MB" % (MAX_DOWNLOAD // 2**20))
        return data
    raise ProviderError("too many redirects")


ASPECTS = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "9:21", "21:9"]


def nearest_aspect(w, h, choices=ASPECTS):
    r = w / float(h)

    def val(a):
        x, y = a.split(":")
        return float(x) / float(y)
    import math
    return min(choices, key=lambda a: abs(math.log(val(a) / r)))


def nearest_size(w, h, sizes):
    """Pick from ['1024x1024', ...] the size whose aspect is closest to w/h."""
    import math
    r = w / float(h)
    return min(sizes, key=lambda s: abs(math.log((int(s.split("x")[0]) / float(s.split("x")[1])) / r)))
