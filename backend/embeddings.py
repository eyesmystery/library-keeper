"""Search by meaning in what we store about each book (layer 2 of «طبق كل الطبقات», 2026-10-08).

The word search in topics.py finds «الغربة» only where that word is written; a book whose summary
says «المنفى» or «البعد عن الوطن» is missed. Here each book's title, subjects, setting, hook and
summary are turned into a vector by a local Ollama embedding model (bge-m3, multilingual), stored in
`book_embeddings`, and a subject the visitor asks for is compared with them.

- Vectors are built offline by scripts/build_embeddings.py (only books with a summary, hook or subjects).
- At chat time one short call embeds the visitor's subject(s); if Ollama or the model is missing the
  search is silently skipped (the word search still works) and retried after five minutes.
- No new Python dependency: vectors are float32 arrays, compared with plain Python.
"""
import array
import hashlib
import json
import math
import time
import urllib.request

import llm
from models import get_connection

MODEL = "bge-m3"
THRESHOLD = 0.52      # cosine similarity a book needs (bge-m3, query «كتاب عن …», calibrated 2026-10-08)
BAND = 0.05           # and it must be within this of the best match for the same subject
TOP = 6               # at most this many books per subject
_cache = {"at": 0.0, "vecs": None, "model": None, "hashes": None}
_down = {"until": 0.0}


def ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS book_embeddings (
        book_id INTEGER PRIMARY KEY, model TEXT NOT NULL, text_hash TEXT NOT NULL, dim INTEGER NOT NULL,
        vec BLOB NOT NULL, updated_at TEXT,
        FOREIGN KEY (book_id) REFERENCES books(book_id) ON DELETE CASCADE)""")


def model_name():
    return (llm.read_settings().get("embed_model") or MODEL).strip()


def _json_words(v):
    try:
        items = json.loads(v) if isinstance(v, str) and v.strip().startswith("[") else [v]
    except ValueError:
        items = [v]
    return "، ".join(str(x) for x in items if x)


def book_text(b):
    """What a book is about, as one text; None when we only know its title."""
    parts = [str(b.get(k) or "").strip() for k in ("hook", "summary")]
    extra = [_json_words(b.get("subjects")), _json_words(b.get("subgenres")), str(b.get("setting") or "")]
    if not any(parts) and not b.get("subjects"):
        return None
    return " | ".join(x for x in [str(b.get("title") or "")] + [x for x in extra if x] + [x for x in parts if x] if x)[:2000]


def text_hash(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def embed(texts, timeout=20.0):
    """-> list of unit vectors (array('f')), or None when Ollama / the model is not reachable."""
    if not texts:
        return []
    if time.time() < _down["until"]:
        return None
    body = json.dumps({"model": model_name(), "input": list(texts)}).encode()
    req = urllib.request.Request(llm._ollama_base() + "/api/embed", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except Exception:  # noqa: BLE001 - the chat goes on without it
        _down["until"] = time.time() + 300
        return None
    out = []
    for v in data.get("embeddings") or []:
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append(array.array("f", (x / n for x in v)))
    return out if len(out) == len(texts) else None


def load(max_age=300):
    """{book_id: vector} of the current model, cached for five minutes.

    A vector is used only while its stored text_hash is the hash of what the book says NOW (title,
    subjects, setting, hook, summary). A record corrected to another work, or a rewritten summary,
    leaves a vector of the old text behind: it is skipped here until scripts/build_embeddings.py
    writes a new one (audit 2026-10-08)."""
    m = model_name()
    if _cache["vecs"] is not None and _cache["model"] == m and time.time() - _cache["at"] < max_age:
        return _cache["vecs"]
    vecs, hashes = {}, {}
    conn = get_connection()
    try:
        ensure_table(conn)
        rows = conn.execute("SELECT book_id, vec, text_hash FROM book_embeddings WHERE model=?", (m,)).fetchall()
        now = _current_hashes(conn, [r[0] for r in rows])
        for bid, blob, h in rows:
            if now.get(bid) != h:
                continue  # stale: the book's text changed after this vector was built
            a = array.array("f")
            a.frombytes(blob)
            vecs[bid] = a
            hashes[bid] = h
    finally:
        conn.close()
    _cache.update(at=time.time(), vecs=vecs, model=m, hashes=hashes)
    return vecs


def _current_hashes(conn, book_ids):
    """{book_id: text_hash of the book's text as it is in the database now}; a book that no longer
    has anything to read (or no longer exists) is left out."""
    out, ids = {}, list(book_ids)
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        cur = conn.execute("SELECT * FROM books WHERE book_id IN (%s)" % ", ".join("?" * len(part)), part)
        cols = [c[0] for c in cur.description]
        for row in cur:
            b = dict(zip(cols, row))
            text = book_text(b)
            if text:
                out[b["book_id"]] = text_hash(text)
    return out


def _drop_changed(book_ids):
    """Of the cached vectors for these books, the ids whose text changed since the cache was filled
    (the cache lives five minutes; a correction must not wait for it). They leave the cache too."""
    hashes = _cache.get("hashes") or {}
    ids = [i for i in book_ids if i in hashes]
    if not ids:
        return set()
    conn = get_connection()
    try:
        now = _current_hashes(conn, ids)
    finally:
        conn.close()
    stale = {i for i in ids if now.get(i) != hashes[i]}
    for i in stale:
        _cache["vecs"].pop(i, None)
        hashes.pop(i, None)
    return stale


def invalidate(book_ids=None):
    """Forget cached vectors: all of them (None), or those of the books given. The stored rows stay;
    load() checks their text_hash again."""
    if book_ids is None or _cache["vecs"] is None:
        _cache.update(at=0.0, vecs=None, model=None, hashes=None)
        return
    for i in book_ids:
        _cache["vecs"].pop(i, None)
        (_cache.get("hashes") or {}).pop(i, None)


def stale_ids(conn, model=None):
    """book_ids whose stored vector no longer matches the book's text (read only)."""
    rows = conn.execute("SELECT book_id, text_hash FROM book_embeddings WHERE model=?", (model or model_name(),)).fetchall()
    now = _current_hashes(conn, [r[0] for r in rows])
    return sorted(bid for bid, h in rows if now.get(bid) != h)


LIKE_THRESHOLD = 0.58   # a whole description (without its title) against our books' texts (calibrated 2026-10-11:
LIKE_BAND = 0.06       # «اخلع حذاءك» -> books on happiness / self-reflection 0.64-0.67; within this of the best)


def similar_to(text, book_ids, top=4, threshold=None):
    """Books whose stored text is closest in meaning to a whole description (a book we do not have,
    looked up outside) -> [(book_id, similarity), ...] best first; [] when unavailable."""
    vecs = load()
    if not vecs or not text:
        return []
    qv = embed([text], timeout=8.0)
    if not qv:
        return []
    q = qv[0]
    scored = []
    for i in book_ids:
        v = vecs.get(i)
        if v is not None:
            s = sum(a * b for a, b in zip(q, v))
            if s >= (LIKE_THRESHOLD if threshold is None else threshold):
                scored.append((i, round(s, 3)))
    scored.sort(key=lambda x: -x[1])
    stale = _drop_changed([i for i, _ in scored[:top * 2]])
    scored = [x for x in scored if x[0] not in stale]
    return [x for x in scored if x[1] >= scored[0][1] - LIKE_BAND][:top] if scored else []


def search(phrases, book_ids):
    """-> {phrase: [(book_id, similarity), ...] best first} for the books given (only those with a
    vector): at or above THRESHOLD and within BAND of the best one, TOP per phrase. {} when unavailable.
    The subject is asked as «كتاب عن …», which matched our summaries clearly better than the bare word."""
    vecs = load()
    if not vecs or not phrases:
        return {}
    qv = embed(["كتاب عن " + p for p in phrases], timeout=8.0)
    if not qv:
        return {}
    ids = [i for i in book_ids if i in vecs]
    out = {}
    for phrase, q in zip(phrases, qv):
        scored = []
        for i in ids:
            v = vecs[i]
            s = sum(a * b for a, b in zip(q, v))
            if s >= THRESHOLD:
                scored.append((i, round(s, 3)))
        scored.sort(key=lambda x: -x[1])
        # the candidates are checked against the book's text as it is this second, not as it was
        # when the cache was filled: a corrected record never answers for the work it used to name
        stale = _drop_changed([i for i, _ in scored])
        if stale:
            scored = [x for x in scored if x[0] not in stale]
        if scored:
            best = scored[0][1]
            out[phrase] = [x for x in scored if x[1] >= best - BAND][:TOP]
    return out
