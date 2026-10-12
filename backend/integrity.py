"""Tamper check for the «عن التطبيق» page (Ibrahim 2026-10-12: «طبق ٣ فقط»).

Nothing is blocked: the library owns the computer and its files. When the about page (its markup
and its CSS in admin/index.html, between the about:start / about:end markers) or the developer's
logo file no longer matches this version of the app, the inbox file staff send to Ibrahim says so
at the top. An update package restores the original files.

When Ibrahim himself changes the about page or the logo, refresh EXPECTED with
    python3 -c "import integrity; print(integrity.fingerprints())"   (run inside backend/)
tests/test_integrity.py fails until it is refreshed, so the change cannot be forgotten.
"""
import hashlib
import os
import re

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ADMIN = os.path.join(ROOT, "admin", "index.html")
LOGO = os.path.join(ROOT, "frontend", "assets", "developer-logo.png")

EXPECTED = {
    "about_page": "35280d18516184d75af0f5f0420c3a07ba9ef2d5981081f1bcad0330072e8c72",
    "about_css": "4295f32ad82f86454080c7729abcc25e5b36ebaeda53181d0ca6b18864fbf5b1",
    "developer_logo": "6501f71edaec4e96bd3011f7ddd245b1be721c340f2a36b8f2a46ba878957ceb",
}
LABELS = {"about_page": "محتوى صفحة «عن التطبيق»", "about_css": "تنسيق صفحة «عن التطبيق»",
          "developer_logo": "شعار المطوّر (إبراهيم)"}


def _block(text, start, end):
    i, j = text.find(start), text.find(end)
    if i < 0 or j < 0 or j < i:
        return None
    return re.sub(r"\s+", " ", text[i:j + len(end)]).strip()


def _sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def fingerprints():
    """-> {name: sha256 or None when the part is missing}"""
    try:
        html = open(ADMIN, encoding="utf-8").read()
    except OSError:
        html = ""
    page = _block(html, "<!-- about:start -->", "<!-- about:end -->")
    css = _block(html, "/* about:css:start", "/* about:css:end */")
    try:
        logo = open(LOGO, "rb").read()
    except OSError:
        logo = None
    return {"about_page": _sha(page) if page else None, "about_css": _sha(css) if css else None,
            "developer_logo": _sha(logo) if logo else None}


def check():
    """-> list of Arabic descriptions of what changed or went missing (empty = intact)."""
    now = fingerprints()
    out = []
    for key, want in EXPECTED.items():
        got = now.get(key)
        if got is None:
            out.append(f"{LABELS[key]}: محذوف أو ما ينقرأ")
        elif got != want:
            out.append(f"{LABELS[key]}: تغيّر عن نسخة التطبيق")
    return out
