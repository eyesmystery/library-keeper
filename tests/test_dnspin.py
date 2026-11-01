"""[AUDIT 2026-10-30 P2] The page and cover fetches connect to the very address that passed the public check
(backend/safe_http.py), so a name that answers public for the check and internal for the connection never reaches the
internal service. No network: DNS and the socket are faked."""
import os, sys, socket, ssl
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import urllib.error
import safe_http, net, covers, page_extract as PE
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

calls = {"gai": 0}
real_gai, real_cc = socket.getaddrinfo, socket.create_connection
def flip_gai(host, port, *a, **k):
    """public on the 1st lookup, loopback afterwards — the rebinding the audit reproduced"""
    calls["gai"] += 1
    ip = "93.184.216.34" if calls["gai"] == 1 else "127.0.0.1"
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
connected = []
class FakeSock:
    def __init__(self, addr): self.addr = addr
    def getpeername(self): return self.addr
    def close(self): pass
def fake_cc(addr, timeout=None, source_address=None):
    connected.append(addr[0]); raise ConnectionRefusedError("stop here (test)")
try:
    socket.getaddrinfo, socket.create_connection = flip_gai, fake_cc
    # 1. the connection goes to the address that was checked, never to a second lookup
    calls["gai"] = 0; connected.clear()
    try:
        safe_http._connect("rebind.example.com", 443, 5, None)
    except ConnectionRefusedError:
        pass
    ok(connected == ["93.184.216.34"] and calls["gai"] == 1, "one lookup, and the socket opens to the checked public address")
    # 2. a name with any internal address is refused before any connection
    socket.getaddrinfo = lambda h, p, *a, **k: [(socket.AF_INET, 1, 6, "", ("93.184.216.34", p)), (socket.AF_INET, 1, 6, "", ("10.0.0.5", p))]
    connected.clear()
    try:
        safe_http._connect("mixed.example.com", 80, 5, None); r = "connected"
    except safe_http.Blocked:
        r = "blocked"
    except ConnectionRefusedError:
        r = "connected"
    ok(r == "blocked" and connected == [], "a name that also resolves inside is refused, no socket opened")
    # 3. the peer is checked again
    socket.getaddrinfo = lambda h, p, *a, **k: [(socket.AF_INET, 1, 6, "", ("93.184.216.34", p))]
    socket.create_connection = lambda addr, timeout=None, source_address=None: FakeSock(("127.0.0.1", addr[1]))
    try:
        safe_http._connect("peer.example.com", 80, 5, None); r = "connected"
    except safe_http.Blocked:
        r = "blocked"
    ok(r == "blocked", "a connection whose peer is internal is closed before anything is sent")
finally:
    socket.getaddrinfo, socket.create_connection = real_gai, real_cc

# 4. both fetches use the pinned opener, with no proxy
o = safe_http.opener(net.ssl_context())
kinds = {type(h).__name__ for h in o.handlers}
ok({"PinnedHTTPHandler", "PinnedHTTPSHandler", "NoRedirect"} <= kinds and "HTTPHandler" not in kinds and "HTTPSHandler" not in kinds,
   "the opener has only the pinned HTTP/HTTPS handlers and follows no redirect")
ph = [h for h in o.handlers if type(h).__name__ == "ProxyHandler"]
ok(all(not h.proxies for h in ph), "no proxy is used")
src_c = open(covers.__file__, encoding="utf-8").read(); src_p = open(PE.__file__, encoding="utf-8").read()
ok("safe_http.opener(net.ssl_context())" in src_c and "safe_http.opener(net.ssl_context())" in src_p, "covers.http_get and page_extract.safe_get use it")
ok(issubclass(safe_http.Blocked, urllib.error.URLError), "a refusal is a URLError, handled like any failed fetch")
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
