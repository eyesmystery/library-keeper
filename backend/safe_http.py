"""Outgoing HTTP(S) to public addresses only, with the address checked being the address connected to.

AUDIT 2026-10-30 P2 (Codex daily audit): page_extract.safe_get and covers.http_get checked the host's address with
getaddrinfo and then let urllib open its own connection, which resolved the name again. A name that resolves to a
public address for the check and to an internal one for the connection reached the internal service. Here the
connection is made to the very address that passed the check (TLS still verifies the certificate for the host name,
SNI is the host name), and the connected peer is checked again before anything is sent. No proxy is used.
Standard library only."""
import http.client
import ipaddress
import socket
import urllib.error
import urllib.request


class Blocked(urllib.error.URLError):
    """The address is not a public one."""


def _is_public(ip):
    try:
        return ipaddress.ip_address(str(ip).split("%")[0]).is_global
    except ValueError:
        return False


def resolve_public(host, port):
    """-> one public IP for host. Raises Blocked when any address the name resolves to is not public."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise urllib.error.URLError(f"unknown host: {e}")
    ips = [i[4][0] for i in infos]
    if not ips or not all(_is_public(ip) for ip in ips):
        raise Blocked("internal address")
    return ips[0]


def _connect(host, port, timeout, source_address):
    ip = resolve_public(host, port)
    sock = socket.create_connection((ip, port), timeout, source_address)
    try:
        peer = sock.getpeername()[0]
    except OSError:
        peer = ip
    if not _is_public(peer):
        sock.close()
        raise Blocked("internal address")
    return sock


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = _connect(self.host, self.port, self.timeout, self.source_address)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        sock = _connect(self.host, self.port, self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PinnedHTTPConnection, req)


class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, context=None):
        super().__init__(context=context)
        self._ctx = context

    def https_open(self, req):
        return self.do_open(_PinnedHTTPSConnection, req, context=self._ctx)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None          # a 3xx comes back as HTTPError; the caller checks the next address before asking it


def opener(context):
    """An opener that follows no redirect, uses no proxy, and connects only to the public address it checked."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect, PinnedHTTPHandler(),
                                       PinnedHTTPSHandler(context=context))
