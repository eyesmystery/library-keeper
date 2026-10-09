# Development, tests and version control

Project location (since 2026-09-26): `~/Projects/Apps/Library Keeper` — a local folder, **not** iCloud.
The old iCloud folder `iCloud Drive/Projects/Apps/Library Keeper` is no longer the project.

## Running the app
Double-click `start_server.command`. On the first run it:
1. creates `backend/venv` and installs `backend/requirements.txt` (needs internet once);
2. builds `data/library.db` from `data/catalog_snapshot.sql` if the database does not exist;
3. backs up the database (see `docs/backup-restore.md`);
4. starts the server on http://127.0.0.1:8802 and opens the browser.

## Running the tests
Double-click `run_tests.command`, or in Terminal from the project folder:

    ./run_tests.command                 # all suites
    ./run_tests.command test_age.py     # one suite

`tests/run_all.py` copies `backend/`, `frontend/`, `admin/` and a snapshot of `data/library.db`
into a temporary folder for every suite, with no API keys and a throwaway key file, so tests never
touch the real database, settings, keys or test logs. Exit code 0 = all suites passed.

| Suite | Covers |
|---|---|
| test_base.py | routes, catalog rows, Phase 1 keyword queries, exclusion, feedback, PATCH validation, fresh seeding |
| test_profiles.py | profile-based reasons, confidence tiers, conflict exclusion |
| test_llm.py | AI preference understanding (mocked): negation, dialect, unsupported topics, cache, fallback, off switch, request format |
| test_json.py | JSON extraction from model replies, one corrective retry |
| test_chat.py | chat flow per provider (mocked), grounding, id validation, fallbacks |
| test_testlog.py | test mode logging and the production guard |
| test_keys.py | key storage, masking, local-only key/endpoint routes |
| test_admin_cards.py | admin catalog and card API |
| test_review.py | optional staff review actions |
| test_import.py | Rewaa CSV/xlsx import (xlsx built with `tests/xlsx_helper.py`, no extra dependency) |
| test_age.py | age suitability and content warnings in recommend/chat/admin |
| test_structured.py | schema-enforced JSON, fallback when a provider refuses it, per-call diagnostics, failed-stage reporting |
| test_eval_script.py | the model evaluation script against a fake model |
| test_fallback.py | keyword fallback: whole-word matching, negation, dialect, message-by-message understanding, constraints (66 Arabic cases in `tests/fallback_cases.json`) |

Not yet in the permanent suite: the headless-browser end-to-end checks (they need Playwright,
which is not a project dependency).

## Model evaluation
Double-click `eval_model.command` (uses the model chosen in admin → الإعدادات; real API calls). Cases: `tests/eval/cases.json`. Reports: `eval_results/` (not committed). Options: `--repeat N`, `--mode schema|json|both`, `--cases id ...`, `--provider/--model`.

## Git
- The repository lives in the project folder (`.git`). Commit code, docs, tests, `data/seed_books.json`
  and `data/catalog_snapshot.sql`.
- Never committed (see `.gitignore`): `backend/venv`, `__pycache__`, `data/library.db` and its temp/backup
  files, `backups/`, `data/test_logs/` (visitor conversations), `data/llm_settings.json`, key files.
  API keys live outside the project in `~/.library_keeper/keys.json`.
- When the catalog changes (import, enrichment, staff review) refresh the snapshot before committing:

      backend/venv/bin/python scripts/catalog_snapshot.py export
      backend/venv/bin/python scripts/catalog_snapshot.py check

- Useful commands: `git status`, `git log --oneline`, `git diff`, `git show <commit>`,
  `git restore <file>` (discard an uncommitted change), `git revert <commit>` (undo a commit safely).

## Versioning (approved by Ibrahim 2026-10-02)

The version number lives in one place: the `VERSION` file in the project folder (one line, e.g. `0.8 beta`).
The server reads it at startup and admin → الإعدادات → «عن التطبيق» shows it, so the page shows the version
of the server that is actually running (restart the server after changing the file). Never write the number
anywhere else in the code. A missing or empty file shows «—».

The number has the form `MAJOR.MINOR[.PATCH] beta`. Started at `0.8 beta`.

| What happened | Bump | Example |
|---|---|---|
| An update that changes something a visitor or a staff member sees or uses | third number | 0.8 → 0.8.1 → 0.8.2 |
| A stage on the way to the library trial is complete | second number (third dropped) | 0.8.4 → 0.9 |
| Ibrahim decides the app is ready for the library trial | `1.0 beta` — only on his decision | 1.0 |
| Book research batches, catalog data, documentation, tests, internal changes with nothing visible | no bump | — |

- One bump per update, in the same commit as the change; the `log.md` entry names the new version.
- Tag the commit: `git tag v0.8.1-beta`.
- No date is kept next to the version; the date of a version is the date of its commit/tag.
- `docs/trial-guide/trial-guide.html` says «الإصدار ١٫٠» on purpose: it is the guide of the library-trial version.

## Windows (added 2026-09-27)

The app is plain Python + Flask and runs on Windows too. Install Python 3 from python.org (tick "Add python.exe to PATH"), then double-click:

- `start_server.bat` — normal use on this computer only (like `start_server.command` on a Mac).
- `trial_server.bat` — trial mode: phones on the same Wi-Fi open it by scanning the QR code (admin → الإعدادات → رمز التجربة). Allow Python on *private* networks when Windows Firewall asks.

The first run creates `backend\venv-win` (separate from the Mac `backend/venv`, so one shared project folder works on both) and needs internet to install Flask. Backups stay in `backups\` (the iCloud mirror is Mac-only; set `LIBRARY_KEEPER_BACKUP_MIRROR` to e.g. a OneDrive folder to mirror them). The other tools run from a Command Prompt in the project folder, e.g. `backend\venv-win\Scripts\python scripts\fetch_covers.py --limit 20`. The daily-backup LaunchAgent is Mac-only.
