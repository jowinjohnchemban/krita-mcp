"""Download an image URL with SSRF protection (public hosts only, no redirects, size-capped)."""
import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 25 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _check_host(host: str):
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ValueError("cannot resolve %s: %s" % (host, e))
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError("refusing to fetch from non-public address %s" % ip)


def fetch_image(url: str) -> bytes:
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("only http(s) URLs are allowed")
    _check_host(u.hostname)
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "krita-mcp"})
    try:
        with opener.open(req, timeout=30) as r:
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if not ctype.startswith("image/"):
                raise ValueError("URL did not return an image (Content-Type %r)" % ctype)
            data = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            raise ValueError("URL redirects; pass the final image URL (redirects are not followed)")
        raise ValueError("HTTP %d from server" % e.code)
    if len(data) > MAX_BYTES:
        raise ValueError("image larger than %d MB" % (MAX_BYTES // 2**20))
    return data
