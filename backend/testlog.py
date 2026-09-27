"""Opt-in TEST MODE conversation log (for Ibrahim and Claude to review model behavior).

- Off by default; switched in the admin page (stored as `test_log` in data/llm_settings.json).
- Hard-disabled when LIBRARY_KEEPER_PRODUCTION=1 (must be set for any real deployment).
- While on, the visitor page shows a visible notice that the conversation is being saved.
- Writes JSON Lines to data/test_logs/chat-YYYY-MM-DD.jsonl: visitor text, model reply,
  interpretation, candidates, shown/dropped ids, timing, errors. No API keys are ever written.
- Logging failures never break the visitor experience.
"""
import json
import os
import re
from datetime import datetime

import llm

LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "test_logs")
_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def production():
    return os.environ.get("LIBRARY_KEEPER_PRODUCTION") == "1"


def enabled():
    return (not production()) and bool(llm.read_settings().get("test_log"))


def set_enabled(value):
    if value and production():
        raise ValueError("test mode is disabled in production")
    llm.update_settings(test_log=bool(value))


def clean_id(value):
    value = str(value or "")
    return value if _ID.match(value) else None


def record(kind, payload):
    if not enabled():
        return
    line = {"ts": datetime.now().isoformat(timespec="seconds"), "kind": kind}
    line.update(payload)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, f"chat-{datetime.now():%Y-%m-%d}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


def summary():
    files, turns, conversations = [], 0, set()
    if os.path.isdir(LOG_DIR):
        for name in sorted(os.listdir(LOG_DIR)):
            if not name.endswith(".jsonl"):
                continue
            files.append(name)
            try:
                with open(os.path.join(LOG_DIR, name), encoding="utf-8") as f:
                    for raw in f:
                        try:
                            row = json.loads(raw)
                        except ValueError:
                            continue
                        if row.get("kind") == "turn":
                            turns += 1
                            conversations.add(row.get("conversation_id"))
            except OSError:
                continue
    return {"enabled": enabled(), "production": production(), "files": files,
            "turns": turns, "conversations": len(conversations - {None})}
