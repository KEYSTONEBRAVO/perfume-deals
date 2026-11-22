# 🏷️ Perfume deals by Keystone

A small personal project that tracks perfume promotions on [Kosmenia](https://kosmenia.ma) every day (men, women and unisex, every size), then:

- 📱 publishes a **phone web app** with every deal, price history and a watchlist
- 📧 sends a **daily email** with the famous perfumes on sale and a full Excel list

**Open the app:** https://keystonebravo.github.io/perfume-deals/

---

## Features

### Phone app
| Feature | What it does |
|---|---|
| **Men / Women / Unisex** | Switch collections at the top |
| **Deals** | Every perfume with photo, best size, price per 100 ml. Search, filters (on sale, famous, new, lowest price, with notes, in stock, brand, minimum discount) and 7 sort orders, including cheapest per 100 ml |
| **Detail view** | Every size with its own price, discount and stock; price-history chart per size; fragrance notes (top / heart / base) and description when Kosmenia lists them; links to Kosmenia and Fragrantica |
| **Watchlist** | Star (☆) perfumes to follow them. Copy the list into `docs/watchlist.txt` to also get email alerts |
| **Stats** | Key figures, best deals on famous perfumes, best value per 100 ml, deals by brand, discount levels, biggest price drops |
| **Theme & offline** | Auto / dark / light switch; opens without internet using the last saved data |

To install it: open the link on your phone → **Add to Home screen** (Chrome: ⋮ menu, Safari: Share button). It then opens full-screen like a normal app.

### Daily email
- Subject with the essentials, for example: `Kosmenia: 953 perfume deals, 3 new, 2 on your watchlist - 22/11/2026`
- **Your watchlist** first: starred perfumes on sale, price drops since the last run, lowest prices ever
- Then the famous perfumes on sale for men, women and unisex, with a **NEW** badge on deals that started or got bigger
- Every size on sale attached as an Excel file (collection, fame rank, sales rank, size, discount, price per 100 ml, lowest price)

---

## How it works

```
GitHub Actions (every day 09:00 UTC, or "Run workflow" from the GitHub app)
   │
   ├─ price_gatherer.py
   │     1. reads Kosmenia's product feed (every size, price, stock) for
   │        parfum-homme, parfum-femme and parfum-mixte, plus the best-sellers order
   │     2. ranks perfumes: famous_perfumes*.txt first, then Kosmenia sales rank
   │     3. flags NEW deals (seen_promos.json), updates the price history per size
   │     4. saves the app data in docs/data/ (one file per collection)
   │     5. sends the email (watchlist + top deals + Excel attachment)
   │
   └─ commits the data → GitHub Pages republishes the app (docs/)
```

**"Famous" ranking.** `famous_perfumes.txt` (men), `famous_perfumes_women.txt` and `famous_perfumes_unisex.txt` are ordered lists of well-known perfumes (Fragrantica-style popularity). A product matches a line when all the words of that line appear in its name. Products that are not on the list but are in Kosmenia's **top 100 best sellers** are also kept.

**Fragrance notes** come from Kosmenia's own product descriptions when they list them. Fragrantica has no public API and does not allow automated copying, so the app links to it instead.

**Price history.** Only price *changes* are stored, per size, so the files stay small. The charts and the "Lowest price" badges fill up after a few days of runs.

---

## Files

| File | Role |
|---|---|
| `price_gatherer.py` | Reads Kosmenia, ranking, price history, email and app data |
| `famous_perfumes*.txt` | Fame rankings for men, women and unisex (editable) |
| `requirements.txt` | Python libraries (`requests`, `beautifulsoup4`, `openpyxl`) |
| `.github/workflows/kosmenia.yml` | Daily schedule and manual run |
| `docs/` | The phone app (`index.html`, `sw.js` for offline, manifest, icons) served by GitHub Pages |
| `docs/watchlist.txt` | Perfumes you want email alerts for (one id per line, copied from the app) |
| `docs/data/` | Data written by each run: `<collection>.json`, `details-*.json`, `history-*.json` |
| `seen_promos.json` | Memory of the last run, used for NEW badges |

---

## Customising

- **Change the fame ranking:** edit the `famous_perfumes*.txt` files. Move lines up or down, add or delete them. Use `|` for alternative spellings, e.g. `bad boy | badboy`.
- **Change the run time:** edit the `cron` line in `.github/workflows/kosmenia.yml` (time in UTC).
- **Settings at the top of `price_gatherer.py`:**
  - `TOP_SELLERS = 100`: best-seller rank still counted as "popular"
  - `CARDS`: cards per collection in the email (keeps it under Gmail's ~102 KB clipping limit)
  - `COLLECTIONS`: which Kosmenia collections are tracked

---

## Privacy & security

- No personal information is stored in this repository. The Gmail address and app password live only in encrypted GitHub secrets, which are hidden in logs and never shared with forks.
- The published data contains only Kosmenia's public product names, prices and links.
- The watchlist is stored in your browser on your phone. It only reaches the repository if you paste it into `docs/watchlist.txt` yourself (perfume ids only).

## Disclaimer

Personal, non-commercial project, not affiliated with Kosmenia. It reads the public collection pages once a day, with a pause between pages. Prices can change at any time; always check the price on the Kosmenia website before buying.

Made for fun by KEYSTONE.
