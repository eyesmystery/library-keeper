"""Small file writes that survive Windows (CHANFIX, 2026-10-27).

The library's 0.9.10 on Windows stopped «موافقة وتحديث» with
`[WinError 5] Access is denied: '…channel_state.json.tmp' -> '…channel_state.json'`: the download thread replaced the
state file at every percent while the admin page (another thread) was reading it, and Windows refuses to replace a file
that another handle has open (an antivirus scan does the same for a moment). On macOS and Linux the replace always
succeeds, so it never showed here.

- replace(src, dst): os.replace, tried again for a few seconds while Windows reports the target busy.
- write_json(path, data): a temporary file with a name of its own (two writers never share one), then replace().
- read_json(path, default): a read that waits a moment when the file is busy instead of returning `default` at once.
"""
import json
import os
import tempfile
import time

TRIES = 100         # × WAIT ≈ 10 s (CHANFIX2: an antivirus scan can hold a new file a few seconds)
WAIT = 0.1


def replace(src, dst, tries=TRIES, wait=WAIT):
    for n in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if n == tries - 1:
                raise
            time.sleep(wait)


def write_json(path, data, private=False, indent=1, newline=True):
    """Atomic JSON write. private: readable by this user only (POSIX; Windows ignores the mode)."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=folder)
    try:
        if private:
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            if newline:
                f.write("\n")
        replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def read_json(path, default=None, tries=20, wait=WAIT):
    """-> the parsed file, or `default` when it does not exist or is not JSON. A file that is busy (Windows) or caught
    half-replaced is read again for up to ~2 s."""
    for n in range(tries):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return default
        except (PermissionError, ValueError):
            if n == tries - 1:
                return default
            time.sleep(wait)
        except OSError:
            return default
    return default
