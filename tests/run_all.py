"""Run every test suite against a throwaway copy of the project.

Usage (from the project folder):  python3 tests/run_all.py [suite ...]
Never touches the real data/library.db, data/llm_settings.json, test logs or API keys:
each suite gets a fresh temporary copy of backend/, frontend/, admin/ and data/.
Exit code 0 = everything passed.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
SUITES = ["test_base.py", "test_profiles.py", "test_llm.py", "test_json.py", "test_chat.py",
          "test_testlog.py", "test_keys.py", "test_admin_cards.py", "test_review.py",
          "test_import.py", "test_age.py", "test_structured.py", "test_eval_script.py", "test_fallback.py", "test_codex.py", "test_origin.py", "test_model_options.py", "test_ollama_cloud.py", "test_auth.py", "test_wording.py", "test_covers.py", "test_insights.py", "test_sections.py", "test_english.py", "test_trial.py", "test_rewaa_cards.py", "test_relations.py", "test_reference.py", "test_lookup_sources.py", "test_authors.py", "test_admin_library.py", "test_deleted_books.py", "test_visitor_notes.py", "test_staff_notes.py", "test_honors.py", "test_audit_fixes.py", "test_confirmations.py", "test_topics.py", "test_events.py", "test_more.py", "test_age_edition.py", "test_qa2_fixes.py", "test_author_spelling.py", "test_auditfix.py"]
KEY_VARS = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY",
            "CUSTOM_LLM_API_KEY", "OLLAMA_BASE_URL", "LIBRARY_KEEPER_PRODUCTION", "LIBRARY_KEEPER_LLM"]
IGNORE = shutil.ignore_patterns("venv", "__pycache__", "*.pyc", "test_logs", "llm_settings.json",
                                "*.db-journal", "*.db-wal", "*.db-shm")


def fresh_copy(dest):
    for part in ("backend", "frontend", "admin", "scripts"):
        shutil.copytree(os.path.join(ROOT, part), os.path.join(dest, part), ignore=IGNORE)
    shutil.copytree(os.path.join(ROOT, "tests", "eval"), os.path.join(dest, "tests", "eval"))
    shutil.copy2(os.path.join(ROOT, "tests", "fallback_cases.json"), os.path.join(dest, "tests"))
    shutil.copytree(os.path.join(ROOT, "tests", "fake_codex"), os.path.join(dest, "tests", "fake_codex"))
    shutil.copy2(os.path.join(ROOT, "VERSION"), dest)
    os.makedirs(os.path.join(dest, "data"))
    shutil.copy2(os.path.join(ROOT, "data", "seed_books.json"), os.path.join(dest, "data"))
    # Tests run on the frozen prototype catalog (500 books, saved 2026-09-28 before the real Rewaa
    # catalog replaced it), so they keep working whatever is in the live database.
    db = os.path.join(TESTS, "fixtures", "prototype_catalog.db")
    if not os.path.exists(db):
        db = os.path.join(ROOT, "data", "library.db")
    if os.path.exists(db):  # a read-only copy (sqlite backup API = consistent copy)
        import sqlite3
        src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        dst = sqlite3.connect(os.path.join(dest, "data", "library.db"))
        src.backup(dst)
        # tests start without the real staff accounts (never carrying their password hashes) and without visitor activity
        for table in ("staff_users", "audit_log", "usage_daily", "feedback", "book_choices", "book_daily", "visitor_notes"):
            try:
                dst.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError:
                pass
        dst.commit()
        dst.close()
        src.close()


def main(names):
    names = names or SUITES
    total_p = total_f = 0
    failed = []
    for name in names:
        tmp = tempfile.mkdtemp(prefix="lk-test-")
        try:
            fresh_copy(tmp)
            env = {k: v for k, v in os.environ.items() if k not in KEY_VARS}
            env["LIBRARY_KEEPER_SECRETS"] = os.path.join(tmp, "no-keys", "keys.json")
            env["LIBRARY_KEEPER_SECRET_FILE"] = os.path.join(tmp, "no-keys", "secret_key")
            env["PYTHONPATH"] = TESTS
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            t0 = time.time()
            proc = subprocess.run([sys.executable, os.path.join(TESTS, name)], cwd=os.path.join(tmp, "backend"),
                                  env=env, capture_output=True, text=True, timeout=600)
            out = proc.stdout + proc.stderr
            p = len(re.findall(r"^PASS\b", out, re.M))
            f = len(re.findall(r"^FAIL\b", out, re.M))
            crashed = proc.returncode != 0
            status = "OK " if (f == 0 and not crashed and p > 0) else "BAD"
            print(f"{status} {name:22s} pass={p:3d} fail={f:3d}  ({time.time() - t0:.1f}s)" + ("  [crashed]" if crashed else ""))
            if status == "BAD":
                failed.append(name)
                for line in out.splitlines():
                    if line.startswith("FAIL") or "Error" in line or "Traceback" in line:
                        print("      " + line[:200])
            total_p += p
            total_f += f
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nTOTAL pass={total_p} fail={total_f} suites_failed={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
