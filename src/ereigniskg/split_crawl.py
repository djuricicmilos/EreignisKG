"""Send pending URLs from a merged snapshot to their owners; preserve local results."""
import argparse
import sqlite3
from collections import Counter
from pathlib import Path
from src.ereigniskg.crawler import SCHEMA, owner


def split(source, root, workers):
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    counts = Counter()
    with sqlite3.connect(source) as db:
        rows = db.execute("SELECT url, depth FROM pages WHERE status = 'pending'").fetchall()
    for i in range(workers):
        path = Path(root) / f"worker_{i}"
        path.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path / "crawl.db")
        try:
            db.executescript(SCHEMA)
            for url, depth in rows:
                if owner(url, workers) == i:
                    db.execute("INSERT OR IGNORE INTO pages(url,status,depth) VALUES(?, 'pending', ?)", (url, depth))
                    counts[i] += 1
            db.commit()
        finally:
            db.close()
        print(f"Worker {i}: {counts[i]} pending URLs assigned")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True)
    ap.add_argument("--root", default="data")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    split(a.source, a.root, a.workers)