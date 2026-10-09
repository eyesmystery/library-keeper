# Database backup and restore

## What is backed up
`data/library.db` (the live catalog: books, cards, evidence, feedback, import history).
Code is protected by Git; a Git bundle of all commits is also copied with each backup.

## When
- Every time `start_server.command` starts the server.
- Daily at 03:15 once `scripts/install_daily_backup.command` has been double-clicked (macOS LaunchAgent;
  remove with `scripts/install_daily_backup.command --remove`). Log: `~/Library/Logs/LibraryKeeper-backup.log`.
- Manually: `backend/venv/bin/python scripts/backup_db.py --reason "before import"`.
  Do this before any big import, migration or data change.

## Where
1. `backups/library-YYYYMMDD-HHMMSS.db` in the project folder (ignored by Git).
2. A mirror outside the Mac, in iCloud Drive: `Projects/Apps/Library Keeper Backups/`
   (plus `code.bundle`). Override with `LIBRARY_KEEPER_BACKUP_MIRROR`.
The newest 30 backups are kept in each place. Every run is logged in `backups/backup_log.txt`.
Each backup is made with SQLite's backup API (safe while the server runs) and must pass
`PRAGMA integrity_check`, otherwise it is discarded and the failure is logged.

## Restore the last good copy
1. Stop the server (close its Terminal window or press Ctrl+C).
2. In Terminal, from the project folder:

       backend/venv/bin/python scripts/restore_db.py --list      # see backups, books, integrity
       backend/venv/bin/python scripts/restore_db.py --latest    # or: --file backups/library-….db

   The script refuses while the server is running or if the backup fails its integrity check.
   The current database is first kept as `data/library.db.before-restore-<time>`.
3. Start the server again.

## Rebuild from scratch (no backup available)
Delete or move `data/library.db`, then start the server: it rebuilds the catalog from
`data/catalog_snapshot.sql` (the last committed snapshot). Feedback and import history made after
that snapshot are lost in this case, which is why the backups exist.

## Restore the code
`git clone "~/Library/Mobile Documents/com~apple~CloudDocs/Projects/Apps/Library Keeper Backups/code.bundle" "Library Keeper"`

## Cover images (since 2026-09-27)
Covers live in `data/covers/` (not in Git). Every backup copies new or changed cover files to the mirror
folder `…/Library Keeper Backups/covers/`. To restore them, copy that folder back to `data/covers/`
(the database only stores the file name, e.g. `/covers/123.jpg`). Any cover that is still missing can be
fetched again with `./fetch_covers.command`.
