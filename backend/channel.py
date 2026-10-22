"""Updates and messages sent from Ibrahim's side, installed at the library with one approval (AUTOUPD).

Ibrahim (2026-10-16): «ابغى طريقة يصير حتى التحديث انا ارسله من عندي وهم يجيهم رسالة مجرد الموافقة عليها يتم التحديث»
— approved the same evening («approve»).

The channel is one release of the project's GitHub repository (tag `manar-channel`). Its file `manar-channel.json` is
signed on Ibrahim's Mac (scripts/channel_publish.py, Ed25519, backend/ed25519.py); this app holds only the public key
(backend/channel_key.py), so nobody else can send it an update or a message. A read-only token is used when the
repository is private (data/channel.json, never in git).

  every hour (and on «تحقق الآن»)   the channel file is read and its signature checked; its messages join
                                    «ملاحظات من المطور»; a newer version shows in «التنبيهات» with «موافقة وتحديث»
  «موافقة وتحديث» (manager)         the update file is downloaded, its size and SHA-256 checked against the signed
                                    file, unpacked, and tried with `apply_update.py --dry-run`; then
                                    data/update_pending.json is written and the server stops itself
  scripts/run_server.py             (the start file's loop) sees the pending update, runs scripts/auto_update.py
                                    (apply_update.py: backup, merge, code swap, rollback on any failure), and starts
                                    the server again
  next start                        data/update_result.json is read: «تم التحديث» or «فشل… ورجعنا للنسخة السابقة»,
                                    written to the audit log and shown in «التنبيهات»
Standard library only.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone

import ed25519
import net

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DATA = os.path.join(ROOT, "data")
CONFIG = os.path.join(DATA, "channel.json")
STATE = os.path.join(DATA, "channel_state.json")
PENDING = os.path.join(DATA, "update_pending.json")
RESULT = os.path.join(DATA, "update_result.json")
UPDATES = os.path.join(ROOT, "updates")
DEFAULTS = {"repo": "eyesmystery/library-keeper", "tag": "manar-channel", "asset": "manar-channel.json",
            "api": "https://api.github.com", "check_minutes": 60, "enabled": True, "token": None}
FORMAT = "manar-channel-1"
MAX_MANIFEST = 512 * 1024
MAX_UPDATE = 1024 * 1024 * 1024
_lock = threading.Lock()
_worker = {"thread": None}
_timer = {"thread": None}


class ChannelError(Exception):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def config():
    c = dict(DEFAULTS)
    c.update({k: v for k, v in (_read_json(CONFIG, {}) or {}).items() if k in DEFAULTS})
    return c


def public_key():
    try:
        import channel_key
        return bytes.fromhex(channel_key.PUBLIC_KEY.strip()) if channel_key.PUBLIC_KEY.strip() else None
    except (ImportError, ValueError):
        return None


def installed_version():
    try:
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def version_key(v):
    """«0.9.10 beta» -> (0, 9, 10): 0.9.10 is newer than 0.9.9."""
    return tuple(int(x) for x in re.findall(r"\d+", str(v or "")))


def state():
    return _read_json(STATE, {}) or {}


def _save_state(**changes):
    with _lock:
        s = state()
        s.update(changes)
        _write_json(STATE, s)
        return s


def _set_update(**changes):
    with _lock:
        s = state()
        up = dict(s.get("update") or {})
        up.update(changes)
        s["update"] = up
        _write_json(STATE, s)
        return up


# ------------------------------------------------------------------ HTTP (GitHub releases)
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _headers(cfg, accept="application/vnd.github+json"):
    h = {"Accept": accept, "User-Agent": "Manar-library-app", "X-GitHub-Api-Version": "2022-11-28"}
    if cfg.get("token"):
        h["Authorization"] = "Bearer " + str(cfg["token"]).strip()
    return h


def _open(url, headers, timeout=30, follow=True):
    req = urllib.request.Request(url, headers=headers)
    handlers = [urllib.request.HTTPSHandler(context=net.ssl_context())]
    if not follow:
        handlers.append(_NoRedirect())
    return urllib.request.build_opener(*handlers).open(req, timeout=timeout)


def _release(cfg):
    url = f"{cfg['api'].rstrip('/')}/repos/{cfg['repo']}/releases/tags/{cfg['tag']}"
    try:
        with _open(url, _headers(cfg), timeout=20) as r:
            return json.loads(r.read(MAX_MANIFEST).decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ChannelError("القناة غير موجودة بعد" if e.code == 404 else f"GitHub رد بالرمز {e.code}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ChannelError("تعذر الاتصال بالإنترنت أو بـ GitHub: " + str(getattr(e, "reason", e))[:120])


def _asset_stream(cfg, asset, timeout=60):
    """An open response for a release asset (private repos: the API URL with the token, then the storage URL
    without it — the storage refuses a request that still carries the token)."""
    if cfg.get("token"):
        try:
            r = _open(asset["url"], _headers(cfg, "application/octet-stream"), timeout=timeout, follow=False)
            return r   # some servers answer directly
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                return _open(e.headers["Location"], {"User-Agent": "Manar-library-app"}, timeout=timeout)
            raise ChannelError(f"تعذر تنزيل الملف (GitHub {e.code})")
    return _open(asset.get("browser_download_url") or asset["url"],
                 {"User-Agent": "Manar-library-app", "Accept": "application/octet-stream"}, timeout=timeout)


# ------------------------------------------------------------------ the signed file
def read_manifest(raw, key=None):
    """raw bytes of manar-channel.json -> the verified payload (dict). Raises ChannelError."""
    key = key if key is not None else public_key()
    if not key:
        raise ChannelError("مفتاح التحقق غير موجود في هذا الإصدار")
    try:
        doc = json.loads(raw.decode("utf-8"))
        payload_text, sig = doc["payload"], bytes.fromhex(doc["signature"])
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ChannelError("ملف القناة تالف")
    if doc.get("format") != FORMAT:
        raise ChannelError("صيغة ملف القناة غير معروفة")
    if not ed25519.verify(key, payload_text.encode("utf-8"), sig):
        raise ChannelError("توقيع ملف القناة غير صحيح — لم يُقبل")
    try:
        payload = json.loads(payload_text)
    except ValueError:
        raise ChannelError("ملف القناة تالف")
    if not isinstance(payload, dict) or not isinstance(payload.get("seq"), int):
        raise ChannelError("ملف القناة تالف")
    return payload


def check(fetch=None):
    """Read the channel once. -> state. fetch: for tests, a function returning (release json, manifest bytes)."""
    cfg = config()
    if not cfg.get("enabled"):
        return _save_state(last_check=_now(), last_error="قناة التحديثات متوقفة في هذا الجهاز")
    try:
        if fetch:
            rel, raw = fetch()
        else:
            rel = _release(cfg)
            asset = next((a for a in rel.get("assets") or [] if a.get("name") == cfg["asset"]), None)
            if not asset:
                raise ChannelError("ملف القناة غير موجود في الإصدار")
            with _asset_stream(cfg, asset, timeout=30) as r:
                raw = r.read(MAX_MANIFEST + 1)
            if len(raw) > MAX_MANIFEST:
                raise ChannelError("ملف القناة أكبر من المسموح")
        payload = read_manifest(raw)
    except ChannelError as e:
        return _save_state(last_check=_now(), last_error=str(e))
    s = state()
    if payload["seq"] < int(s.get("seq") or 0):
        return _save_state(last_check=_now(), last_error="وصل ملف قناة أقدم من آخر ملف مقبول — تجاهلناه")
    added = 0
    try:
        import dev_notes
        msgs = [m for m in payload.get("messages") or [] if isinstance(m, dict)]
        added = dev_notes.add_notes(msgs)
    except Exception as e:  # noqa: BLE001 - messages never block the update part
        _save_state(messages_error=str(e)[:200])
    up = payload.get("update") if isinstance(payload.get("update"), dict) else None
    cur = dict(s.get("update") or {})
    busy = cur.get("status") in ("downloading", "checking", "restarting", "ready")
    if up and up.get("version") and version_key(up["version"]) > version_key(installed_version()):
        same = cur.get("version") == up["version"] and cur.get("sha256") == up.get("sha256")
        if not same:
            cur = {"status": "available"}
        elif not busy and cur.get("status") != "failed":
            cur["status"] = "available"
        cur.update({k: up.get(k) for k in ("version", "asset", "sha256", "size", "notes", "published")})
    elif not busy and cur.get("status") not in ("installed", "failed"):
        cur = {"status": "none"}
    return _save_state(last_check=_now(), last_ok=_now(), last_error=None, seq=payload["seq"],
                       issued=payload.get("issued"), update=cur, messages_added=added)


# ------------------------------------------------------------------ install (after the manager's approval)
def _safe_extract(zpath, dest):
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/") or re.match(r"^[A-Za-z]:", name):
                raise ChannelError("ملف التحديث فيه مسار غير آمن")
        z.extractall(dest)
    for base, dirs, files in os.walk(dest):
        if "apply_update.py" in files and os.path.isdir(os.path.join(base, "payload")):
            return base
    raise ChannelError("ملف التحديث ناقص (لا يوجد apply_update.py)")


def _download(cfg, up, progress):
    os.makedirs(os.path.join(UPDATES, "_download"), exist_ok=True)
    rel = _release(cfg)
    asset = next((a for a in rel.get("assets") or [] if a.get("name") == up.get("asset")), None)
    if not asset:
        raise ChannelError("ملف التحديث غير موجود في القناة")
    size = int(up.get("size") or 0)
    if not size or size > MAX_UPDATE or (asset.get("size") and int(asset["size"]) != size):
        raise ChannelError("حجم ملف التحديث لا يطابق الملف الموقّع")
    path = os.path.join(UPDATES, "_download", os.path.basename(up["asset"]))
    h, got = hashlib.sha256(), 0
    with _asset_stream(cfg, asset, timeout=120) as r, open(path + ".part", "wb") as f:
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            got += len(chunk)
            if got > size:
                raise ChannelError("ملف التحديث أكبر من الملف الموقّع")
            h.update(chunk)
            f.write(chunk)
            progress(got, size)
    if got != size or h.hexdigest() != str(up.get("sha256") or "").lower():
        os.remove(path + ".part")
        raise ChannelError("بصمة ملف التحديث لا تطابق الملف الموقّع — لم يُثبَّت")
    os.replace(path + ".part", path)
    return path


def _install(by, download=None, dry_run=None, exit_after=True):
    cfg = config()
    up = dict(state().get("update") or {})
    try:
        _set_update(status="downloading", progress=0, message="جاري تنزيل التحديث…", by=by, started=_now())
        def progress(got, total):
            pct = int(got * 100 / total) if total else 0
            if pct != (state().get("update") or {}).get("progress"):
                _set_update(progress=pct)
        zpath = (download or _download)(cfg, up, progress)
        _set_update(status="checking", message="جاري فحص التحديث قبل تثبيته…")
        tag = re.sub(r"[^0-9A-Za-z._-]", "_", str(up.get("version") or "update"))
        dest = os.path.join(UPDATES, tag)
        if os.path.isdir(dest):
            shutil.rmtree(dest, ignore_errors=True)
        upd = _safe_extract(zpath, dest)
        try:
            os.remove(zpath)
        except OSError:
            pass
        rc, out = (dry_run or _dry_run)(upd)
        if rc != 0:
            raise ChannelError("الفحص التجريبي للتحديث فشل، ولم يتغير شيء: " + out[-400:])
        _write_json(PENDING, {"version": up.get("version"), "update_dir": upd, "by": by, "at": _now(),
                              "from_version": installed_version()})
        launcher = os.environ.get("LIBRARY_KEEPER_LAUNCHER") == "1"
        if launcher:
            _set_update(status="restarting", message="التحديث جاهز. السيرفر يُعاد تشغيله الآن لتثبيته (دقيقة أو دقيقتين)…")
            if exit_after:
                threading.Timer(3.0, lambda: os._exit(0)).start()
        else:
            _set_update(status="ready", message="التحديث جاهز. أغلق نافذة السيرفر ثم شغّل التطبيق من start_library ليُثبَّت.")
    except Exception as e:  # noqa: BLE001
        _set_update(status="failed", message=str(e)[:500], finished=_now())


def _dry_run(upd):
    py = sys.executable
    p = subprocess.run([py, os.path.join(upd, "apply_update.py"), "--app", ROOT, "--update", upd, "--dry-run"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def install(by, **kw):
    """Start the approved install in the background. -> (ok, message)."""
    up = state().get("update") or {}
    if up.get("status") not in ("available", "failed"):
        return False, "لا يوجد تحديث ينتظر الموافقة"
    if not up.get("version") or version_key(up["version"]) <= version_key(installed_version()):
        return False, "لا يوجد تحديث أحدث من الإصدار المثبت"
    if _worker["thread"] and _worker["thread"].is_alive():
        return False, "التحديث يعمل الآن"
    t = threading.Thread(target=_install, args=(by,), kwargs=kw, daemon=True)
    _worker["thread"] = t
    t.start()
    return True, "بدأ تنزيل التحديث"


# ------------------------------------------------------------------ after a restart
def after_start(audit=None):
    """Read data/update_result.json (written by scripts/auto_update.py) once. -> the result or None."""
    res = _read_json(RESULT)
    if not res:
        return None
    ok = bool(res.get("ok"))
    _set_update(status="installed" if ok else "failed", finished=res.get("at"), version=res.get("version"),
                message=(f"تم التحديث إلى {res.get('version')}." if ok else
                         "فشل تثبيت التحديث ورجعنا للنسخة السابقة كما كانت. " + str(res.get("error") or "")[:300]),
                log=res.get("log"), report=res.get("report_file"))
    _save_state(last_result=res)
    if audit:
        try:
            audit(None, "update.installed" if ok else "update.failed", res.get("version"),
                  {"from": res.get("from_version"), "by": res.get("by"), "log": res.get("log"), "error": res.get("error")})
        except Exception:  # noqa: BLE001
            pass
    os.makedirs(UPDATES, exist_ok=True)
    try:
        os.replace(RESULT, os.path.join(UPDATES, "update_result_" + time.strftime("%Y%m%d-%H%M%S") + ".json"))
    except OSError:
        pass
    return res


def status(role=None):
    cfg, s = config(), state()
    up = dict(s.get("update") or {})
    return {"enabled": bool(cfg.get("enabled")), "has_key": bool(public_key()), "private": bool(cfg.get("token")),
            "installed_version": installed_version(), "last_check": s.get("last_check"), "last_ok": s.get("last_ok"),
            "last_error": s.get("last_error"), "update": up, "launcher": os.environ.get("LIBRARY_KEEPER_LAUNCHER") == "1",
            "can_install": role == "manager", "pending": os.path.exists(PENDING)}


def start_background():
    """The hourly check (started by app.py when the server starts)."""
    if _timer["thread"] or not config().get("enabled"):
        return
    def loop():
        time.sleep(45)
        while True:
            try:
                check()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(max(10, int(config().get("check_minutes") or 60)) * 60)
    t = threading.Thread(target=loop, daemon=True)
    _timer["thread"] = t
    t.start()
