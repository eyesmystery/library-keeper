# Installing Library Keeper (أمين المكتبة) — step by step

This file is written so that **a person or an AI coding agent** (Claude, ChatGPT/Codex, Cursor, Copilot, Gemini…) can install and start the app on a new computer without prior knowledge. Agents: follow the steps in order, run the verification commands, and report the results to the user in Arabic. Do not change any application code to make it start; if a step fails, use the troubleshooting table.

## What it is

A small web app: a Python/Flask server with an SQLite database. Visitors open it on their phones (by scanning a QR code) and chat with a reading assistant that suggests books from the library's catalog. Staff use an admin page on the same server.

- Server: `backend/app.py` (Flask 3.0.3, Werkzeug 3.0.3; nothing else to install)
- Port: `8802`
- Database: `data/library.db`, built automatically on first start from `data/catalog_snapshot.sql` (500 books). Never commit it.
- No build step, no Node.js, no Docker, no external database.

## Requirements

- macOS 12+ or Windows 10/11 (Linux also works from the command line)
- Python 3.9 or newer
  - macOS: `python3 --version`. If missing: install from https://www.python.org/downloads/ (or `brew install python`).
  - Windows: `py -3 --version` or `python --version`. If missing: install from https://www.python.org/downloads/ and tick **"Add python.exe to PATH"** in the installer.
- Internet on the first start only (to install Flask into a local virtual environment)
- For the staff trial: the computer and the phones on the **same Wi-Fi network**

## 1. Get the files

```
git clone <this repository URL> library-keeper
cd library-keeper
```

(Or download the ZIP from GitHub and unzip it.)

## 2. Start it

There are two ways to start. **Trial mode** is the one used for the staff trial (phones can open it). **Local mode** is for this computer only.

### macOS — double-click
- Trial mode: `trial_server.command`
- Local mode: `start_server.command`

If macOS blocks the file ("cannot be opened because it is from an unidentified developer"): right-click → Open → Open. If it says permission denied, run once in Terminal inside the folder:
```
chmod +x *.command scripts/*.sh
```

### Windows — double-click
- Trial mode: `trial_server.bat`
- Local mode: `start_server.bat`

If Windows SmartScreen warns: More info → Run anyway.

### Any system — from a terminal (equivalent, useful for agents)
macOS / Linux:
```
python3 -m venv backend/venv
backend/venv/bin/pip install -r backend/requirements.txt
backend/venv/bin/python scripts/catalog_snapshot.py build      # only if data/library.db does not exist yet
cd backend && LIBRARY_KEEPER_HOST=0.0.0.0 ../backend/venv/bin/python app.py      # trial mode
```
Windows (Command Prompt):
```
py -3 -m venv backend\venv-win
backend\venv-win\Scripts\python -m pip install -r backend\requirements.txt
backend\venv-win\Scripts\python scripts\catalog_snapshot.py build
cd backend
set LIBRARY_KEEPER_HOST=0.0.0.0
..\backend\venv-win\Scripts\python app.py
```
(Leave out the `LIBRARY_KEEPER_HOST` line for local mode.)

The first start creates the virtual environment, builds `data/library.db` (message: `built data/library.db from snapshot (500 books, integrity=ok …)`) and makes a backup in `backups/`. In trial mode the window prints:
```
Trial mode - phones on the same Wi-Fi: http://192.168.x.x:8802/
```
**Keep this window open** while the app is in use. Closing it stops the server.

When the operating system asks whether Python may accept incoming network connections: **Allow** (on Windows: allow on *Private* networks). Without this, phones cannot open the page.

## 3. First-time setup (once)

1. On the same computer open `http://127.0.0.1:8802/admin`.
2. The page asks to create the **manager account** (name, username in English letters, password of at least 8 characters with letters and digits). This form only works from the computer running the server.
3. Settings (الإعدادات) → **الموظفون**: add an account for each staff member.
4. Settings → **رمز التجربة (QR)**: the status must say «جاهز». Print the QR card (اطبع لوحة الرمز), download it as an image, or copy the link.
5. Optional — Settings → **نموذج المحادثة**: choose an AI model and press «اختبار». Without a model the app still works (keyword understanding, simpler replies). API keys are stored outside the project folder (`~/.library_keeper/`) and never in the repository.
6. Optional — Settings → **أقسام المكتبة**: rename the sections to match the library and switch them on, so visitors see where a book is.

## 4. Verify (agents: run these and report)

With the server running, in a second terminal:
```
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8802/            # expect 200 (visitor page)
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8802/admin       # expect 200 (admin page)
curl -s http://127.0.0.1:8802/api/admin/session                           # expect "needs_setup": true before the manager account exists
curl -s -X POST http://127.0.0.1:8802/api/chat -H "Content-Type: application/json" -d "{\"messages\":[{\"role\":\"user\",\"content\":\"أبغى رواية\"}]}"
                                                                          # expect JSON with "reply" and a non-empty "books" list
```
(On Windows use `curl.exe` in PowerShell, or open the addresses in a browser.)

Then from a phone on the same Wi-Fi, open the address printed in the server window (or scan the QR code): the welcome page «وش ودّك تقرأ اليوم؟» must appear.

## 5. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `python3`/`py` not found | Install Python 3 (see Requirements). On Windows reinstall with "Add python.exe to PATH". Close and reopen the terminal. |
| `pip install` fails | No internet on the first start, or a proxy. Connect and start again. |
| `Address already in use` / "The server is already running" | Another copy is running on port 8802. Close its window (or stop that process) and start again. |
| Phone cannot open the page | Phone not on the same Wi-Fi; the server was started in local mode (use trial mode); the firewall prompt was denied (macOS: System Settings → Network → Firewall → allow Python; Windows: Windows Defender Firewall → Allow an app → Python, Private); the guest Wi-Fi isolates devices (use the staff Wi-Fi). |
| QR panel says the server is an old version | The page is newer than the running server: close the server window, start trial mode again, refresh the page. |
| QR panel says «ما فيه عنوان شبكة» | The computer is not on a network. Connect to the Wi-Fi and refresh. |
| The address changed after a restart | The router gave the computer a new address: print the QR code again (or ask IT for a fixed address for this computer). |
| Setup form refuses ("from this computer only") | Open the admin page on the computer running the server, at `http://127.0.0.1:8802/admin`. |
| Replies are simple or repetitive | No AI model is working; the keyword mode is answering. Settings → نموذج المحادثة → اختبار. |

## Rules for agents

- Do not commit `data/library.db`, `backups/`, `data/test_logs/`, `data/llm_settings.json` or any key file (they are in `.gitignore`).
- Do not expose the server to the internet (no port forwarding, no tunnels) — it is meant for the local Wi-Fi during this trial.
- Do not modify application code or data to "fix" a start-up problem; report the error text instead.
- Test mode (saving conversations) is off by default; only the manager switches it in Settings.

## Other tools

- `fetch_covers.command` (macOS) or `python scripts/fetch_covers.py --limit 20`: fetch book covers (needs internet).
- `python scripts/backup_db.py` / `python scripts/restore_db.py --list`: manual backup and restore (a backup is also made at every start).

Guide for staff (Arabic, with the evaluation form): `docs/دليل-تجربة-أمين-المكتبة.pdf`
