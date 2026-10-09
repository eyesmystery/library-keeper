"""TLS certificates for outgoing HTTPS (covers, AI providers) — no extra dependency.

The python.org installer for macOS ships Python without a CA bundle until "Install
Certificates.command" is run, so every HTTPS call fails with CERTIFICATE_VERIFY_FAILED
(seen on Ibrahim's Mac, 2026-09-28). We use the system/default store when it has CA
certificates, otherwise the first bundle that exists: macOS /etc/ssl/cert.pem, common
Linux bundles, then the certifi bundle that pip vendors inside the venv.
Verification is never switched off.
"""
import glob
import os
import ssl
import sys

_CANDIDATES = ["/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"]
_ctx = None


def _pip_certifi():
    base = os.path.dirname(os.path.dirname(os.__file__))
    pats = [os.path.join(sys.prefix, "lib", "python*", "site-packages", "pip", "_vendor", "certifi", "cacert.pem"),
            os.path.join(sys.prefix, "Lib", "site-packages", "pip", "_vendor", "certifi", "cacert.pem"),
            os.path.join(base, "site-packages", "pip", "_vendor", "certifi", "cacert.pem")]
    for p in pats:
        for hit in glob.glob(p):
            return hit
    return None


def ssl_context():
    global _ctx
    if _ctx is not None:
        return _ctx
    ctx = ssl.create_default_context()
    if not ctx.cert_store_stats().get("x509_ca"):
        for path in _CANDIDATES + [_pip_certifi()]:
            if path and os.path.isfile(path):
                try:
                    ctx.load_verify_locations(cafile=path)
                except (ssl.SSLError, OSError):
                    continue
                if ctx.cert_store_stats().get("x509_ca"):
                    break
    _ctx = ctx
    return ctx
