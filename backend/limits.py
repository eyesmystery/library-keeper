"""Abuse and cost protection for the public visitor endpoints (approved 2026-09-27).

- Burst limits (in memory, per server process): per network address and per conversation.
  A bookstore Wi-Fi puts many visitors behind ONE address, so the per-address limit is generous;
  the per-conversation limit catches a single runaway client.
- Daily model-call budget (stored in usage_daily): when it is used up, the chat keeps working
  with the keyword fallback instead of calling the model, and the admin page shows a warning.
  The budget is set by a manager (setting `daily_model_calls` in data/llm_settings.json);
  default 1000 calls (~500 conversation turns; two calls per turn).
"""
import os
import time
from collections import defaultdict, deque
from datetime import datetime

import llm
from models import get_connection

PER_ADDRESS = (int(os.environ.get("LIBRARY_KEEPER_RATE_ADDR", "300")), 600)   # requests / seconds
PER_ADDRESS_NOTES = (int(os.environ.get("LIBRARY_KEEPER_RATE_NOTES", "40")), 600)  # reports + suggestions
PER_ADDRESS_FEEDBACK = (int(os.environ.get("LIBRARY_KEEPER_RATE_FEEDBACK", "60")), 600)  # ratings
PER_CONVERSATION = (int(os.environ.get("LIBRARY_KEEPER_RATE_CONV", "30")), 600)
DEFAULT_DAILY_CALLS = 1000

_hits = defaultdict(deque)


def _allow(key, limit, window):
    now = time.time()
    q = _hits[key]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def allow_request(address, conversation_id=None):
    """False when this address or conversation sent too many requests recently."""
    ok = _allow("a:" + str(address), *PER_ADDRESS)
    if ok and conversation_id:
        ok = _allow("c:" + str(conversation_id)[:64], *PER_CONVERSATION)
    if not ok:
        bump("rate_limited")
    return ok


def allow_note(address):
    """Visitor reports and suggestions: a much smaller allowance than chat (they are typed by hand)."""
    ok = _allow("n:" + str(address), *PER_ADDRESS_NOTES) and _allow("a:" + str(address), *PER_ADDRESS)
    if not ok:
        bump("rate_limited")
    return ok


def allow_feedback(address):
    """Ratings: a visitor sends one after choosing a book, so a small allowance per address is enough
    and keeps unlinked ratings from flooding the statistics (audit 2026-10-02)."""
    ok = _allow("f:" + str(address), *PER_ADDRESS_FEEDBACK) and _allow("a:" + str(address), *PER_ADDRESS)
    if not ok:
        bump("rate_limited")
    return ok


def reset():
    _hits.clear()


def today():
    return datetime.now().strftime("%Y-%m-%d")


def daily_cap():
    try:
        v = int(llm.read_settings().get("daily_model_calls", DEFAULT_DAILY_CALLS))
    except (TypeError, ValueError):
        v = DEFAULT_DAILY_CALLS
    return max(0, v)


def set_daily_cap(value):
    value = int(value)
    if value < 0 or value > 1_000_000:
        raise ValueError("قيمة غير صحيحة")
    llm.update_settings(daily_model_calls=value)


def bump(column, amount=1):
    assert column in ("model_calls", "tokens", "fallback_turns", "capped_turns", "rate_limited")
    conn = get_connection()
    try:
        conn.execute("INSERT OR IGNORE INTO usage_daily (day) VALUES (?)", (today(),))
        conn.execute(f"UPDATE usage_daily SET {column} = {column} + ? WHERE day = ?", (int(amount), today()))
        conn.commit()
    finally:
        conn.close()


def record_call(call):
    """llm.call_hook: count every model call and its tokens."""
    usage = call.get("usage") or {}
    tokens = sum(v for v in usage.values() if isinstance(v, int))
    conn = get_connection()
    try:
        conn.execute("INSERT OR IGNORE INTO usage_daily (day) VALUES (?)", (today(),))
        # a call with a corrective retry is two provider requests
        conn.execute("UPDATE usage_daily SET model_calls = model_calls + ?, tokens = tokens + ? WHERE day = ?",
                     (max(1, int(call.get("attempts") or 1)), tokens, today()))
        conn.commit()
    finally:
        conn.close()


def usage(days=7):
    conn = get_connection()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM usage_daily ORDER BY day DESC LIMIT ?", (days,))]
    finally:
        conn.close()
    t = next((r for r in rows if r["day"] == today()), {"day": today(), "model_calls": 0, "tokens": 0,
                                                         "fallback_turns": 0, "capped_turns": 0, "rate_limited": 0})
    cap = daily_cap()
    return {"today": t, "cap": cap, "remaining": max(0, cap - t["model_calls"]), "capped": t["model_calls"] >= cap,
            "days": rows}


def budget_left(needed=2):
    """True while today's model calls leave room for one more turn (two calls)."""
    return usage(1)["today"]["model_calls"] + needed <= daily_cap()
