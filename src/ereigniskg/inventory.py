#!/usr/bin/env python3
"""
inventory.py: pregled onoga što je crawler pronašao i skinuo.

Grupiše URL-ove po prefiksu putanje (prvih nekoliko direktorijuma) i za svaku
grupu ispisuje broj stranica, ukupnu veličinu, nekoliko primera i naslov jedne
stranice. Cilj je da vidiš koliko različitih tipova stranica sajt ima, pre nego
što počneš da pišeš parsere.

Primeri:
  python inventory.py                # grupisanje po prvom direktorijumu
  python inventory.py --depth 2      # po prva dva direktorijuma
  python inventory.py --depth 2 --min 20
Rezultat se upisuje i u data/inventory.csv (jedan red po URL-u).
"""

import argparse
import csv
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title>", re.I | re.S)


def group_key(url, depth):
    segments = [s for s in urlparse(url).path.split("/") if s]
    directories = segments[:-1]                    # poslednji segment je ime fajla
    if not directories:
        return "/(fajlovi u korenu)"
    return "/" + "/".join(directories[:depth]) + "/"


def read_title(path):
    try:
        head = path.read_bytes()[:8192]
    except OSError:
        return ""
    m = TITLE_RE.search(head)
    if not m:
        return ""
    return " ".join(m.group(1).decode("utf-8", "replace").split())[:80]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--depth", type=int, default=1)
    ap.add_argument("--min", type=int, default=1, help="ne prikazuj grupe sa manje stranica od ovoga")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    db = sqlite3.connect(data_dir / "crawl.db")
    rows = db.execute("SELECT url, status, size, local_path FROM pages").fetchall()

    groups = defaultdict(list)
    for url, status, size, local_path in rows:
        groups[group_key(url, args.depth)].append((url, status, size or 0, local_path))

    print(f"Ukupno URL-ova u bazi: {len(rows)}\n")
    for key, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(items) < args.min:
            continue
        done = [i for i in items if i[1] == "done"]
        total_kb = sum(i[2] for i in done) / 1024
        print(f"{key}  stranica: {len(items)}  skinuto: {len(done)}  veličina: {total_kb:.0f} KB")
        for url, *_ in items[:3]:
            print(f"    {url}")
        if done and done[0][3]:
            title = read_title(data_dir / "raw" / done[0][3])
            if title:
                print(f"    naslov prve skinute: {title}")
        print()

    out = data_dir / "inventory.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["group", "url", "status", "size", "local_path"])
        for key, items in groups.items():
            for url, status, size, local_path in items:
                w.writerow([key, url, status, size, local_path])
    print(f"Zapisano: {out}")


if __name__ == "__main__":
    main()