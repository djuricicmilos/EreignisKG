import argparse
import sqlite3
import sys
from pathlib import Path

EDITS = [
    (
        '''# Ove ekstenzije se beleže u bazi, ali se ne preuzimaju (npr. PDF-ovi radova).
SKIP_EXT = {
    ".pdf", ".jpg",''',
        '''# Ove ekstenzije se beleže u bazi, ali se ne preuzimaju (PDF se preuzima).
SKIP_EXT = {
    ".jpg",''',
    ),
    (
        '''MAX_BYTES = 5 * 1024 * 1024''',
        '''MAX_PDF_BYTES = 100 * 1024 * 1024   # najveći dozvoljeni PDF
MAX_BYTES = 5 * 1024 * 1024''',
    ),
    (
        '''STORE_TYPES = HTML_TYPES | {"text/plain"}''',
        '''PDF_TYPES = {"application/pdf", "application/x-pdf"}
STORE_TYPES = HTML_TYPES | {"text/plain"} | PDF_TYPES''',
    ),
    (
        '''            digest = sha256(url.encode("utf-8")).hexdigest()
            return Path(digest[:2]) / (digest + ".html")''',
        '''            digest = sha256(url.encode("utf-8")).hexdigest()
            ext = ".pdf" if urlparse(url).path.lower().endswith(".pdf") else ".html"
            return Path(digest[:2]) / (digest + ext)''',
    ),
    (
        '''            ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ctype not in STORE_TYPES:''',
        '''            ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
            is_pdf = ctype in PDF_TYPES or (
                ctype == "application/octet-stream" and urlparse(url).path.lower().endswith(".pdf")
            )
            if is_pdf:
                ctype = "application/pdf"
            if ctype not in STORE_TYPES:''',
    ),
    (
        '''            chunks, size = [], 0
            for chunk in r.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    self.update(url, status="failed", http_status=code, error="odgovor veći od MAX_BYTES")''',
        '''            max_bytes = MAX_PDF_BYTES if is_pdf else MAX_BYTES
            chunks, size = [], 0
            for chunk in r.iter_content(65536):
                size += len(chunk)
                if size > max_bytes:
                    self.update(url, status="failed", http_status=code,
                                error=f"odgovor veći od {max_bytes // (1024 * 1024)} MB")''',
    ),
]


def patch_code(path):
    try:
        src = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        sys.exit(f"{path} nije u UTF-8 kodiranju. Sačuvaj ga kao UTF-8 pa pokreni ponovo.")
    if "PDF_TYPES" in src:
        print(f"{path} već podržava PDF, preskačem izmenu koda.")
        return
    for i, (old, new) in enumerate(EDITS, 1):
        if src.count(old) != 1:
            sys.exit(
                f"Izmena {i} se ne uklapa u {path} (deo koda je drugačiji nego što očekujem). "
                "Ništa nije upisano. Pošalji mi taj deo fajla pa ga prilagođavam."
            )
        src = src.replace(old, new)
    backup = Path(str(path) + ".bak")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(src, encoding="utf-8")
    print(f"{path} izmenjen, kopija stare verzije: {backup}")


def requeue_pdfs(data_dir):
    db_path = Path(data_dir) / "crawl.db"
    if not db_path.exists():
        print(f"Baza {db_path} ne postoji, preskačem vraćanje PDF-ova u red.")
        return
    con = sqlite3.connect(db_path)
    n = con.execute(
        "UPDATE pages SET status = 'pending', depth = -1, tries = 0 "
        "WHERE status = 'redirected' AND substr(lower(url), -4) = '.pdf'"
    ).rowcount
    con.commit()
    con.close()
    print(f"PDF-ova vraćeno u red: {n}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--crawler", default="crawler.py")
    ap.add_argument("--data-dir", default="data")
    args = ap.parse_args()
    patch_code(Path(args.crawler))
    requeue_pdfs(args.data_dir)


if __name__ == "__main__":
    main()