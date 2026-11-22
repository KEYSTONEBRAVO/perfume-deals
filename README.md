# 🏷️ Perfume deals by Keystone

A small personal project that tracks men's perfume promotions on [Kosmenia](https://kosmenia.ma/collections/parfum-homme) every day, then:

- 📱 publishes a **phone web app** with every deal, price history and a watchlist
- 📧 sends a **daily email** with the famous perfumes on sale and a full Excel list

**Open the app:** https://bouzriadnane.github.io/perfume-deals/

---

## Features

### Phone app
| Tab | What it shows |
|---|---|
| **Deals** | Every product with photo and price. Search, filters (on sale, famous, new, lowest price, in stock, brand, minimum discount) and 6 sort orders |
| **Detail view** | Discount, saving, lowest price ever, regular price, fame rank, Kosmenia sales rank and a price-history chart |
| **Watchlist** | Star (☆) perfumes to follow them; cards show how much the price dropped since you starred it. Saved on your phone only |
| **Stats** | Key figures, best deals on famous perfumes, deals by brand, discount levels, biggest price drops |

To install it: open the link on your phone → **Add to Home screen** (Chrome: ⋮ menu, Safari: Share button). It then opens full-screen like a normal app.

### Daily email
- Subject with the essentials, for example: `Kosmenia: 229 famous perfumes on sale, 3 new - 22/11/2026`
- Summary tiles, then the famous perfumes on sale as cards (most famous first), with a **NEW** badge on deals that started or got bigger since the last run
- The complete list attached as an Excel file (fame rank, sales rank, discount, saving, link)

---

## How it works

```
GitHub Actions (every day 09:00 UTC, or "Run workflow" from the GitHub app)
   │
   ├─ price_gatherer.py
   │     1. reads all collection pages, sorted by Kosmenia best sellers
   │     2. ranks perfumes: famous_perfumes.txt first, then Kosmenia sales rank
   │     3. keeps in-stock products, flags NEW deals (seen_promos.json)
   │     4. saves docs/data/latest.json + docs/data/history.json
   │     5. sends the email (HTML + Excel attachment)
   │
   └─ commits the data → GitHub Pages republishes the app (docs/)
```

**"Famous" ranking.** `famous_perfumes.txt` is an ordered list of well-known men's perfumes (Fragrantica-style popularity). A product matches a line when all the words of that line appear in its name. Products that are not on the list but are in Kosmenia's **top 100 best sellers** are also kept.

**Price history.** Only price *changes* are stored, so the file stays small. The charts and the "Lowest price" badges fill up after a few days of runs.

---

## Files

| File | Role |
|---|---|
| `price_gatherer.py` | Scraper, ranking, email and app data export |
| `famous_perfumes.txt` | Fame ranking (editable) |
| `requirements.txt` | Python libraries (`requests`, `beautifulsoup4`, `openpyxl`) |
| `.github/workflows/kosmenia.yml` | Daily schedule and manual run |
| `docs/` | The phone app (`index.html`, manifest, icons) served by GitHub Pages |
| `docs/data/` | Data written by each run (`latest.json`, `history.json`) |
| `seen_promos.json` | Memory of the last run, used for NEW badges |

---

## Customising

- **Change the fame ranking:** edit `famous_perfumes.txt`. Move lines up or down, add or delete them. Use `|` for alternative spellings, e.g. `bad boy | badboy`.
- **Change the run time:** edit the `cron` line in `.github/workflows/kosmenia.yml` (time in UTC).
- **Settings at the top of `price_gatherer.py`:**
  - `TOP_SELLERS = 100`: best-seller rank still counted as "popular"
  - `MAX_CARDS = 60`: cards in the email (keeps it under Gmail's ~102 KB clipping limit)

---

## Set up your own copy

1. Fork or copy this repository.
2. **Settings → Secrets and variables → Actions**, add two repository secrets:
   - `GMAIL_USER`: the Gmail address that sends and receives the alert
   - `GMAIL_APP_PASSWORD`: a 16-letter [Google app password](https://myaccount.google.com/apppasswords) (requires 2-Step Verification; your normal password will not work)
3. **Settings → Pages:** Deploy from a branch → `main` → `/docs` → Save.
4. **Actions → Kosmenia promotion alert → Run workflow.**

To run it on your own computer instead: `pip install -r requirements.txt`, then `python price_gatherer.py`. It asks for your Gmail address and app password once and stores them in local files that are excluded from Git (`.gitignore`).

---

## Privacy & security

- No personal information is stored in this repository. The Gmail address and app password live only in encrypted GitHub secrets, which are hidden in logs and never shared with forks.
- The published data contains only Kosmenia's public product names, prices and links.
- The watchlist is stored in your browser on your own phone; it is never uploaded.

## Disclaimer

Personal, non-commercial project, not affiliated with Kosmenia. It reads the public collection pages once a day, with a pause between pages. Prices can change at any time; always check the price on the Kosmenia website before buying.

Made for fun by Adnane AKA: KEYSTONE.
