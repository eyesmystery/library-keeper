"""[AUDFIX 2026-10-29 evening, Codex audit P2] «اجلب من الرابط»: a public page that redirects to an internal address.
Before, urllib followed the redirect and only the final address was checked, after the internal one had been asked.
Now page_extract.safe_get checks every hop before asking it. No network: DNS and the opener are faked."""
import os, sys, io, socket, email.message, urllib.error, urllib.request
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import page_extract as PE
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

ADDR = {"shop.example.com": "93.184.216.34", "cdn.example.com": "93.184.216.35", "evil.example.com": "127.0.0.1"}
real_gai = socket.getaddrinfo
def fake_gai(host, port, *a, **k):
    if host in ADDR:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ADDR[host], port))]
    if host.replace(".", "").isdigit():
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port))]
    raise socket.gaierror("unknown")
socket.getaddrinfo = fake_gai

asked = []
ROUTES = {}
class FakeResp(io.BytesIO):
    def __init__(self, url, body): super().__init__(body); self._u = url
    def geturl(self): return self._u
    def __enter__(self): return self
    def __exit__(self, *a): self.close()
class FakeOpener:
    def open(self, req, timeout=None):
        url = req.full_url; asked.append(url)
        kind, val = ROUTES[url]
        if kind == "redirect":
            h = email.message.Message(); h["Location"] = val
            raise urllib.error.HTTPError(url, 302, "Found", h, None)
        return FakeResp(url, val)
real_build = urllib.request.build_opener
urllib.request.build_opener = lambda *a, **k: FakeOpener()

PAGE = b"<html><head><title>x</title></head><body>" + b"a" * 2000 + b"</body></html>"
try:
    # 1. a public page redirecting to an internal address: the internal one is never asked
    ROUTES.clear(); asked.clear()
    ROUTES["https://shop.example.com/book"] = ("redirect", "http://evil.example.com/admin")
    ROUTES["http://evil.example.com/admin"] = ("page", PAGE)
    try:
        PE.extract("https://shop.example.com/book"); err = None
    except PE.PageError as e:
        err = str(e)
    ok(err is not None and "داخلي" in err, "redirect to an internal address refused with «عنوان داخلي»")
    ok(asked == ["https://shop.example.com/book"], "the internal address was never requested (asked: %s)" % asked)

    # 2. a redirect to a literal loopback IP
    ROUTES.clear(); asked.clear()
    ROUTES["https://shop.example.com/b2"] = ("redirect", "http://127.0.0.1:8802/api/admin/books")
    try:
        PE.safe_get("https://shop.example.com/b2"); err = None
    except PE.PageError as e:
        err = str(e)
    ok(err and len(asked) == 1, "redirect to 127.0.0.1 refused before asking it")

    # 3. a public → public redirect still works (relative Location too)
    ROUTES.clear(); asked.clear()
    ROUTES["https://shop.example.com/old"] = ("redirect", "https://cdn.example.com/new")
    ROUTES["https://cdn.example.com/new"] = ("redirect", "/final")
    ROUTES["https://cdn.example.com/final"] = ("page", PAGE)
    final, body = PE.safe_get("https://shop.example.com/old")
    ok(final == "https://cdn.example.com/final" and body == PAGE, "public redirects (absolute and relative) are followed")

    # 4. a redirect loop stops
    ROUTES.clear(); asked.clear()
    ROUTES["https://shop.example.com/loop"] = ("redirect", "https://shop.example.com/loop")
    try:
        PE.safe_get("https://shop.example.com/loop"); err = None
    except PE.PageError as e:
        err = str(e)
    ok(err is not None and len(asked) == PE.MAX_REDIRECTS + 1, "a redirect loop stops after %d hops" % (PE.MAX_REDIRECTS + 1))

    # 5. an internal address given directly is refused without any request
    ROUTES.clear(); asked.clear()
    try:
        PE.extract("http://evil.example.com/x"); err = None
    except PE.PageError as e:
        err = str(e)
    ok(err and asked == [], "an internal address given directly is never requested")
    ok(PE.extract.__defaults__ == (None,) and "safe_get" in PE.extract.__code__.co_names, "extract() uses safe_get by default")

    # 6. AGY-20261029-E1: covers.http_get (the cover download) checks every hop too
    import covers
    ROUTES.clear(); asked.clear()
    ROUTES["https://shop.example.com/c.jpg"] = ("redirect", "http://evil.example.com/c.jpg")
    try:
        covers.http_get("https://shop.example.com/c.jpg"); err = None
    except urllib.error.URLError as e:
        err = str(e)
    ok(err and "internal" in err and asked == ["https://shop.example.com/c.jpg"], "cover download: redirect to an internal address never requested")
    ROUTES.clear(); asked.clear()
    try:
        covers.http_get("http://127.0.0.1:8802/api/admin/x"); err = None
    except urllib.error.URLError as e:
        err = str(e)
    ok(err and asked == [], "cover download: an internal address is never requested")
    ROUTES["https://cdn.example.com/a.jpg"] = ("page", PAGE)
    ok(covers.http_get("https://cdn.example.com/a.jpg")[0] == "https://cdn.example.com/a.jpg", "cover download: a public address still works")
    # 7. a page whose cover points inside: no cover is offered
    ROUTES.clear(); asked.clear()
    ROUTES["https://shop.example.com/p"] = ("page", b'<html><head><meta property="og:image" content="http://evil.example.com/x.jpg"><title>t</title></head><body>' + b"a" * 2000 + b"</body></html>")
    ok(PE.extract("https://shop.example.com/p")["cover"] is None, "a page's internal cover address is not offered")
finally:
    socket.getaddrinfo = real_gai
    urllib.request.build_opener = real_build

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
