"""Kosmenia perfume deals: reads the shop every day, keeps a price history,
publishes the phone app data (docs/data) and emails the best deals."""
import json
import os
import re
import time
from datetime import date
from html import escape

import requests
from bs4 import BeautifulSoup
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

HERE = os.path.dirname(os.path.abspath(__file__))
SHOP = "https://kosmenia.ma"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PriceGatherer/2.0"}
PAUSE = 1                 # seconds between pages - be polite to the site

# (app id, Kosmenia collection, fame ranking file)
COLLECTIONS = [
    ("men", "parfum-homme", "famous_perfumes.txt"),
    ("women", "parfum-femme", "famous_perfumes_women.txt"),
    ("unisex", "parfum-mixte", "famous_perfumes_unisex.txt"),
]
LABELS = {"men": "Men", "women": "Women", "unisex": "Unisex"}

TOP_SELLERS = 100         # also "popular": anything in a collection's top-100 best sellers
CARDS = {"men": 20, "women": 20, "unisex": 8}   # cards per section (keeps the email < 102 KB)
WATCH_CARDS = 15
SEEN_FILE = os.path.join(HERE, "seen_promos.json")         # memory for NEW badges
APP_URL = os.environ.get("APP_URL", "")                     # link to the phone app, shown in the email

# No personal info in this file: the Gmail address comes from the GMAIL_USER
# secret on GitHub, or from "gmail_user.txt" next to the script when run locally.
USER_FILE = os.path.join(HERE, "gmail_user.txt")
FROM_EMAIL = None
TO_EMAIL = None
APP_DIR = os.path.join(HERE, "docs")          # the phone app (GitHub Pages)
DATA_DIR = os.path.join(APP_DIR, "data")
WATCH_FILE = os.path.join(APP_DIR, "watchlist.txt")   # synced watchlist (perfume ids)


def get_gmail_user():
    user = os.environ.get("GMAIL_USER", "").strip()
    if not user and os.path.exists(USER_FILE):
        user = open(USER_FILE, encoding="utf-8").read().strip()
    while "@" not in user:
        user = input("Your Gmail address: ").strip()
        if "@" in user:
            with open(USER_FILE, "w", encoding="utf-8") as f:
                f.write(user)
    return user

# The app password is never written in the code. The script looks for it in:
#   1. the GMAIL_APP_PASSWORD environment variable, or
#   2. a file "gmail_app_password.txt" next to this script.
# If neither exists, it asks you once and saves it to that file.
PASSWORD_FILE = os.path.join(HERE, "gmail_app_password.txt")


def get_app_password():
    pwd = os.environ.get("GMAIL_APP_PASSWORD")
    if pwd:
        return pwd.replace(" ", "").strip()
    if os.path.exists(PASSWORD_FILE):
        with open(PASSWORD_FILE, encoding="utf-8") as f:
            pwd = f.read().replace(" ", "").replace("\xa0", "").strip().lower()
        if len(pwd) == 16 and pwd.isalpha() and pwd.isascii():
            return pwd
        os.remove(PASSWORD_FILE)  # invalid content - don't keep it on disk
        print("The saved password was not a valid app password - deleted it.")
    while True:
        pwd = input("Paste your 16-letter Gmail app password and press Enter: ")
        pwd = pwd.replace(" ", "").replace("\xa0", "").strip().lower()
        if len(pwd) == 16 and pwd.isalpha() and pwd.isascii():
            break
        print("That is not an app password (it must be exactly 16 letters, like 'abcd efgh ijkl mnop').\n"
              "Your normal Gmail password will NOT work. Create one at https://myaccount.google.com/apppasswords")
    with open(PASSWORD_FILE, "w", encoding="utf-8") as f:
        f.write(pwd)
    print(f"Saved to {PASSWORD_FILE} - you won't be asked again.")
    return pwd


APP_PASSWORD = None


def send_email(subject, body, to_email, attachment=None, filename=None):
    from email.mime.application import MIMEApplication
    msg = MIMEMultipart("mixed")
    msg["From"] = FROM_EMAIL
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "html", "utf-8"))
    if attachment:
        part = MIMEApplication(attachment, Name=filename)
        part["Content-Disposition"] = f'attachment; filename="{filename}"'
        msg.attach(part)

    def via_ssl():  # port 465, encrypted from the start
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(FROM_EMAIL, APP_PASSWORD)
            server.sendmail(FROM_EMAIL, to_email, msg.as_string())

    def via_starttls():  # port 587, upgraded to encryption
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(FROM_EMAIL, APP_PASSWORD)
            server.sendmail(FROM_EMAIL, to_email, msg.as_string())

    last_error = None
    for name, method in (("SSL/465", via_ssl), ("STARTTLS/587", via_starttls)):
        try:
            print(f"Sending email via {name}...")
            method()
            print(f"Email sent to {to_email}.")
            return
        except smtplib.SMTPAuthenticationError:
            if os.path.exists(PASSWORD_FILE):
                os.remove(PASSWORD_FILE)  # so you're asked again next run
            raise SystemExit("Gmail refused the app password. Create a new one at "
                             "https://myaccount.google.com/apppasswords and run the script again.")
        except (smtplib.SMTPServerDisconnected, OSError) as e:
            print(f"  {name} failed: {e}")
            last_error = e

    raise SystemExit(
        f"Could not connect to Gmail ({last_error}).\n"
        "Most likely an antivirus 'mail shield' or firewall is blocking it.\n"
        "Or the saved app password is wrong: delete gmail_app_password.txt and run again.")



# ---------------------------------------------------------------------------
# Reading Kosmenia
# ---------------------------------------------------------------------------
def get(url, as_json=False):
    """GET with retries. Returns text / parsed JSON, or None after 3 failures."""
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json() if as_json else r.text
        except (requests.RequestException, ValueError) as e:
            print(f"  ! {url} failed ({e}), retry {attempt + 1}/3")
            time.sleep(3)
    return None


def fetch_products(collection):
    """All products of a collection from Kosmenia's JSON feed (every size, price, stock)."""
    products = []
    for page in range(1, 30):
        data = get(f"{SHOP}/collections/{collection}/products.json?limit=250&page={page}", as_json=True)
        if not data or not data.get("products"):
            break
        products += data["products"]
        time.sleep(PAUSE)
    return products


def fetch_sales_ranks(collection):
    """{handle: rank} from the collection sorted by 'best-selling' (real sales order)."""
    ranks = {}
    for page in range(1, 80):
        html = get(f"{SHOP}/collections/{collection}?sort_by=best-selling&page={page}")
        handles = re.findall(r'href="/collections/[^"/]+/products/([^"?#]+)"[^>]*class="product-grid-item"', html or "")
        if not handles:
            break
        for h in handles:
            ranks.setdefault(h, len(ranks) + 1)
        time.sleep(PAUSE)
    return ranks


# ---------------------------------------------------------------------------
# Cleaning one product
# ---------------------------------------------------------------------------
def text_of(html):
    return re.sub(r"\s+", " ", BeautifulSoup(html or "", "html.parser").get_text(" ")).strip()


def split_title(title_html):
    """'<strong> DIOR <br> SAUVAGE </strong><br> Eau de Parfum' -> ('Dior', 'Sauvage', 'Eau de Parfum')."""
    m = re.search(r"<strong>(.*?)</strong>(.*)", title_html or "", re.S | re.I)
    if not m:
        return "", text_of(title_html), ""
    parts = [text_of(p) for p in re.split(r"<br\s*/?>", m.group(1), flags=re.I)]
    parts = [p for p in parts if p]
    brand = parts[0].title() if parts else ""
    name = " ".join(parts[1:]).title() if len(parts) > 1 else brand
    return brand, name, text_of(m.group(2))


def tag_value(tags, prefix):
    for t in tags or []:
        if t.startswith(prefix):
            return t[len(prefix):].strip()
    return ""


def parse_size(title):
    """'100 ml' -> (100.0, False); 'Échantillon - 1,5 ml' -> (1.5, True)."""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*ml", title or "", re.I)
    ml = float(m.group(1).replace(",", ".")) if m else None
    sample = "chantillon" in (title or "").lower() or (ml is not None and ml < 5)
    return ml, sample


def block_text(html):
    """Description as plain text, one line per paragraph / <br> / heading (keeps the structure)."""
    html = re.sub(r"(?i)<br\s*/?>|</?(p|div|li|ul|h[1-6]|tr)\b[^>]*>", "\n", html or "")
    text = BeautifulSoup(html, "html.parser").get_text("")
    lines = [re.sub(r"[ \t ]+", " ", l).strip() for l in text.split("\n")]
    return "\n".join(l for l in lines if l)


# "Tête :", "Note de tête :", "Notes de cœur :", "Fond :" ... (a colon is required, so prose like
# "sur un fond de cèdre" is never taken for a label)
_LBL = {
    "top": r"(?:notes?\s+de\s+)?t[êe]te",
    "heart": r"(?:notes?\s+de\s+)?c(?:œ|oe)ur",
    "base": r"(?:notes?\s+de\s+)?fond",
}
_ANY_LBL = r"(?<![A-Za-zÀ-ÿ])(?:" + "|".join(_LBL.values()) + r")\s*:"


def extract_notes(text):
    """Top / heart / base notes from Kosmenia's description (stops at the next label or line)."""
    notes = {}
    for key, label in _LBL.items():
        m = re.search(rf"(?<![A-Za-zÀ-ÿ]){label}\s*:\s*(.+?)(?={_ANY_LBL}|\n|$)", text, re.I)
        if m:
            value = re.split(r"(?i)\b(livraison|formats?|découvrez)\b", m.group(1))[0]
            value = value.strip(" .:;-–")
            if 1 < len(value) <= 200:
                notes[key] = value
    if not notes:   # no top/heart/base: a plain list under "Notes parfumées" / "Notes olfactives"
        m = re.search(r"(?i)notes?\s+(?:olfactives?|parfum[ée]es?)\s*:?\s*\n((?:.*\n?){1,8})", text)
        if m:
            items = []
            for line in m.group(1).split("\n"):
                if re.match(r"(?i)\s*(formats?|livraison|découvrez|famille|si vous|nous vous)\b", line):
                    break
                line = line.strip(" -–•·").strip()
                if line:
                    items.append(line)
            value = ", ".join(items)
            if 1 < len(value) <= 200:
                notes["main"] = value
    return notes


def extract_family(text):
    """'Famille olfactive : Parfum Oriental Boisé' -> 'Oriental Boisé'."""
    m = re.search(r"famille\s+olfactive\s*:\s*(.+?)(?=\n|$|\bnotes?\b|\bt[êe]te\s*:|\bformats?\b|\blivraison\b)", text, re.I)
    if not m:
        return ""
    fam = re.sub(r"(?i)^parfums?\s+", "", m.group(1)).strip(" .:;-–")
    fam = re.sub(r"\bE(pic|pice)", r"É\1", fam)          # "Epicé" and "Épicé" -> one family
    return fam[:1].upper() + fam[1:] if 2 < len(fam) < 40 else ""


_DESC_STOP = re.compile(r"(?im)^\s*(famille\s+olfactive|notes?\s+(olfactives?|parfum[ée]es?)|pyramide|"
                        r"(notes?\s+de\s+)?t[êe]te\s*:|livraison|formats?\b)")


def short_description(text):
    """The story part of the description (before notes, formats and delivery info), max ~600 chars."""
    m = _DESC_STOP.search(text)
    text = (text[:m.start()] if m else text).replace("\n", " ").strip()
    if len(text) <= 600:
        return text
    cut = text[:600]
    end = cut.rfind(". ")
    return cut[:end + 1] if end > 250 else cut.rstrip() + "…"


def build_product(raw, gender, famous, ranks):
    brand, name, kind = split_title(raw.get("title"))
    brand = tag_value(raw.get("tags"), "Marques_") or brand
    kind = kind or tag_value(raw.get("tags"), "Concentrations_")
    image = (raw.get("images") or [{}])[0].get("src", "")
    if image:
        image += ("&" if "?" in image else "?") + "width=300"
    variants = []
    for v in raw.get("variants", []):
        price = to_number(v.get("price"))
        if price is None:
            continue
        old = to_number(v.get("compare_at_price"))
        old = old if old and old > price else None
        ml, sample = parse_size(v.get("title"))
        variants.append({
            "size": (v.get("title") or "").strip(), "ml": ml, "sample": sample,
            "p": price, "o": old, "d": round((old - price) / old * 100) if old else 0,
            "st": bool(v.get("available")),
        })
    desc_text = block_text(raw.get("body_html"))
    notes = extract_notes(desc_text)
    title = f"{brand} {name} {kind}".strip()
    handle = raw["handle"]
    return {
        "gender": gender, "handle": handle, "title": title, "brand": brand, "name": name, "kind": kind,
        "link": f"{SHOP}/products/{handle}", "image": image,
        "fame": fame_rank(title, handle, famous), "rank": ranks.get(handle),
        "variants": variants, "notes": notes, "family": extract_family(desc_text),
        "desc": short_description(desc_text),
    }


def best_variant(p):
    """The size shown on cards: biggest in-stock discount, else the 100 ml-ish in-stock size."""
    full = [v for v in p["variants"] if not v["sample"]] or p["variants"]
    on_sale = [v for v in full if v["d"] > 0 and v["st"]]
    if on_sale:
        return max(on_sale, key=lambda v: (v["d"], -abs((v["ml"] or 100) - 100)))   # 100 ml on ties
    pool = [v for v in full if v["st"]] or full
    return min(pool, key=lambda v: abs((v["ml"] or 100) - 100)) if pool else None


def summarise(p):
    v = best_variant(p)
    p["best"] = v
    p["on_sale"] = bool(v and v["d"] > 0 and v["st"])
    p["in_stock"] = any(x["st"] for x in p["variants"])
    p["discount"] = v["d"] if p["on_sale"] else 0
    p["saving"] = round((v["o"] or v["p"]) - v["p"], 2) if v else 0
    p["per100"] = round(v["p"] / v["ml"] * 100) if v and v["ml"] else None
    return p


# ---------------------------------------------------------------------------
# Fame ranking
# ---------------------------------------------------------------------------
def load_famous(path):
    """List of (rank, [alternatives -> [words]]) from a famous_perfumes*.txt file."""
    famous = []
    if not os.path.exists(path):
        return famous
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip().lower()
            if line:
                alts = [alt.split() for alt in line.split("|") if alt.strip()]
                famous.append((len(famous) + 1, alts))
    return famous


def normalize(text):
    """Lowercase words without accents, with l'homme / d'hermes style names glued."""
    import unicodedata
    text = unicodedata.normalize("NFD", text.lower()).encode("ascii", "ignore").decode()
    text = text.replace("'", "")
    text = re.sub(r"\b([ld])[\s-]+(homme|hermes)\b", r"\1\2", text)
    return set(re.split(r"[^a-z0-9]+", text)) - {""}


def fame_rank(title, handle, famous):
    words = normalize(title) | normalize(handle)
    for rank, alts in famous:
        if any(all(w in words for w in alt) for alt in alts):
            return rank
    return None


def popularity_key(p):
    """Famous perfumes first (in fame order), then by Kosmenia sales rank."""
    return (p["fame"] or 10_000, p["rank"] or 10_000)


def is_popular(p):
    return p["fame"] is not None or (p["rank"] or 10_000) <= TOP_SELLERS


def to_number(value):
    """'1,067.00 DH' / '1018.80' -> float (None if not a number)."""
    try:
        return float(str(value).replace("DH", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Memory: NEW badges, price history, watchlist
# ---------------------------------------------------------------------------
def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data, pretty=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True,
                  **({"indent": 0} if pretty else {"separators": (",", ":")}))


def mark_new(products):
    """NEW = a size whose promotion started or got bigger since the last run."""
    seen = load_json(SEEN_FILE, None)
    current = {}
    for p in products:
        p["new"] = False
        for v in p["variants"]:
            if v["d"] > 0 and v["st"] and not v["sample"]:
                key = f'{p["gender"]}|{p["handle"]}|{v["size"]}'
                current[key] = v["d"]
                v["new"] = seen is not None and v["d"] > seen.get(key, 0)
                p["new"] = p["new"] or v["new"]
    save_json(SEEN_FILE, current, pretty=True)
    if seen is None:
        print("First run: no NEW badges yet (nothing to compare with).")


def update_history(products, gender, today):
    """docs/data/history-<gender>.json: per product and size, the dates its price changed."""
    path = os.path.join(DATA_DIR, f"history-{gender}.json")
    history = load_json(path, {})
    for p in products:
        sizes = history.setdefault(p["handle"], {})
        for v in p["variants"]:
            pts = sizes.setdefault(v["size"], [])
            point = [today, v["p"], v["o"]]
            if pts and pts[-1][0] == today:
                pts[-1] = point                       # re-run on the same day
            elif not pts or pts[-1][1:] != point[1:]:
                pts.append(point)                     # only store changes
    save_json(path, history)
    for p in products:                                # lowest / highest price ever, per size
        for v in p["variants"]:
            prices = [pt[1] for pt in history[p["handle"]][v["size"]]]
            v["lo"], v["hi"] = min(prices), max(prices)
            prev = [pt for pt in history[p["handle"]][v["size"]] if pt[0] < today]
            v["prev"] = prev[-1][1] if prev else None
    return history


def load_watchlist():
    """Handles listed in docs/watchlist.txt (one per line, # for comments)."""
    if not os.path.exists(WATCH_FILE):
        return []
    with open(WATCH_FILE, encoding="utf-8") as f:
        return [l.split("#", 1)[0].strip() for l in f if l.split("#", 1)[0].strip()]


# ---------------------------------------------------------------------------
# Bottle colours (for the app's glow and accents)
# ---------------------------------------------------------------------------
COLORS_FILE = os.path.join(DATA_DIR, "colors.json")
MAX_NEW_COLORS = 1600     # new photos analysed per run (each one is read only once, then remembered)


def dominant_color(image_bytes):
    """Main colour of a bottle photo on a white background, e.g. '#354d69'.
    Picks the strongest hue band; black / grey / clear bottles return their neutral tone."""
    import colorsys
    from io import BytesIO
    from PIL import Image
    img = Image.open(BytesIO(image_bytes)).convert("RGBA").resize((48, 48))
    px = [(r, g, b) for r, g, b, a in img.getdata() if a >= 128 and not (r > 232 and g > 232 and b > 232)]
    if len(px) < 30:
        return None
    bands, neutral = {}, []
    for r, g, b in px:
        h, sat, val = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if sat > 0.22 and val > 0.18:
            band = bands.setdefault(int(h * 18) % 18, [0.0, []])
            band[0] += sat * val
            band[1].append((r, g, b))
        else:
            neutral.append((r, g, b))
    weight = lambda k: bands.get(k, [0])[0] + 0.5 * (bands.get((k + 1) % 18, [0])[0] + bands.get((k - 1) % 18, [0])[0])
    best = max(range(18), key=weight)
    pick = bands[best][1] if bands.get(best) and weight(best) > len(px) * 0.04 else neutral
    if not pick:
        return None
    return "#" + "".join(f"{round(sum(p[i] for p in pick) / len(pick)):02x}" for i in range(3))


def add_colors(products):
    cache = load_json(COLORS_FILE, {})
    new = 0
    for p in products:
        key = (p["image"] or "").split("?")[0]
        if key and key not in cache and new < MAX_NEW_COLORS:
            new += 1
            try:
                r = requests.get(key + "?width=64", headers=HEADERS, timeout=20)
                r.raise_for_status()
                cache[key] = dominant_color(r.content) or ""
            except Exception as e:                     # a missing photo never stops the run
                print(f"  ! colour of {key} failed: {e}")
                cache[key] = ""
            time.sleep(0.1)
        p["color"] = cache.get(key) or None
    save_json(COLORS_FILE, cache, pretty=True)
    if new:
        print(f"Bottle colours: {new} new photos analysed.")


# ---------------------------------------------------------------------------
# App data
# ---------------------------------------------------------------------------
def app_item(p):
    """Compact product record used by the app (short keys keep the files small)."""
    b = p["best"]
    return {
        "h": p["handle"], "g": p["gender"], "b": p["brand"], "n": p["name"], "k": p["kind"], "l": p["link"],
        "i": p["image"], "f": p["fame"], "r": p["rank"], "s": p["on_sale"], "st": p["in_stock"], "nw": p["new"],
        "d": p["discount"], "p": b["p"] if b else None, "o": b["o"] if b else None,
        "sz": b["size"] if b else "", "save": p["saving"], "pm": p["per100"], "nt": bool(p["notes"]),
        "lo": b["lo"] if b else None, "hi": b["hi"] if b else None, "c": p.get("color"), "fm": p.get("family", ""),
        "v": [[v["size"], v["ml"], v["p"], v["o"], v["d"], v["st"], v["lo"], v["hi"], v["sample"]] for v in p["variants"]],
    }


def export_app_data(products, gender, today):
    details = {p["handle"]: {"desc": p["desc"], "notes": p["notes"]} for p in products}
    save_json(os.path.join(DATA_DIR, f"{gender}.json"), {"updated": today, "gender": gender, "items": [app_item(p) for p in products]})
    save_json(os.path.join(DATA_DIR, f"details-{gender}.json"), details)


def export_home(by_gender, today):
    """docs/data/home.json: small file for the app's home screen (top deals of the day)."""
    top = []
    for g, n in (("men", 5), ("women", 5), ("unisex", 2)):
        famous = [p for p in by_gender.get(g, []) if p["on_sale"] and p["fame"]]
        seen = set()
        for p in sorted(famous, key=lambda p: (-p["discount"], -p["saving"])):
            if p["fame"] not in seen and len([t for t in top if t["gender"] == g]) < n:
                seen.add(p["fame"])                     # one version per famous perfume
                top.append(p)
    top.sort(key=lambda p: (-p["discount"], -p["saving"]))
    allp = [p for ps in by_gender.values() for p in ps]
    new = sorted([p for p in allp if p["new"] and is_popular(p)], key=popularity_key)[:10]
    counts = {g: sum(p["on_sale"] for p in ps) for g, ps in by_gender.items()}
    save_json(os.path.join(DATA_DIR, "home.json"), {
        "updated": today, "counts": counts, "best": max((p["discount"] for p in allp), default=0),
        "top": [app_item(p) for p in top], "new": [app_item(p) for p in new]})


SHARE_PAGE = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{desc}">
<meta property="og:type" content="product">
<meta property="og:site_name" content="Promos Parfums">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:image" content="{image}">
<meta property="og:url" content="{url}">
<meta name="twitter:card" content="summary">
<meta http-equiv="refresh" content="0; url={app}">
<link rel="canonical" href="{app}">
</head><body style="font-family:Arial,sans-serif;padding:24px">
<p><a href="{app}">{title}</a></p>
<script>location.replace({app_js});</script>
</body></html>
"""


def export_share_pages(by_gender):
    """docs/p/<gender>/<handle>.html: one tiny page per perfume, so a shared link shows
    a preview (photo, name, price) in WhatsApp/Instagram and then opens the app on it."""
    import shutil
    root = os.path.join(APP_DIR, "p")
    shutil.rmtree(root, ignore_errors=True)              # rebuilt every run (removed products disappear)
    base = APP_URL or "../../"
    for g, ps in by_gender.items():
        os.makedirs(os.path.join(root, g), exist_ok=True)
        for p in ps:
            b = p["best"]
            title = f"{p['brand']} {p['name']} - {p['kind']}".strip(" -")
            price = f"{b['p']:,.0f} DH".replace(",", " ")
            desc = (f"-{p['discount']}% : {price} au lieu de {b['o']:,.0f} DH ({b['size']})".replace(",", " ")
                    if p["on_sale"] else f"{price} ({b['size']})") + " · prix vérifié chaque jour"
            app = f"{base}#p={g}/{p['handle']}"
            url = f"{APP_URL}p/{g}/{p['handle']}.html" if APP_URL else ""
            with open(os.path.join(root, g, p["handle"] + ".html"), "w", encoding="utf-8") as f:
                f.write(SHARE_PAGE.format(title=escape(title), desc=escape(desc), image=escape(p["image"]),
                                          url=escape(url), app=escape(app), app_js=json.dumps(app)))


# ---------------------------------------------------------------------------
# Email + Excel
# ---------------------------------------------------------------------------
def build_excel(all_products):
    """Every size on sale, all collections, most famous first."""
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "Deals"
    ws.append(["Collection", "Fame rank", "Sales rank", "New", "Brand", "Perfume", "Type", "Size",
               "Discount %", "Price (DH)", "Was (DH)", "Saving (DH)", "Price / 100 ml", "Lowest ever", "Link"])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F2937")
    for p in all_products:
        for v in p["variants"]:
            if v["d"] > 0 and v["st"] and not v["sample"]:
                ws.append([LABELS[p["gender"]], p["fame"], p["rank"], "NEW" if v.get("new") else "", p["brand"],
                           p["name"], p["kind"], v["size"], v["d"], v["p"], v["o"], round(v["o"] - v["p"], 2),
                           round(v["p"] / v["ml"] * 100) if v["ml"] else None, v["lo"], p["link"]])
                ws.cell(ws.max_row, 15).hyperlink = p["link"]
    for col, w in zip("ABCDEFGHIJKLMNO", (11, 10, 10, 6, 18, 30, 16, 10, 10, 10, 10, 11, 13, 11, 50)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def card(p, note=""):
    b = p["best"]
    saving = f"Save {p['saving']:,.0f} DH" if p["saving"] else ""
    rank = f"#{p['fame']} famous" if p["fame"] else (f"#{p['rank']} best seller" if p["rank"] else "")
    badge = lambda txt, bg: (f'<span style="background:{bg};color:#fff;font-weight:bold;font-size:11px;'
                             f'padding:2px 7px;border-radius:10px;margin-right:4px;">{txt}</span>')
    tags = (badge("NEW", "#2563eb") if p["new"] else "") + (badge(f"-{p['discount']}%", "#e11d48") if p["on_sale"] else "")
    img = (f'<img src="{escape(p["image"])}" width="72" style="width:72px;height:auto;border-radius:6px;display:block;" alt="">'
           if p["image"] else "")
    old = f'<span style="color:#999;text-decoration:line-through;font-size:12px;padding-left:6px;">{b["o"]:,.0f} DH</span>' if b["o"] else ""
    note = f'<div style="font-size:12px;color:#16a34a;font-weight:bold;">{escape(note)}</div>' if note else ""
    return f"""
    <tr><td style="padding:10px 0;border-bottom:1px solid #eee;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
        <td width="80" valign="top"><a href="{escape(p['link'])}">{img}</a></td>
        <td valign="top" style="padding-left:10px;">
          {tags}<span style="color:#16a34a;font-size:12px;font-weight:bold;">{saving}</span>
          <div style="font-size:14px;font-weight:bold;color:#111;margin:4px 0 1px;">
            <a href="{escape(p['link'])}" style="color:#111;text-decoration:none;">{escape(p['brand'])} {escape(p['name'])}</a></div>
          <div style="font-size:11px;color:#888;">{escape(p['kind'])} · {escape(b['size'])} · {rank}</div>
          {note}
          <div style="font-size:14px;margin-top:2px;"><b style="color:#16a34a;">{b['p']:,.0f} DH</b>{old}</div>
        </td></tr></table>
    </td></tr>"""


def section(title, subtitle, rows):
    if not rows:
        return ""
    return f"""
  <tr><td style="padding:18px 20px 0;">
    <div style="font-size:16px;font-weight:bold;color:#111;">{title}</div>
    <div style="font-size:12px;color:#777;">{subtitle}</div></td></tr>
  <tr><td style="padding:0 20px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table></td></tr>"""


def tile(value, label, color, bg):
    return (f'<td align="center" style="background:{bg};border-radius:8px;padding:10px 4px;">'
            f'<div style="font-size:20px;font-weight:bold;color:{color};">{value}</div>'
            f'<div style="font-size:11px;color:#555;">{label}</div></td>')


def build_html(by_gender, watched, run_date):
    allp = [p for ps in by_gender.values() for p in ps]
    deals = [p for p in allp if p["on_sale"]]
    gap = '<td width="6"></td>'

    w_rows = ""
    for p in watched[:WATCH_CARDS]:
        b = p["best"]
        note = ""
        if b and b.get("prev") and b["p"] < b["prev"]:
            note = f"Price dropped {b['prev'] - b['p']:,.0f} DH since last run"
        elif b and b["p"] <= b["lo"] < b["hi"]:
            note = "Lowest price ever"
        w_rows += card(p, note)

    body = section("⭐ Your watchlist", "Starred perfumes on sale or with a price drop", w_rows)
    for g, n in (("men", CARDS["men"]), ("women", CARDS["women"]), ("unisex", CARDS["unisex"])):
        top, per_name = [], {}
        for p in by_gender.get(g, []):
            if p["on_sale"] and is_popular(p) and len(top) < n:
                key = p["fame"] or p["handle"]          # max 2 versions of the same famous perfume
                if per_name.get(key, 0) < 2:
                    per_name[key] = per_name.get(key, 0) + 1
                    top.append(p)
        body += section(f"{LABELS[g]} - famous perfumes on sale", "Most famous first, all in stock", "".join(card(p) for p in top))

    button = (f'<div style="margin-top:12px;"><a href="{escape(APP_URL)}" style="background:#111827;color:#fff;'
              f'padding:10px 16px;border-radius:8px;text-decoration:none;font-weight:bold;">Open the app</a></div>') if APP_URL else ""
    return f"""<html><body style="margin:0;background:#f4f4f4;font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:16px 8px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#fff;border-radius:10px;">
  <tr><td style="background:#111827;color:#fff;padding:18px 20px;border-radius:10px 10px 0 0;">
    <div style="font-size:20px;font-weight:bold;">Kosmenia perfume deals</div>
    <div style="font-size:13px;color:#cbd5e1;margin-top:4px;">Report of {run_date} - men, women and unisex</div></td></tr>
  <tr><td style="padding:16px 20px 4px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      {tile(len(deals), "on sale", "#111", "#f1f5f9")}{gap}
      {tile(sum(p["new"] for p in deals), "new deals", "#2563eb", "#eff6ff")}{gap}
      {tile(len(watched), "watchlist alerts", "#b45309", "#fffbeb")}{gap}
      {tile(f"-{max((p['discount'] for p in deals), default=0)}%", "best discount", "#e11d48", "#fff1f2")}
    </tr></table></td></tr>
  {body}
  <tr><td style="padding:16px 20px;font-size:12px;color:#777;">
    Every size on sale, for all collections, is in the attached Excel file.{button}</td></tr>
</table></td></tr></table></body></html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    global APP_PASSWORD, FROM_EMAIL, TO_EMAIL
    FROM_EMAIL = get_gmail_user()
    TO_EMAIL = os.environ.get("ALERT_TO", "").strip() or FROM_EMAIL
    APP_PASSWORD = get_app_password()  # ask up front, not after minutes of reading

    today = date.today().isoformat()
    by_gender = {}
    for gender, collection, fame_file in COLLECTIONS:
        print(f"Reading {collection}...")
        raw = fetch_products(collection)
        if not raw:
            raise SystemExit(f"Could not read {collection} - stopping so no data is overwritten.")
        ranks = fetch_sales_ranks(collection)
        famous = load_famous(os.path.join(HERE, fame_file))
        products = []
        for r in raw:
            p = summarise(build_product(r, gender, famous, ranks))
            if p["best"]:
                products.append(p)
        products.sort(key=popularity_key)
        by_gender[gender] = products
        print(f"  {len(products)} products, {sum(p['on_sale'] for p in products)} on sale, "
              f"{sum(bool(p['notes']) for p in products)} with notes.")
    # a perfume in the unisex collection belongs to 'unisex' only
    unisex = {p["handle"] for p in by_gender.get("unisex", [])}
    for g in ("men", "women"):
        by_gender[g] = [p for p in by_gender.get(g, []) if p["handle"] not in unisex]

    allp = [p for ps in by_gender.values() for p in ps]
    mark_new(allp)
    add_colors(allp)
    for g, ps in by_gender.items():
        update_history(ps, g, today)
        export_app_data(ps, g, today)
    export_home(by_gender, today)
    export_share_pages(by_gender)
    print(f"App data saved: {len(allp)} products (+ home screen and share pages).")

    wanted = set(load_watchlist())
    watched = [p for p in allp if p["handle"] in wanted and p["best"] and
               (p["on_sale"] or (p["best"].get("prev") and p["best"]["p"] < p["best"]["prev"]))]

    deals = [p for p in allp if p["on_sale"]]
    n_new = sum(p["new"] for p in deals)
    print(f"Total: {len(deals)} deals, {n_new} new, {len(watched)} watchlist alerts.")
    run_date = date.today().strftime("%d/%m/%Y")
    parts = [f"{len(deals)} perfume deals"] + ([f"{n_new} new"] if n_new else []) + \
            ([f"{len(watched)} on your watchlist"] if watched else [])
    send_email(f"Kosmenia: {', '.join(parts)} - {run_date}", build_html(by_gender, watched, run_date),
               TO_EMAIL, build_excel(sorted(allp, key=popularity_key)), f"kosmenia_deals_{today}.xlsx")


if __name__ == "__main__":
    import sys
    import traceback
    exit_code = 0
    try:
        main()
    except SystemExit as e:
        print(f"\nSTOPPED: {e}")
        exit_code = 1
    except Exception:
        print("\nERROR - the script crashed:\n")
        traceback.print_exc()
        exit_code = 1
    # Keep the window open when started by double-click (not on GitHub)
    if sys.stdin and sys.stdin.isatty() and not os.environ.get("GITHUB_ACTIONS"):
        input("\nPress Enter to close this window...")
    sys.exit(exit_code)  # makes a failed run show as failed on GitHub
