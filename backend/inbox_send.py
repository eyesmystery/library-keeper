"""The library's inbox sent to Ibrahim, sealed, every week and whenever he asks (INBOXSEND).

Ibrahim 2026-10-23: «تجارب الموظفين والزوار اللي تنحفظ في المكتبة.. هل في طريقة يصير كل اسبوع او وقت ما انا ابغى يصير
تنرسل لي عشان اعالجها؟» — he chose «رفع تلقائي مشفّر».

  what      the same Markdown file as «تنزيل البلاغات» (backend/inbox_export.py): visitor reports and suggestions,
            unanswered questions, staff edit requests and edits, staff suggestions, test-mode conversations
  sealed    with Ibrahim's public key (backend/inbox_key.py, backend/sealed.py): only his Mac opens it
  where     a file in the private repository eyesmystery/manar-inbox (GitHub contents API), written with an upload
            token limited to that repository; the manager pastes the token once in «التنبيهات», it is kept in
            data/inbox_send.json on this computer only (never in git, never in a package, never shown again)
  when      - every `every_days` days (7) while sending is on
            - when Ibrahim asks: a signed request in the update channel (backend/channel.py) is sent at the next
              hourly check, once per request id
            - «أرسل الآن» (manager)
  consent   off until the manager turns it on; the manager can turn it off at any time. Each send is in the audit log.
Standard library only.
"""
import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import safe_io
import net
import sealed

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DATA = os.path.join(ROOT, "data")
CONFIG = os.path.join(DATA, "inbox_send.json")
STATE = os.path.join(DATA, "inbox_send_state.json")
DEFAULTS = {"enabled": False, "token": None, "repo": "eyesmystery/manar-inbox", "folder": "inbox",
            "api": "https://api.github.com", "every_days": 7}
FORMAT = "manar-inbox-1"
MAX_FILE = 40 * 1024 * 1024
RIYADH = timezone(timedelta(hours=3))
_lock = threading.Lock()
_send_lock = threading.Lock()
_timer = {"thread": None}
_audit = {"fn": None}   # auth.audit, set by start_background()


class SendError(Exception):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, data, private=False):
    # CHANFIX (Windows, 2026-10-27): a temp file of its own and a replace that waits while the target is busy;
    # private: the upload token, readable by this user only (Windows ignores the mode)
    safe_io.write_json(path, data, private=private)


def config():
    c = dict(DEFAULTS)
    c.update({k: v for k, v in (_read_json(CONFIG, {}) or {}).items() if k in DEFAULTS})
    return c


def state():
    return _read_json(STATE, {}) or {}


def _save_state(**changes):
    with _lock:
        s = state()
        s.update(changes)
        _write_json(STATE, s)
        return s


def public_key():
    try:
        import inbox_key
        k = inbox_key.PUBLIC_KEY.strip()
        return bytes.fromhex(k) if len(k) == 64 else None
    except (ImportError, ValueError):
        return None


def _parse(iso):
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def next_due(cfg=None, s=None):
    cfg, s = cfg or config(), s if s is not None else state()
    last = _parse(s.get("last_sent"))
    if not last:
        return None
    return (last + timedelta(days=max(1, int(cfg.get("every_days") or 7)))).isoformat(timespec="seconds")


def ready(cfg=None):
    """-> None when this computer can send now, else why not (Arabic)."""
    cfg = cfg or config()
    if not public_key():
        return "مفتاح المطوّر غير موجود في هذا الإصدار؛ التحديث القادم يضيفه"
    if not cfg.get("enabled"):
        return "الإرسال متوقف — يشغّله مدير المكتبة"
    if not cfg.get("token"):
        return "رمز الرفع غير محفوظ — يحفظه مدير المكتبة"
    return None


def status(role=None):
    cfg, s = config(), state()
    return {"enabled": bool(cfg.get("enabled")), "has_token": bool(cfg.get("token")), "has_key": bool(public_key()),
            "every_days": int(cfg.get("every_days") or 7), "repo": cfg.get("repo"),
            "last_sent": s.get("last_sent"), "last_file": s.get("last_file"), "last_counts": s.get("last_counts"),
            "last_try": s.get("last_try"), "last_error": s.get("last_error"), "sending": bool(s.get("sending")),
            "next_due": next_due(cfg, s) if cfg.get("enabled") else None, "pending_request": s.get("pending_request"),
            "not_ready": ready(cfg), "can_manage": role in ("manager", "developer")}


def set_settings(enabled=None, token=None, clear_token=False):
    """The manager's settings. token: a new upload token (never returned). -> status()."""
    with _lock:
        c = dict(_read_json(CONFIG, {}) or {})
        if enabled is not None:
            c["enabled"] = bool(enabled)
        if clear_token:
            c["token"] = None
        elif token:
            t = str(token).strip()
            if len(t) < 20 or len(t) > 300 or any(ch.isspace() for ch in t):
                raise ValueError("الرمز غير صحيح — انسخه كاملًا كما أرسله المطوّر")
            c["token"] = t
        _write_json(CONFIG, c, private=True)
    return status("manager")


# ------------------------------------------------------------------ the file and the upload
def build_file(scope="open", reason="weekly", by=None, request_id=None):
    """-> (file name, sealed bytes, counts)."""
    import inbox_export
    key = public_key()
    if not key:
        raise SendError("مفتاح المطوّر غير موجود في هذا الإصدار")
    body, counts = inbox_export.build(scope, by or "إرسال تلقائي إلى المطوّر")
    try:
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
            version = f.read().strip()
    except OSError:
        version = None
    doc = {"format": FORMAT, "created": _now(), "reason": reason, "scope": scope, "request_id": request_id,
           "by": by, "version": version, "counts": counts, "markdown": body}
    blob = sealed.seal(key, json.dumps(doc, ensure_ascii=False).encode("utf-8"))
    if len(blob) > MAX_FILE:
        raise SendError("ملف البلاغات أكبر من المسموح")
    stamp = datetime.now(RIYADH).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{reason}.manar", blob, counts


def _put(cfg, name, blob):
    url = f"{cfg['api'].rstrip('/')}/repos/{cfg['repo']}/contents/{cfg['folder']}/{name}"
    body = json.dumps({"message": f"inbox {name}", "content": base64.b64encode(blob).decode("ascii")}).encode()
    req = urllib.request.Request(url, data=body, method="PUT", headers={
        "Accept": "application/vnd.github+json", "User-Agent": "Manar-library-app", "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": "Bearer " + str(cfg["token"]).strip(), "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=net.ssl_context()))
    try:
        with opener.open(req, timeout=120) as r:
            if r.status not in (200, 201):
                raise SendError(f"GitHub رد بالرمز {r.status}")
    except urllib.error.HTTPError as e:
        raise SendError({401: "رمز الرفع غير صالح أو انتهت مدته — اطلب رمزًا جديدًا من المطوّر",
                         403: "رمز الرفع لا يسمح بالكتابة في مستودع البلاغات",
                         404: "مستودع البلاغات غير موجود أو الرمز لا يصل إليه",
                         422: "ملف بنفس الاسم موجود — حاول بعد دقيقة"}.get(e.code, f"GitHub رد بالرمز {e.code}"))
    except (urllib.error.URLError, OSError) as e:
        raise SendError("تعذر الاتصال بالإنترنت أو بـ GitHub: " + str(getattr(e, "reason", e))[:120])


def _covers(sent_scope, wanted_scope):
    """A file sent with sent_scope holds everything a request for wanted_scope asks for ("all" ⊇ "open")."""
    return sent_scope == "all" or sent_scope == (wanted_scope or "open")


def send(reason="manual", scope="open", by=None, request_id=None, put=None, audit=None):
    """Build, seal and upload one file now. -> (ok, message). put: for tests, put(cfg, name, blob).
    AUDFIX (audit 2026-10-23 P2): a waiting request is marked done only by a file that carries its id and covers its
    scope. A manual or weekly send that covers it answers it (its id goes in the file); one that does not (open while
    "all" was asked) leaves it waiting for tick()."""
    cfg = config()
    why = ready(cfg)
    if why:
        if request_id:
            _save_state(pending_request={"id": request_id, "scope": scope, "at": _now()})
        return False, why
    if not _send_lock.acquire(blocking=False):
        return False, "الإرسال يعمل الآن"
    adopted = False
    try:
        _save_state(sending=True, last_try=_now())
        s0 = state()
        pend0 = s0.get("pending_request")
        if (not request_id and pend0 and pend0.get("id") and pend0["id"] not in (s0.get("requests_done") or [])
                and _covers(scope, pend0.get("scope"))):
            request_id, adopted = pend0["id"], True     # this file answers the waiting request too
        name, blob, counts = build_file(scope, reason, by, request_id)
        (put or _put)(cfg, name, blob)
        s = state()
        done = [x for x in (s.get("requests_done") or []) if x][-50:]
        pend = s.get("pending_request")
        short = bool(pend and request_id and pend.get("id") == request_id and not _covers(scope, pend.get("scope")))
        if request_id and not short and request_id not in done:
            done.append(request_id)
        if pend and request_id and pend.get("id") == request_id and not short:
            pend = None
        _save_state(sending=False, last_sent=_now(), last_file=name, last_counts=counts, last_error=None,
                    requests_done=done, pending_request=pend, last_reason=reason)
        audit = audit or _audit["fn"]
        if audit:
            try:
                audit(None, "inbox.sent", name, {"reason": reason, "scope": scope, "request": request_id,
                                                  "by": by, "counts": counts, "bytes": len(blob)})
            except Exception:  # noqa: BLE001
                pass
        return True, f"أُرسل ملف البلاغات إلى المطوّر ({name})"
    except (SendError, sealed.SealedError) as e:
        if request_id and not adopted:       # an adopted request is still waiting as it was
            _save_state(pending_request={"id": request_id, "scope": scope, "at": _now()})
        _save_state(sending=False, last_error=str(e)[:300])
        return False, str(e)
    except Exception as e:  # noqa: BLE001 - never break the server
        _save_state(sending=False, last_error=("خطأ غير متوقع: " + str(e))[:300])
        return False, "خطأ غير متوقع أثناء الإرسال"
    finally:
        _send_lock.release()


def send_async(**kw):
    t = threading.Thread(target=send, kwargs=kw, daemon=True)
    t.start()
    return t


def on_request(req, audit=None, run=None):
    """A signed request from the update channel: {"id": "...", "scope": "open"|"all"}. Sent once per id."""
    if not isinstance(req, dict) or not req.get("id"):
        return False
    rid = str(req["id"])[:80]
    scope = req.get("scope") if req.get("scope") in ("open", "all") else "open"
    s = state()
    if rid in (s.get("requests_done") or []):
        return False
    kw = dict(reason="request", scope=scope, by="طلب المطوّر", request_id=rid, audit=audit)
    if ready():                          # cannot send now: kept, sent by tick() once the manager turns sending on
        _save_state(pending_request={"id": rid, "scope": scope, "at": _now()})
        return True
    (run or send_async)(**kw)
    return True


def tick(audit=None, put=None):
    """The hourly look: a waiting request first, else the weekly file when it is due. -> what was done or None."""
    cfg, s = config(), state()
    if ready(cfg):
        return None
    pend = s.get("pending_request")
    if pend and pend.get("id") and pend["id"] not in (s.get("requests_done") or []):
        return send(reason="request", scope=pend.get("scope") or "open", by="طلب المطوّر", request_id=pend["id"],
                    put=put, audit=audit)
    due = next_due(cfg, s)
    if not s.get("last_sent") or (due and datetime.now(timezone.utc) >= _parse(due)):
        return send(reason="weekly", scope="open", by="الإرسال الأسبوعي", put=put, audit=audit)
    return None


def start_background(audit=None):
    """The hourly look (started by app.py with the server)."""
    if audit:
        _audit["fn"] = audit
    if _timer["thread"]:
        return
    if state().get("sending"):           # a send cut by a stopped server
        _save_state(sending=False)

    def loop():
        time.sleep(120)
        while True:
            try:
                tick(audit)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(3600)
    t = threading.Thread(target=loop, daemon=True)
    _timer["thread"] = t
    t.start()
