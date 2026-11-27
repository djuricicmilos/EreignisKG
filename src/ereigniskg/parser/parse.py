#!/usr/bin/env python3
"""
parse.py: iz sirovog HTML-a u data/raw/ pravi data/pages.jsonl, jedan red po stranici.

Svaka stranica dobija tip na osnovu putanje URL-a (PAGE_TYPES), a tip određuje
koji parser se koristi (PARSERS). Dok ne napišeš posebne parsere, svi tipovi
koriste parse_generic. Zato je dobro da ovu skriptu pokrećeš često: čim
napišeš bolji parser za jedan tip, ponovo je pokreneš i dobiješ novi pages.jsonl
bez ponovnog skidanja sajta.

Primer:
  python parse.py
  python parse.py --only paper        # samo jedan tip, za brzo testiranje
"""

import argparse
import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

try:
    import lxml  # noqa: F401
    PARSER = "lxml"
except ImportError:
    PARSER = "html.parser"

try:
    import pymupdf
except ImportError:
    try:
        import fitz as pymupdf      # starije verzije PyMuPDF-a
    except ImportError:
        pymupdf = None

# Redosled je bitan: proverava se od vrha, prvi obrazac koji odgovara pobeđuje.
# Ovo su HIPOTEZE iz pretrage. Zameni ih pravim obrascima kada pogledaš inventory.py.
PAGE_TYPES = [
    (r"^/hb/gesamt\.html$", "gesamtausgabe_list"),
    (r"^/hb/", "bibliography_or_tool"),
    (r"^/(papers|docs)/", "paper"),
    (r"^/pages/", "book_page"),
]


def classify(url):
    path = urlparse(url).path
    for pattern, page_type in PAGE_TYPES:
        if re.search(pattern, path):
            return page_type
    return "other"


def clean(text):
    # NFC je važan za grčki sa diakriticima i za nemačke umlaute
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def parse_generic(soup, url):
    """Osnovni parser: naslov, naslovi sekcija i tekst bez skripti i stilova."""
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    title = clean(soup.title.get_text(" ")) if soup.title else ""
    headings = [clean(h.get_text(" ")) for h in soup.find_all(["h1", "h2", "h3"])]
    if soup.head:
        soup.head.decompose()          # naslov je već izvučen, ne treba da uđe u tekst
    text = clean(soup.get_text("\n"))
    return {"title": title, "headings": headings, "text": text}


def parse_pdf(path):
    """Tekst PDF-a po stranicama. Stranice su razdvojene znakom \\f u polju text,
    pa se broj stranice može povratiti sa text.split("\\f"). PDF bez tekstualnog
    sloja (skeniran) dobija needs_ocr=True."""
    with pymupdf.open(path) as doc:
        pages = [clean(page.get_text()) for page in doc]
        meta = doc.metadata or {}
    chars = sum(len(p) for p in pages)
    return {
        "title": clean(meta.get("title") or ""),
        "headings": [],
        "text": "\f".join(pages),
        "n_pdf_pages": len(pages),
        "pdf_author_meta": clean(meta.get("author") or ""),
        "needs_ocr": chars / max(len(pages), 1) < 50,
    }


# TODO: ovde dodaješ parsere za pojedinačne tipove, na primer:
#   def parse_gesamtausgabe_list(soup, url): ... vraća i listu tomova (broj GA, naslov, prevodi)
# Parser vraća dict koji se spaja u izlazni red.
PARSERS = {
    # "gesamtausgabe_list": parse_gesamtausgabe_list,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--only", default=None, help="obradi samo ovaj tip stranice")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    db = sqlite3.connect(data_dir / "crawl.db")
    rows = db.execute(
        "SELECT url, local_path, content_type FROM pages "
        "WHERE status = 'done' AND local_path IS NOT NULL ORDER BY url"
    ).fetchall()

    available = {}
    for url, _, _ in rows:
        t = classify(url)
        available[t] = available.get(t, 0) + 1

    out_path = data_dir / "pages.jsonl"
    counts = {}
    skipped_pdf = 0
    if pymupdf is None:
        print("PyMuPDF nije instaliran (pip install pymupdf), PDF-ovi se preskaču.")
    with out_path.open("w", encoding="utf-8") as out:
        for url, local_path, content_type in rows:
            page_type = classify(url)
            if args.only and page_type != args.only:
                continue
            file_path = data_dir / "raw" / local_path
            record = {"url": url, "page_type": page_type, "local_path": local_path,
                      "format": "pdf" if content_type == "application/pdf" else "html"}
            if content_type == "application/pdf":
                if pymupdf is None:
                    skipped_pdf += 1
                    continue
                try:
                    record.update(parse_pdf(file_path))
                except Exception as e:           # oštećen ili zaštićen PDF
                    record.update({"title": "", "headings": [], "text": "", "error": str(e)[:200]})
                record["encoding"] = None
                record["n_chars"] = len(record.get("text", ""))
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                counts[page_type] = counts.get(page_type, 0) + 1
                continue
            body = file_path.read_bytes()
            if content_type == "text/plain":
                record.update({"title": "", "headings": [], "text": clean(body.decode("utf-8", "replace")),
                               "encoding": "utf-8?"})
            else:
                soup = BeautifulSoup(body, PARSER)    # bs4 sam pogađa kodiranje iz bajtova
                parse = PARSERS.get(page_type, parse_generic)
                record.update(parse(soup, url))
                record["encoding"] = soup.original_encoding
            record["n_chars"] = len(record.get("text", ""))
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            counts[page_type] = counts.get(page_type, 0) + 1

    print(f"Zapisano u {out_path}")
    if skipped_pdf:
        print(f"  preskočeno PDF-ova (nema PyMuPDF): {skipped_pdf}")
    for page_type, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {page_type}: {n}")
    if not counts:
        print("Nijedna stranica nije obrađena, pages.jsonl je prazan.")
        if args.only:
            print(f"Među skinutim stranicama nema tipa '{args.only}'. Dostupni tipovi:")
        else:
            print("Nema skinutih stranica. Dostupni tipovi:")
        for page_type, n in sorted(available.items(), key=lambda kv: -kv[1]):
            print(f"  {page_type}: {n}")


if __name__ == "__main__":
    main()