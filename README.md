# EreignisKG
Knowledge Graph construction and GraphRAG-powered semantic search for beyng.com (Ereignis), a comprehensive digital collection of Martin Heidegger's works, translations, and scholarly research.

**Website:** [beyng.com](https://beyng.com/)

## Web scraping — distribuirano prikupljanje podataka

### 1. Cilj

U okviru projekta EreignisKG implementiran je distribuirani web crawler za prikupljanje podataka sa sajta [beyng.com](https://www.beyng.com/), koji sadrži tekstove i druge materijale povezane sa filozofijom Martina Hajdegera.

Prikupljeni podaci predstavljaju osnovu za dalju obradu pomoću velikih jezičkih modela (LLM), ekstrakciju entiteta i relacija, izgradnju grafa znanja u Neo4j i implementaciju semantičke pretrage.

Radi efikasnije raspodele posla, scraping je organizovan tako da tri studenta istovremeno prikupljaju različite stranice sajta sa svojih računara.

### 2. Korišćene tehnologije

| Tehnologija | Namena |
|---|---|
| Python | Implementacija crawlera |
| Requests | Slanje HTTP zahteva |
| BeautifulSoup i lxml | Parsiranje HTML dokumenata |
| SQLite | Evidencija URL-ova, statusa i linkova |
| SHA-256 | Deterministička raspodela URL-ova |
| urllib.robotparser | Provera pravila iz robots.txt |
| uv | Upravljanje Python okruženjem i zavisnostima |
| Git/GitHub | Verziona kontrola i deljenje rezultata |

### 3. Struktura projekta

```text
EreignisKG/
├── src/
│   └── ereigniskg/
│       ├── __init__.py
│       ├── crawler.py
│       ├── split_crawl.py
│       ├── merge_crawls.py
│       ├── inventory.py
│       └── parse.py
├── data/
│   ├── worker_0/
│   ├── worker_1/
│   ├── worker_2/
│   └── merged_round_1/
├── pyproject.toml
├── uv.lock
└── README.md
```

Glavne komponente sistema su:

- **`crawler.py`** — preuzimanje HTML stranica, otkrivanje novih URL-ova i evidencija rezultata.
- **`split_crawl.py`** — raspodela URL-ova između tri workera.
- **`merge_crawls.py`** — objedinjavanje rezultata pojedinačnih workera.
- **`inventory.py`** — generisanje inventara prikupljenih stranica.
- **`parse.py`** — priprema i izdvajanje tekstualnog sadržaja iz preuzetih dokumenata.

### 4. Princip distribuiranog scrapinga

Svaki student pokreće crawler sa različitim `worker-id` parametrom i koristi zasebnu SQLite bazu.

Za određivanje workera kojem URL pripada koristi se deterministička hash funkcija:

```python
from hashlib import sha256

def owner(url, workers):
    return int(
        sha256(url.encode("utf-8")).hexdigest(), 16
    ) % workers
```

Za tri workera rezultat je `0`, `1` ili `2`.

Ovakva raspodela omogućava da svaki URL ima unapred određenog workera i smanjuje mogućnost duplog preuzimanja.

Tokom rada crawler otkriva nove linkove. Pošto novootkriveni URL može pripadati drugom workeru, nakon jednog kruga rada potrebno je objediniti baze i ponovo raspodeliti preostale URL-ove.

### 5. Instalacija

Projekat koristi `uv` za upravljanje zavisnostima.

Instalacija biblioteka:

```powershell
uv sync
```

Provera dostupnih komandi:

```powershell
uv run python -m ereigniskg.crawler --help
uv run python -m ereigniskg.split_crawl --help
uv run python -m ereigniskg.merge_crawls --help
```

### 6. Inicijalizacija početnih baza

Inicijalizaciju prvog kruga izvršava jedan student:

```powershell
uv run python -m ereigniskg.crawler --workers 3 --worker-id 0 --data-dir data/worker_0 --limit 0
```

Nakon formiranja početnog reda URL-ova, pokreće se raspodela:

```powershell
uv run python -m ereigniskg.split_crawl --source data/worker_0/crawl.db --root data --workers 3
```

Time se formiraju zasebne baze za sva tri workera.

### 7. Pokretanje crawlera

**Worker 0**

```powershell
uv run python -m ereigniskg.crawler --workers 3 --worker-id 0 --data-dir data/worker_0 --no-sitemap
```

**Worker 1**

```powershell
uv run python -m ereigniskg.crawler --workers 3 --worker-id 1 --data-dir data/worker_1 --no-sitemap
```

**Worker 2**

```powershell
uv run python -m ereigniskg.crawler --workers 3 --worker-id 2 --data-dir data/worker_2 --no-sitemap
```

Za probno pokretanje može se dodati `--limit 20`.

Crawler koristi postojeću SQLite bazu, što omogućava nastavak rada nakon prekida bez ponovnog preuzimanja već uspešno obrađenih stranica.

### 8. Čuvanje rezultata

Svaki worker podatke čuva u svom direktorijumu:

```text
data/
├── worker_0/
│   ├── crawl.db
│   └── raw/
├── worker_1/
│   ├── crawl.db
│   └── raw/
└── worker_2/
    ├── crawl.db
    └── raw/
```

Baza `crawl.db` sadrži informacije o URL-ovima i njihovim statusima, kao i otkrivenim hiperlinkovima.

Najvažniji statusi su:

| Status | Značenje |
|---|---|
| `pending` | URL čeka obradu |
| `in_progress` | Obrada je u toku |
| `done` | Stranica je uspešno preuzeta |
| `http_error` | HTTP greška |
| `failed` | Neuspešno preuzimanje |
| `skipped_type` | Nepodržani tip datoteke |
| `skipped_robots` | URL nije dozvoljen prema robots.txt |
| `redirected` | URL preusmerava na drugu adresu |

### 9. Rezultati prvog kruga — worker 2

Prvi duži krug scrapinga workera 2 završen je 23. novembra 2026. godine.

| Metrika | Broj |
|---|---:|
| Uspešno preuzete stranice (`done`) | 4.236 |
| URL-ovi koji čekaju obradu (`pending`) | 1.357 |
| HTTP greške (`http_error`) | 37 |
| Preskočeni tipovi (`skipped_type`) | 6 |

Završni ispis crawlera:

```text
INFO Red je prazan.
INFO Kraj kruga | done=4236, http_error=37, pending=1357, skipped_type=6
```

Poruka „Red je prazan“ znači da worker 2 u tom trenutku nema dodatnih URL-ova koji su njemu dodeljeni. Preostali URL-ovi sa statusom `pending` biće uzeti u obzir prilikom narednog objedinjavanja i raspodele.

Ovi podaci predstavljaju rezultat samo workera 2, a ne zbirne rezultate sva tri studenta.

### 10. Obrada grešaka i odgovorno preuzimanje

Crawler proverava `robots.txt`, beleži HTTP i mrežne greške i omogućava ponovne pokušaje za određene neuspešne zahteve.

Tokom rada zabeležene su povremene DNS greške i prekidi mrežne konekcije.

Prilikom paralelnog rada potrebno je voditi računa o ukupnoj učestalosti zahteva prema serveru. Ako se pojave indikacije ograničavanja pristupa ili preopterećenja (`403`, `429`, `503`), scraping treba obustaviti i proveriti uslove pristupa.

Preuzimanje dostupnog sadržaja i dozvola za njegovu redistribuciju nisu isto. Pre javnog deljenja punih HTML dokumenata potrebno je proveriti odgovarajuća prava i dozvole.

### 11. GitHub sinhronizacija

Za objedinjavanje rezultata koristi se zajednička Git grana `scrape`.

Svaki student šalje podatke svog workera nakon zaustavljanja crawlera.

Primer za worker 2:

```powershell
git add data/worker_2/
git commit -m "Worker 2 - round 1 results"
git pull --rebase origin scrape
git push origin scrape
```

Odvojeni direktorijumi smanjuju rizik od konflikata pri istovremenom radu, jer svaki student menja sopstvenu SQLite bazu.

### 12. Objedinjavanje rezultata

Kada sva tri studenta završe krug rada, rezultati se objedinjuju:

```powershell
uv run python -m ereigniskg.merge_crawls --root data --output data/merged_round_1 --workers 3
```

Nakon toga ponovo se raspodeljuju preostali i novootkriveni URL-ovi:

```powershell
uv run python -m ereigniskg.split_crawl --source data/merged_round_1/crawl.db --root data --workers 3
```

Ovaj postupak ponavlja se dok se ne obrade svi relevantni otkriveni URL-ovi.

### 13. Dalji koraci

Nakon završetka scrapinga planirana je obrada prikupljenih stranica pomoću modula `inventory.py` i `parse.py`.

Dobijeni tekstualni podaci koristiće se za LLM ekstrakciju entiteta i relacija, izgradnju grafa znanja u Neo4j i razvoj semantičke pretrage.

### 14. Ograničenja

Trenutna implementacija ima nekoliko ograničenja:

- Workeri ne razmenjuju novootkrivene URL-ove u realnom vremenu.
- Potrebno je periodično objedinjavanje i ponovno raspoređivanje URL-ova.
- Hash raspodela ne garantuje potpuno jednako opterećenje.
- Mrežne i HTTP greške mogu ostaviti deo URL-ova neobrađenim.
- Veliki broj HTML datoteka i SQLite baze povećavaju veličinu Git repozitorijuma.
- Potrebno je proveriti potpunost konačnog korpusa i dozvole za dalje korišćenje sadržaja.
