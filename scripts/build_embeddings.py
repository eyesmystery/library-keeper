"""Build / refresh the meaning-search vectors of the books (backend/embeddings.py).

  backend/venv/bin/python scripts/build_embeddings.py            # only new or changed books
  backend/venv/bin/python scripts/build_embeddings.py --all      # every book again

Needs Ollama running with the embedding model (default bge-m3: `ollama pull bge-m3`).
Writes only the table book_embeddings (no backup needed: it is rebuilt from the books at any time).
"""
import os, sys, time
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "backend"))
import embeddings  # noqa: E402
from models import get_connection, list_books  # noqa: E402


def main():
    rebuild = "--all" in sys.argv
    model = embeddings.model_name()
    conn = get_connection()
    embeddings.ensure_table(conn)
    have = {r[0]: r[1] for r in conn.execute("SELECT book_id, text_hash FROM book_embeddings WHERE model=?", (model,))}
    todo = []
    for b in (dict(x) for x in list_books(only_active=False)):
        text = embeddings.book_text(b)
        if not text:
            continue
        h = embeddings.text_hash(text)
        if rebuild or have.get(b["book_id"]) != h:
            todo.append((b["book_id"], text, h))
    print(f"model {model}: {len(todo)} books to embed ({len(have)} stored)", flush=True)
    # vectors of an old text whose book will not get a new one (nothing left to read, or deleted)
    again = {t[0] for t in todo}
    gone = [bid for bid in embeddings.stale_ids(conn, model) if bid not in again]
    if gone:
        conn.executemany("DELETE FROM book_embeddings WHERE book_id=? AND model=?", [(bid, model) for bid in gone])
        conn.commit()
        print(f"removed {len(gone)} stale vectors of books without a text now", flush=True)
    done, t0 = 0, time.time()
    for i in range(0, len(todo), 32):
        batch = todo[i:i + 32]
        vecs = embeddings.embed([t for _, t, _ in batch], timeout=180.0)
        if vecs is None:
            sys.exit("Ollama or the model is not reachable; nothing more written")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.executemany("INSERT OR REPLACE INTO book_embeddings (book_id, model, text_hash, dim, vec, updated_at) VALUES (?,?,?,?,?,?)",
                         [(bid, model, h, len(v), v.tobytes(), now) for (bid, _, h), v in zip(batch, vecs)])
        conn.commit()
        done += len(batch)
        if done % 320 == 0 or done == len(todo):
            print(f"{done}/{len(todo)} ({time.time() - t0:.0f} s)", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
