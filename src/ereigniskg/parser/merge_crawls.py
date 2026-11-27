"""Merge stopped workers into a NEW directory; no network requests."""
import argparse
import shutil
import sqlite3
from pathlib import Path
from ereigniskg.parser.crawler import SCHEMA, owner


def merge(root, output, workers):
    root, output = Path(root), Path(output)
    if output.exists():
        raise ValueError(f"Output already exists: {output}")
    paths = [root / f"worker_{i}" for i in range(workers)]
    for path in paths:
        if not (path / "crawl.db").is_file():
            raise FileNotFoundError(path / "crawl.db")
    output.mkdir(parents=True)
    (output / "raw").mkdir()
    db = sqlite3.connect(output / "crawl.db")
    try:
        db.executescript(SCHEMA)
        for i, path in enumerate(paths):
            with sqlite3.connect(path / "crawl.db") as src:
                src.row_factory = sqlite3.Row
                for row in src.execute("SELECT * FROM pages"):
                    r = dict(row)
                    url = r["url"]
                    if owner(url, workers) != i:
                        # Non-owner rows are discoveries, not authoritative results.
                        db.execute("INSERT OR IGNORE INTO pages(url,status,depth) VALUES(?,?,?)", (url, "skipped_type" if r["status"] == "skipped_type" else "pending", r["depth"]))
                        continue
                    if r["status"] == "in_progress":
                        r["status"] = "pending"
                    if r["status"] == "done":
                        rel = Path(r["local_path"])
                        if rel.is_absolute() or ".." in rel.parts:
                            raise ValueError(f"Invalid path: {rel}")
                        source = path / "raw" / rel
                        target = output / "raw" / rel
                        if target.exists():
                            raise ValueError(f"Path collision: {target}")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, target)
                    cols = list(r)
                    db.execute(f"INSERT OR REPLACE INTO pages ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", list(r.values()))
                db.executemany("INSERT OR IGNORE INTO links(src,dst,anchor) VALUES(?,?,?)", src.execute("SELECT src,dst,anchor FROM links"))
        db.commit()
        print(db.execute("SELECT status, COUNT(*) FROM pages GROUP BY status").fetchall())
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default="data")
    ap.add_argument("--output", required=True)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    merge(a.root, a.output, a.workers)