#!/usr/bin/env python3
"""
crawler.py: pažljiv crawler koji se može nastaviti, za sajt beyng.com.

Šta radi:
  1. Čita robots.txt i sitemap (ako postoji) i poštuje pravila i pauzu.
  2. Drži red URL-ova u SQLite bazi (data/crawl.db). Posle prekida (Ctrl+C,
     pad internet veze) nastavlja tačno gde je stalo.
  3. Svaku stranicu čuva kao sirove bajtove u data/raw/, u strukturi koja prati
     putanju URL-a. Ništa se ne dekodira i ne čisti u ovom koraku.
  4. Beleži svaki link (izvor, odredište, tekst linka) u tabelu links. Te veze
     će kasnije postati ivice grafa.

Primeri:
  python crawler.py --limit 50      # probni krug od 50 stranica
  python crawler.py                 # nastavlja do kraja reda
  python crawler.py --delay 3       # sporije, ako želiš da budeš oprezniji

Pre prvog pokretanja zameni USER_AGENT tako da sadrži tvoj pravi e-mail.
"""

import argparse
import html
import logging
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from hashlib import md5, sha256
from pathlib import Path
from urllib import robotparser
from urllib.parse import unquote, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

SEED = "https://www.beyng.com/"
USER_AGENT = "EreignisKG-student-project/0.1 (univerzitetski projekat; kontakt: djuricicmilos6@gmail.com)"
DEFAULT_DELAY = 1.5           # sekundi između zahteva (robots.txt Crawl-delay ima prednost ako je veći)
TIMEOUT = 30
MAX_PDF_BYTES = 100 * 1024 * 1024   # najveći dozvoljeni PDF
MAX_BYTES = 5 * 1024 * 1024   # veće odgovore ne čuvamo
MAX_TRIES = 3

# Ove ekstenzije se beleže u bazi, ali se ne preuzimaju (PDF se preuzima).
SKIP_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".ico", ".bmp", ".webp",
    ".zip", ".gz", ".tar", ".mp3", ".mp4", ".wav", ".doc", ".docx", ".xls",
    ".xlsx", ".ppt", ".pptx", ".epub", ".mobi", ".css", ".js", ".woff", ".woff2",
}
HTML_TYPES = {"text/html", "application/xhtml+xml", ""}   # prazan Content-Type tretiramo kao HTML
PDF_TYPES = {"application/pdf", "application/x-pdf"}
STORE_TYPES = HTML_TYPES | {"text/plain"} | PDF_TYPES

try:
    import lxml  # noqa: F401
    PARSER = "lxml"
except ImportError:
    PARSER = "html.parser"

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    url          TEXT PRIMARY KEY,
    status       TEXT NOT NULL,      -- pending, in_progress, done, redirected, http_error, failed, skipped_type, skipped_robots, skipped_host
    depth        INTEGER NOT NULL,
    http_status  INTEGER,
    content_type TEXT,
    final_url    TEXT,
    local_path   TEXT,
    size         INTEGER,
    fetched_at   TEXT,
    error        TEXT,
    tries        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pages_queue ON pages(status, tries, depth);
CREATE TABLE IF NOT EXISTS links (
    src    TEXT NOT NULL,
    dst    TEXT NOT NULL,
    anchor TEXT,
    PRIMARY KEY (src, dst)
);
"""

log = logging.getLogger("crawler")


def owner(url, workers):
    return int(sha256(url.encode("utf-8")).hexdigest(), 16) % workers


class Crawler:
    def __init__(self, data_dir, seed, delay, workers=1, worker_id=0):
        self.data_dir = Path(data_dir)
        self.raw_dir = self.data_dir / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.data_dir / "crawl.db")
        self.db.executescript(SCHEMA)

        parsed = urlparse(seed)
        self.scheme = parsed.scheme
        self.canon = parsed.netloc.lower()
        bare = self.canon[4:] if self.canon.startswith("www.") else self.canon
        self.hosts = {bare, "www." + bare}
        self.seed = self.normalize(seed)
        self.delay = delay * workers
        self.workers = workers
        self.worker_id = worker_id
        self.db.create_function("url_owner", 1, lambda url: owner(url, workers))

        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.robots = robotparser.RobotFileParser()

    # ---------- pomoćne funkcije ----------

    def normalize(self, url, base=None):
        """Vraća kanonski oblik URL-a ili None ako to nije http(s) link."""
        url = urljoin(base, url.strip()) if base else url.strip()
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.netloc:
            return None
        netloc = p.netloc.lower()
        scheme = p.scheme
        if netloc in self.hosts:            # beyng.com i www.beyng.com su isti sajt
            netloc, scheme = self.canon, self.scheme
        path = p.path or "/"
        if path.endswith("/index.html"):    # /docs/index.html i /docs/ su ista stranica
            path = path[: -len("index.html")]
        return urlunparse((scheme, netloc, path, "", p.query, ""))  # bez #fragmenta

    def is_internal(self, url):
        return urlparse(url).netloc == self.canon

    def local_path_for(self, url):
        """URL -> relativna putanja fajla u data/raw/."""
        if self.workers > 1:
            digest = sha256(url.encode("utf-8")).hexdigest()
            ext = ".pdf" if urlparse(url).path.lower().endswith(".pdf") else ".html"
            return Path(digest[:2]) / (digest + ext)
        p = urlparse(url)
        parts = [s for s in unquote(p.path).split("/") if s not in ("", ".", "..")]
        if p.path.endswith("/") or not parts:
            parts.append("index.html")
        parts = [re.sub(r'[<>:"|?*\\]', "_", s) for s in parts]
        name = parts[-1]
        if "." not in name:
            name += ".html"
        if p.query:
            stem, dot, ext = name.rpartition(".")
            name = f"{stem}__{md5(p.query.encode()).hexdigest()[:8]}.{ext}"
        if len(name) > 120:
            stem, dot, ext = name.rpartition(".")
            name = f"{stem[:80]}__{md5(name.encode()).hexdigest()[:8]}.{ext}"
        parts[-1] = name
        return Path(*parts)

    def add_url(self, url, depth):
        status = "pending"
        if Path(unquote(urlparse(url).path)).suffix.lower() in SKIP_EXT:
            status = "skipped_type"
        self.db.execute(
            "INSERT OR IGNORE INTO pages (url, status, depth) VALUES (?, ?, ?)",
            (url, status, depth),
        )

    def update(self, url, **fields):
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE pages SET {cols} WHERE url = ?", (*fields.values(), url))

    # ---------- robots.txt i sitemap ----------

    def load_robots(self):
        try:
            r = self.session.get(urljoin(self.seed, "/robots.txt"), timeout=TIMEOUT, allow_redirects=False)
        except requests.RequestException as e:
            sys.exit(f"Ne mogu da preuzmem robots.txt ({e}). Proveri internet vezu i adresu sajta.")
        if r.status_code == 404:
            self.robots.parse([])                       # nema pravila, sve je dozvoljeno
        elif r.status_code == 200:
            self.robots.parse(r.text.splitlines())
        else:
            sys.exit(f"robots.txt vraća status {r.status_code}. Zaustavljam se, proveri ručno.")
        crawl_delay = self.robots.crawl_delay(USER_AGENT)
        if crawl_delay:
            self.delay = max(self.delay, float(crawl_delay) * self.workers)
        log.info("robots.txt učitan, pauza između zahteva: %.1f s", self.delay)

    def load_sitemaps(self, sitemap_urls, level=0):
        for sm in sitemap_urls:
            if not self.is_internal(self.normalize(sm) or "") or not self.robots.can_fetch(USER_AGENT, sm):
                continue
            time.sleep(self.delay)
            try:
                r = self.session.get(sm, timeout=TIMEOUT, allow_redirects=False)
            except requests.RequestException as e:
                log.warning("sitemap %s nije dostupan: %s", sm, e)
                continue
            if r.status_code != 200:
                continue
            for loc in re.findall(r"<loc>\s*(.*?)\s*</loc>", r.text, flags=re.S):
                loc = html.unescape(loc)
                if loc.lower().endswith(".xml") and level < 2:
                    self.load_sitemaps([loc], level + 1)
                else:
                    url = self.normalize(loc)
                    if url and self.is_internal(url):
                        self.add_url(url, 0)
        self.db.commit()

    # ---------- preuzimanje ----------

    def retry_or_fail(self, url, reason):
        tries = self.db.execute("SELECT tries FROM pages WHERE url = ?", (url,)).fetchone()[0] + 1
        status = "failed" if tries >= MAX_TRIES else "pending"
        self.update(url, status=status, tries=tries, error=reason[:300])
        log.warning("%s: %s (pokušaj %d/%d)", url, reason, tries, MAX_TRIES)

    def extract_links(self, url, body, depth):
        soup = BeautifulSoup(body, PARSER)
        base_tag = soup.find("base", href=True)
        base = urljoin(url, base_tag["href"]) if base_tag else url
        for a in soup.find_all("a", href=True):
            dst = self.normalize(a["href"], base=base)
            if not dst:
                continue
            anchor = " ".join(a.get_text(" ", strip=True).split())[:200]
            self.db.execute("INSERT OR IGNORE INTO links VALUES (?, ?, ?)", (url, dst, anchor))
            if self.is_internal(dst):
                self.add_url(dst, depth + 1)

    def crawl_one(self, url, depth):
        """Obrađuje jedan URL. Vraća True ako je poslat mrežni zahtev."""
        if not self.robots.can_fetch(USER_AGENT, url):
            self.update(url, status="skipped_robots")
            return False
        try:
            r = self.session.get(url, timeout=TIMEOUT, stream=True, allow_redirects=False)
        except requests.RequestException as e:
            self.retry_or_fail(url, f"mrežna greška: {e}")
            return True

        with r:
            code = r.status_code
            if code in (429, 503):
                wait = r.headers.get("Retry-After", "")
                time.sleep(min(int(wait), 120) if wait.isdigit() else 30)
                self.update(url, status="pending", error=f"HTTP {code}")
                self.db.commit()
                raise SystemExit(f"HTTP {code}: stop all workers and review server limits before resuming.")
            if code >= 500:
                self.retry_or_fail(url, f"HTTP {code}")
                return True
            if code >= 400:
                self.update(url, status="http_error", http_status=code)
                return True

            if 300 <= code < 400:
                location = r.headers.get("Location")
                final = self.normalize(location, base=url) if location else None
                if final and self.is_internal(final) and final != url:
                    self.add_url(final, depth)
                    self.update(url, status="redirected", http_status=code, final_url=final)
                else:
                    self.update(url, status="skipped_host" if final and not self.is_internal(final) else "failed", http_status=code, final_url=final, error="Redirect not followed")
                return True

            final = self.normalize(r.url)
            if final is None or not self.is_internal(final):
                self.update(url, status="skipped_host", http_status=code, final_url=r.url)
                return True
            if final != url:                      # preusmeravanje: završni URL dolazi na red kao samostalan
                self.add_url(final, depth)
                self.update(url, status="redirected", http_status=code, final_url=final)
                return True

            ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
            is_pdf = ctype in PDF_TYPES or (
                ctype == "application/octet-stream" and urlparse(url).path.lower().endswith(".pdf")
            )
            if is_pdf:
                ctype = "application/pdf"
            if ctype not in STORE_TYPES:
                self.update(url, status="skipped_type", http_status=code, content_type=ctype)
                return True

            max_bytes = MAX_PDF_BYTES if is_pdf else MAX_BYTES
            chunks, size = [], 0
            for chunk in r.iter_content(65536):
                size += len(chunk)
                if size > max_bytes:
                    self.update(url, status="failed", http_status=code,
                                error=f"odgovor veći od {max_bytes // (1024 * 1024)} MB")
                    return True
                chunks.append(chunk)
            body = b"".join(chunks)

        rel = self.local_path_for(url)
        target = self.raw_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)                  # sirovi bajtovi, bez dekodiranja

        if ctype in HTML_TYPES:
            self.extract_links(url, body, depth)
        self.update(
            url, status="done", http_status=code, content_type=ctype or "text/html",
            final_url=url, local_path=str(rel), size=len(body),
            fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), error=None,
        )
        return True

    # ---------- glavna petlja ----------

    def stats(self):
        rows = self.db.execute("SELECT status, COUNT(*) FROM pages GROUP BY status").fetchall()
        return ", ".join(f"{s}={n}" for s, n in sorted(rows))

    def run(self, limit=None):
        self.db.execute("UPDATE pages SET status = 'pending' WHERE status = 'in_progress'")
        self.db.commit()
        processed = 0
        try:
            while limit is None or processed < limit:
                row = self.db.execute(
                    "SELECT url, depth FROM pages WHERE status = 'pending' AND url_owner(url) = ? "
                    "ORDER BY tries, depth, rowid LIMIT 1", (self.worker_id,)
                ).fetchone()
                if not row:
                    log.info("Red je prazan.")
                    break
                url, depth = row
                self.update(url, status="in_progress")
                requested = self.crawl_one(url, depth)
                self.db.commit()
                processed += 1
                if processed % 25 == 0:
                    log.info("obrađeno %d u ovom krugu | %s", processed, self.stats())
                if requested:
                    time.sleep(self.delay)
        except KeyboardInterrupt:
            log.info("Prekinuto. Stanje je sačuvano, ponovno pokretanje nastavlja.")
        finally:
            self.db.commit()
            log.info("Kraj kruga | %s", self.stats())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", default=SEED)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    ap.add_argument("--limit", type=int, default=None, help="najviše toliko URL-ova u ovom krugu")
    ap.add_argument("--no-sitemap", action="store_true")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--worker-id", type=int, default=0)
    args = ap.parse_args()
    if args.workers < 1 or not 0 <= args.worker_id < args.workers:
        ap.error("Invalid worker configuration")
    if args.delay < DEFAULT_DELAY:
        ap.error("Delay must be at least 1.5 seconds")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if "example.com" in USER_AGENT:
        log.warning("USER_AGENT još sadrži TVOJ_EMAIL@example.com. Zameni ga pravim kontaktom.")

    crawler = Crawler(args.data_dir, args.seed, args.delay, args.workers, args.worker_id)
    first_run = crawler.db.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 0
    crawler.load_robots()
    crawler.add_url(crawler.seed, 0)
    crawler.db.commit()
    if first_run and not args.no_sitemap and args.worker_id == 0:
        sitemaps = crawler.robots.site_maps() or [urljoin(crawler.seed, "/sitemap.xml")]
        crawler.load_sitemaps(sitemaps)
    try:
        crawler.run(args.limit)
    finally:
        crawler.db.close()


if __name__ == "__main__":
    main()