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

# Scraped in Kosmenia's "best-selling" order, so the position = real sales rank
BASE_URL = "https://kosmenia.ma/collections/parfum-homme?sort_by=best-selling"
MAX_PAGES = 100           # safety cap; the loop stops at the first empty page
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PriceGatherer/1.0"}

# ---- "Sold in real life" filter -------------------------------------------
FAMOUS_FILE = os.path.join(HERE, "famous_perfumes.txt")   # fame ranking (editable)
TOP_SELLERS = 100         # also keep anything in Kosmenia's top-100 best sellers
MAX_CARDS = 60            # keeps the email under Gmail's ~102 KB clipping limit
SEEN_FILE = os.path.join(HERE, "seen_promos.json")        # memory for NEW badges
APP_URL = os.environ.get("APP_URL", "")   # link to the phone app, shown in the email

# No personal info in this file: the Gmail address comes from the GMAIL_USER
# secret on GitHub, or from "gmail_user.txt" next to the script when run locally.
USER_FILE = os.path.join(HERE, "gmail_user.txt")
FROM_EMAIL = None
TO_EMAIL = None
APP_DIR = os.path.join(HERE, "docs")          # the phone app (GitHub Pages)
DATA_DIR = os.path.join(APP_DIR, "data")


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


def fetch_page(url):
    """Return the parsed page, or None if the request fails."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"  ! Could not load {url}: {e}")
        return None
    return BeautifulSoup(response.content, "html.parser")



def load_famous():
    """List of (rank, [alternatives -> [words]]) from famous_perfumes.txt."""
    famous = []
    if not os.path.exists(FAMOUS_FILE):
        print("famous_perfumes.txt not found - ranking by Kosmenia sales only.")
        return famous
    with open(FAMOUS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip().lower()
            if line:
                alts = [alt.split() for alt in line.split("|") if alt.strip()]
                famous.append((len(famous) + 1, alts))
    return famous


def normalize(text):
    """Lowercase words, with l'homme / d'hermes style names glued together."""
    text = text.lower().replace("'", "").replace("’", "")
    text = re.sub(r"\b([ld])[\s-]+(homme|hermes)\b", r"\1\2", text)
    return set(re.split(r"[^a-z0-9]+", text)) - {""}


def fame_rank(title, link, famous):
    words = normalize(title) | normalize(link.rsplit("/", 1)[-1])
    for rank, alts in famous:
        if any(all(w in words for w in alt) for alt in alts):
            return rank
    return None


def parse_products(soup, offset):
    """Every product on one page (on sale or not), with its sales rank."""
    products = []
    for a in soup.select("a.product-grid-item"):
        cell = a.find_parent("div", class_="grid-item")   # the product's own card
        if cell is None or not a.get("href"):
            continue
        try:
            href = a["href"]
            classes = cell.get("class", [])
            p_tag = cell.find("p")
            strong = [t.strip() for t in p_tag.find("strong").stripped_strings] if p_tag.find("strong") else []
            title = " ".join(p_tag.stripped_strings).strip()
            kind = title.split(" ".join(strong), 1)[-1].strip() if strong else ""
            badge_tag = cell.find("span", class_="badge__text")
            badge = badge_tag.text.strip() if badge_tag else ""
            image_tag = cell.find("img", class_="lazyload")
            image = ("https:" + image_tag["data-src"].replace("{width}", "200")
                     if image_tag and image_tag.get("data-src") else "")
            prices = [s.text.strip() for s in cell.find_all("span", class_="visually-hidden") if "DH" in s.text]
            on_sale = "on-sale" in classes and bool(badge)
            products.append({
                "handle": href.rstrip("/").rsplit("/", 1)[-1],
                "title": title,
                "brand": strong[0].title() if strong else "",
                "name": " ".join(strong[1:]).title() if len(strong) > 1 else title,
                "kind": kind,
                "link": "https://kosmenia.ma" + href,
                "image": image,
                "price": prices[0] if prices else "",
                "old_price": prices[1] if on_sale and len(prices) > 1 else "",
                "badge": badge if on_sale else "",
                "discount": int(badge.replace("-", "").replace("%", "").strip()) if on_sale else 0,
                "on_sale": on_sale,
                "in_stock": "sold-out" not in classes,
                "sales_rank": offset + len(products) + 1,
            })
        except (AttributeError, TypeError, KeyError, ValueError):
            continue  # skip a malformed product card instead of crashing
    return products


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


def to_number(price_text):
    """'1,067.00 DH' -> 1067.0 (None if not a price)."""
    try:
        return float(price_text.replace("DH", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def popularity_key(p):
    """Famous perfumes first (in fame order), then by Kosmenia sales rank."""
    return (p["fame"] or 10_000, p["sales_rank"] or 10_000)


def is_real_life(p):
    return p["fame"] is not None or (p["sales_rank"] or 10_000) <= TOP_SELLERS


def mark_new(promos):
    """Flag promos that are new (or got a bigger discount) since the last run."""
    first_run = not os.path.exists(SEEN_FILE)
    seen = {}
    if not first_run:
        with open(SEEN_FILE, encoding="utf-8") as f:
            seen = json.load(f)
    for p in promos:
        old = seen.get(p["handle"])
        p["new"] = (not first_run) and (old is None or p["discount"] > old)
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump({p["handle"]: p["discount"] for p in promos}, f, indent=0, sort_keys=True)
    if first_run:
        print("First run: no NEW badges yet (nothing to compare with).")


def build_excel(promos):
    """Full list as an Excel file (bytes), most famous first."""
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "Promotions"
    ws.append(["Fame rank", "Kosmenia sales rank", "New", "Discount %", "Product",
               "Price (DH)", "Original (DH)", "Saving (DH)", "Link"])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F2937")
    for p in promos:
        n, o = to_number(p["price"]), to_number(p["old_price"])
        ws.append([p["fame"], p["sales_rank"], "NEW" if p["new"] else "", p["discount"], p["title"],
                   n, o, round(o - n, 2) if n and o else None, p["link"]])
        ws.cell(ws.max_row, 9).hyperlink = p["link"]
    for col, w in zip("ABCDEFGHI", (11, 19, 7, 11, 48, 11, 13, 12, 60)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def card(p):
    n, o = to_number(p["price"]), to_number(p["old_price"])
    saving = f"Save {o - n:,.0f} DH" if n and o else ""
    rank = f"#{p['fame']} famous" if p["fame"] else f"#{p['sales_rank']} best seller"
    new = ('<span style="background:#2563eb;color:#fff;font-weight:bold;font-size:11px;'
           'padding:2px 6px;border-radius:10px;margin-right:4px;">NEW</span>') if p["new"] else ""
    img = (f'<img src="{escape(p["image"])}" width="80" style="width:80px;height:auto;'
           f'border-radius:6px;display:block;" alt="">') if p["image"] else ""
    return f"""
    <tr><td style="padding:12px 0;border-bottom:1px solid #eee;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
        <td width="88" valign="top"><a href="{escape(p['link'])}">{img}</a></td>
        <td valign="top" style="padding-left:12px;">
          {new}<span style="background:#e11d48;color:#fff;font-weight:bold;font-size:13px;padding:2px 8px;border-radius:10px;">{escape(p['badge'])}</span>
          <span style="color:#16a34a;font-size:12px;font-weight:bold;padding-left:4px;">{saving}</span>
          <div style="font-size:15px;font-weight:bold;color:#111;margin:6px 0 2px;">
            <a href="{escape(p['link'])}" style="color:#111;text-decoration:none;">{escape(p['title'])}</a></div>
          <div style="font-size:11px;color:#888;margin-bottom:4px;">{rank}</div>
          <div style="font-size:15px;"><b style="color:#16a34a;">{escape(p['price'])}</b>
            <span style="color:#999;text-decoration:line-through;font-size:13px;padding-left:6px;">{escape(p['old_price'])}</span></div>
        </td></tr></table>
    </td></tr>"""


def tile(value, label, color, bg):
    return (f'<td align="center" style="background:{bg};border-radius:8px;padding:10px 4px;">'
            f'<div style="font-size:22px;font-weight:bold;color:{color};">{value}</div>'
            f'<div style="font-size:11px;color:#555;">{label}</div></td>')


def build_html(promos, run_date):
    real = [p for p in promos if is_real_life(p)]
    shown = real[:MAX_CARDS]
    n_new = sum(p["new"] for p in real)
    cards = "".join(card(p) for p in shown) or \
        '<tr><td style="padding:12px 0;color:#777;">No famous perfume on sale today.</td></tr>'
    more = (f'<div style="font-size:12px;color:#777;margin-top:8px;">+ {len(real) - len(shown)} more '
            f'popular perfumes in the Excel file.</div>') if len(real) > len(shown) else ""
    gap = '<td width="6"></td>'
    return f"""<html><body style="margin:0;background:#f4f4f4;font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:16px 8px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#fff;border-radius:10px;">
  <tr><td style="background:#111827;color:#fff;padding:18px 20px;border-radius:10px 10px 0 0;">
    <div style="font-size:20px;font-weight:bold;">Kosmenia - Parfum Homme</div>
    <div style="font-size:13px;color:#cbd5e1;margin-top:4px;">Promotion report of {run_date}</div>
  </td></tr>
  <tr><td style="padding:16px 20px 4px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      {tile(len(real), "popular on sale", "#111", "#f1f5f9")}{gap}
      {tile(n_new, "new since last run", "#2563eb", "#eff6ff")}{gap}
      {tile(f"-{max((p['discount'] for p in real), default=0)}%", "best discount", "#e11d48", "#fff1f2")}
    </tr></table>
  </td></tr>
  <tr><td style="padding:16px 20px 0;">
    <div style="font-size:16px;font-weight:bold;color:#111;">Famous perfumes on sale</div>
    <div style="font-size:12px;color:#777;">Most famous first - all in stock at Kosmenia</div>
  </td></tr>
  <tr><td style="padding:0 20px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{cards}</table>{more}</td></tr>
  <tr><td style="padding:16px 20px;font-size:12px;color:#777;">
    {len(promos)} products are on sale in total. The full list, with fame and sales ranks, is attached as an Excel file.
    {f'<div style="margin-top:12px;"><a href="{escape(APP_URL)}" style="background:#111827;color:#fff;padding:10px 16px;border-radius:8px;text-decoration:none;font-weight:bold;">Open the app</a></div>' if APP_URL else ''}
  </td></tr>
</table></td></tr></table></body></html>"""


def update_history(products, today_iso):
    """docs/data/history.json: for each product, the dates its price changed."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "history.json")
    history = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            history = json.load(f)
    for p in products:
        price = to_number(p["price"])
        if price is None:
            continue
        points = history.setdefault(p["handle"], [])
        point = [today_iso, price, to_number(p["old_price"])]
        if points and points[-1][0] == today_iso:
            points[-1] = point                      # re-run on the same day
        elif not points or points[-1][1:] != point[1:]:
            points.append(point)                    # only store changes
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, separators=(",", ":"), sort_keys=True)
    return history


def export_app_data(products, history, today_iso):
    """docs/data/latest.json: today's catalogue for the phone app."""
    items = []
    for p in products:
        pts = [pt for pt in history.get(p["handle"], []) if pt[1]]
        low = min(pts, key=lambda pt: pt[1]) if pts else None
        high = max(pt[1] for pt in pts) if pts else None
        regular = to_number(p["old_price"]) or to_number(p["price"])
        items.append({
            "h": p["handle"], "t": p["title"], "b": p["brand"], "n": p["name"], "k": p["kind"],
            "l": p["link"], "i": p["image"], "p": to_number(p["price"]), "o": to_number(p["old_price"]),
            "d": p["discount"], "s": p["on_sale"], "st": p["in_stock"], "f": p.get("fame"),
            "r": p["sales_rank"], "nw": bool(p.get("new")), "lo": low[1] if low else None,
            "lod": low[0] if low else None, "hi": high, "reg": regular,
        })
    with open(os.path.join(DATA_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": today_iso, "items": items}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"App data saved: {len(items)} products.")


def main():
    global APP_PASSWORD, FROM_EMAIL, TO_EMAIL
    FROM_EMAIL = get_gmail_user()
    TO_EMAIL = os.environ.get("ALERT_TO", "").strip() or FROM_EMAIL
    APP_PASSWORD = get_app_password()  # ask up front, not after a minute of scraping
    famous = load_famous()

    products, offset = {}, 0
    for page in range(1, MAX_PAGES + 1):
        soup = fetch_page(f"{BASE_URL}&page={page}")
        if soup is None:
            continue
        found = parse_products(soup, offset)
        if not found:
            print(f"Page {page} is empty - reached the end of the collection.")
            break
        offset += len(found)
        for p in found:
            products.setdefault(p["handle"], p)  # dedupe, keep the best (first) rank
        n_sale = sum(p["on_sale"] and p["in_stock"] for p in found)
        print(f"Processed page {page}. Found {n_sale} promotions.")
        time.sleep(1)  # be polite to the site

    products = list(products.values())
    for p in products:
        p["fame"] = fame_rank(p["title"], p["link"], famous)
    products.sort(key=popularity_key)
    promos = [p for p in products if p["on_sale"] and p["in_stock"]]
    mark_new(promos)

    today_iso = date.today().isoformat()
    history = update_history(products, today_iso)
    export_app_data(products, history, today_iso)

    real = [p for p in promos if is_real_life(p)]
    n_new = sum(p["new"] for p in real)
    print(f"Total: {len(promos)} promotions, {len(real)} popular, {n_new} new.")

    today = date.today().strftime("%d/%m/%Y")
    if promos:
        new_txt = f", {n_new} new" if n_new else ""
        subject = f"Kosmenia: {len(real)} famous perfumes on sale{new_txt} - {today}"
        send_email(subject, build_html(promos, today), TO_EMAIL,
                   build_excel(promos), f"kosmenia_promos_{date.today():%Y-%m-%d}.xlsx")
    else:
        send_email(f"Kosmenia: no promotions - {today}", "<p>No promotions found.</p>", TO_EMAIL)


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
