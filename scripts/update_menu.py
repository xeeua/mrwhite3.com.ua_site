#!/usr/bin/env python3
"""
Тягне меню з stravopys.com/mrwhite-3 (сторінки там серверно відрендерені,
хоч публічного API й немає) і перегенеровує menu.html + data/menu.json.

Запуск вручну:      python3 scripts/update_menu.py
Автоматично:        .github/workflows/update-menu.yml (раз на добу)

Скрипт нічого не перезаписує, якщо вміст меню не змінився відносно
попереднього запуску (порівнюються самі страви, без службової дати) —
це важливо для автоматизації: git тоді не бачить diff і зайвого коміту
за день без змін просто не буде.

Без сторонніх залежностей (тільки стандартна бібліотека), щоб можна
було запустити де завгодно без pip install.
"""

import html as htmlmod
import json
import re
import ssl
import sys
import urllib.request
from datetime import date
from pathlib import Path

try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CONTEXT = None  # покладаємось на системні сертифікати за замовчуванням

BASE_URL = "https://stravopys.com/mrwhite-3"
ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "menu.json"
MENU_HTML_FILE = ROOT / "menu.html"
PROMO_HTML_FILE = ROOT / "promo.html"
SITEMAP_FILE = ROOT / "sitemap.xml"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "uk,en;q=0.8",
}

EXCLUDED_SLUGS = {"promo"}  # окрема сторінка акцій, не частина меню страв

SOCIAL_ICONS_HTML = '''
      <a href="https://www.instagram.com/mr.white.3.0/" target="_blank" rel="noopener" class="icon-link" aria-label="Instagram">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.2" cy="6.8" r="1.1" fill="currentColor" stroke="none"/></svg>
      </a>
      <a href="https://www.tiktok.com/@mr.white_3.0" target="_blank" rel="noopener" class="icon-link" aria-label="TikTok">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M16.6 2h-3.2v13.6a2.8 2.8 0 1 1-2.4-2.77v-3.24a6 6 0 1 0 5.6 5.98V8.8a7 7 0 0 0 4.4 1.55V7.15A3.9 3.9 0 0 1 16.6 2z"/></svg>
      </a>'''


def render_header() -> str:
    return f'''<header class="site-header" id="top">
  <div class="container header-inner">
    <a href="index.html" class="logo" aria-label="Mr.White 3">
      <span class="logo-neon">White</span>
    </a>
    <nav class="main-nav" id="mainNav">
      <a href="index.html#about">Про нас</a>
      <a href="menu.html">Меню</a>
      <a href="promo.html">Акції</a>
      <a href="index.html#contacts">Контакти</a>
    </nav>
    <div class="header-actions">
      <a href="tel:+380952345566" class="icon-link" aria-label="Подзвонити">☎</a>{SOCIAL_ICONS_HTML}
      <a href="https://t.me/MrWhite3_bot" target="_blank" rel="noopener" class="btn btn-primary">Резерв столику</a>
    </div>
    <button class="burger" id="burger" aria-label="Меню" aria-expanded="false">
      <span></span><span></span><span></span>
    </button>
  </div>
</header>'''


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20, context=_SSL_CONTEXT) as resp:
        return resp.read().decode("utf-8", errors="replace")


def clean_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw)
    text = htmlmod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def extract_categories(homepage_html: str):
    pattern = re.compile(
        r'<a href="/mrwhite-3/([a-z0-9_-]+)">.*?'
        r'<span class="category-title">([^<]*)</span>',
        re.DOTALL,
    )
    seen = set()
    categories = []
    for slug, title in pattern.findall(homepage_html):
        if slug in seen or slug in EXCLUDED_SLUGS:
            continue
        seen.add(slug)
        categories.append((slug, clean_text(title)))
    return categories


def extract_items(category_html: str, fallback_title: str):
    h1_match = re.search(r"<h1>([^<]*)</h1>", category_html)
    category_title = clean_text(h1_match.group(1)) if h1_match else fallback_title

    subcat_pattern = re.compile(r'<h2 data-target="#body-[a-f0-9-]+">([^<]*)</h2>')
    subcats = [(m.start(), clean_text(m.group(1))) for m in subcat_pattern.finditer(category_html)]

    item_pattern = re.compile(
        r'<div class="card menu-item[^"]*"\s+data-id="[0-9a-fA-F]+"\s+data-price="([\d.]+)">'
    )
    matches = list(item_pattern.finditer(category_html))

    items = []
    for i, m in enumerate(matches):
        chunk_start = m.end()
        chunk_end = matches[i + 1].start() if i + 1 < len(matches) else len(category_html)
        chunk = category_html[chunk_start:chunk_end]

        name_match = re.search(r'<h3 class="card-title">(.*?)</h3>', chunk, re.DOTALL)
        if name_match:
            name = clean_text(name_match.group(1))
        else:
            alt_match = re.search(r'alt="([^"]*)"', chunk)
            name = clean_text(alt_match.group(1)) if alt_match else ""

        if not name:
            continue  # без назви позиція нам не потрібна (розхідник/декор)

        desc_match = re.search(r'<p class="card-text description">(.*?)</p>', chunk, re.DOTALL)
        description = clean_text(desc_match.group(1)) if desc_match else ""

        price = float(m.group(1))

        subcat_name = ""
        for pos, title in subcats:
            if pos <= m.start():
                subcat_name = title
            else:
                break

        items.append({
            "subcategory": subcat_name,
            "name": name,
            "description": description,
            "price": price,
        })

    return category_title, items


def extract_promo(html: str):
    item_pattern = re.compile(
        r'<div class="card menu-item[^"]*"\s+data-id="[0-9a-fA-F]+"\s+data-price="[\d.]+">'
    )
    matches = list(item_pattern.finditer(html))

    items = []
    for i, m in enumerate(matches):
        chunk_start = m.end()
        chunk_end = matches[i + 1].start() if i + 1 < len(matches) else len(html)
        chunk = html[chunk_start:chunk_end]

        name_match = re.search(r'<h3 class="card-title">(.*?)</h3>', chunk, re.DOTALL)
        name = clean_text(name_match.group(1)) if name_match else ""
        if not name:
            continue

        desc_match = re.search(r'<p class="card-text description">(.*?)</p>', chunk, re.DOTALL)
        description = clean_text(desc_match.group(1)) if desc_match else ""

        img_match = re.search(r'class="square-image">\s*<picture>.*?<img[^>]+src="([^"]+)"', chunk, re.DOTALL)
        image = f"https://stravopys.com{img_match.group(1)}" if img_match else ""

        items.append({"name": name, "description": description, "image": image})

    return items


def scrape_promo():
    html = fetch(f"{BASE_URL}/promo")
    return extract_promo(html)


def scrape_menu():
    homepage = fetch(BASE_URL)
    categories = extract_categories(homepage)
    if not categories:
        raise RuntimeError("Не знайшов жодної категорії на головній сторінці Stravopys — "
                            "можливо, змінилась розмітка сайту.")

    result = []
    for slug, fallback_title in categories:
        page_html = fetch(f"{BASE_URL}/{slug}")
        title, items = extract_items(page_html, fallback_title)
        if items:
            result.append({"slug": slug, "title": title, "items": items})

    if not result:
        raise RuntimeError("Категорії знайдено, але жодної страви не витягнуто — "
                            "можливо, змінилась розмітка сайту.")

    return result


def format_price(price: float) -> str:
    if price == int(price):
        return f"{int(price)} грн"
    return f"{price:.2f} грн"


def render_menu_html(categories, updated_at: str) -> str:
    nav_items = "".join(
        f'<li><a href="#cat-{c["slug"]}">{htmlmod.escape(c["title"])}</a></li>'
        for c in categories
    )

    sections = []
    menu_sections_ld = []

    for cat in categories:
        rows = []
        current_subcat = object()  # гарантовано не дорівнює жодному рядку на старті
        ld_items = []

        for item in cat["items"]:
            if item["subcategory"] != current_subcat:
                current_subcat = item["subcategory"]
                if current_subcat:
                    rows.append(f'<h3 class="menu-subcat">{htmlmod.escape(current_subcat)}</h3>')

            desc_html = (
                f'<p class="menu-item-desc">{htmlmod.escape(item["description"])}</p>'
                if item["description"] else ""
            )
            rows.append(f'''
            <div class="menu-item-row">
              <div class="menu-item-main">
                <span class="menu-item-name">{htmlmod.escape(item["name"])}</span>
                {desc_html}
              </div>
              <span class="menu-item-price">{format_price(item["price"])}</span>
            </div>''')

            ld_items.append({
                "@type": "MenuItem",
                "name": item["name"],
                "description": item["description"],
                "offers": {
                    "@type": "Offer",
                    "price": item["price"],
                    "priceCurrency": "UAH",
                },
            })

        sections.append(f'''
    <section class="menu-category" id="cat-{cat["slug"]}">
      <h2>{htmlmod.escape(cat["title"])}</h2>
      {"".join(rows)}
    </section>''')

        menu_sections_ld.append({
            "@type": "MenuSection",
            "name": cat["title"],
            "hasMenuItem": ld_items,
        })

    menu_ld = {
        "@context": "https://schema.org",
        "@type": "Menu",
        "name": "Меню Mr.White 3",
        "inLanguage": "uk",
        "hasMenuSection": menu_sections_ld,
    }

    return f'''<!doctype html>
<html lang="uk">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Меню — Кальян-бар Mr.White 3 | Київ</title>
<meta name="description" content="Повне меню кальян-бару Mr.White 3 у Києві: кальяни, коктейлі, кухня, напої та ціни. Оновлено {updated_at}.">
<link rel="canonical" href="https://mrwhite3.com.ua/menu.html">

<meta property="og:type" content="website">
<meta property="og:url" content="https://mrwhite3.com.ua/menu.html">
<meta property="og:title" content="Меню — Кальян-бар Mr.White 3">
<meta property="og:description" content="Кальяни, коктейлі, кухня та напої в Mr.White 3. Оновлено {updated_at}.">
<meta property="og:image" content="https://mrwhite3.com.ua/assets/img/lounge.webp">
<meta property="og:locale" content="uk_UA">

<link rel="icon" href="favicon.ico" sizes="32x32">
<link rel="icon" href="assets/img/favicon-32.png" type="image/png" sizes="32x32">
<link rel="icon" href="assets/img/favicon-16.png" type="image/png" sizes="16x16">
<link rel="apple-touch-icon" href="assets/img/apple-touch-icon.png">
<link rel="stylesheet" href="assets/css/style.css">
<script type="application/ld+json">{json.dumps(menu_ld, ensure_ascii=False)}</script>
</head>
<body>

{render_header()}

<main class="section menu-page">
  <div class="container">
    <h1>Меню Mr.White 3</h1>
    <p class="menu-updated">Оновлено {updated_at}. Актуальне замовлення онлайн — у нашому
      <a href="https://stravopys.com/mrwhite-3" target="_blank" rel="noopener">QR-меню на Stravopys</a>.</p>

    <nav class="menu-jump"><ul>{nav_items}</ul></nav>

    {"".join(sections)}
  </div>
</main>

<footer class="site-footer">
  <div class="container footer-inner">
    <p>&copy; <span id="year"></span> Mr.White 3. Кальян-бар у Києві.</p>
    <p class="footer-note">Заклад для гостей 18+. Курити кальян шкідливо для здоров'я.</p>
  </div>
</footer>

<script src="assets/js/main.js"></script>
</body>
</html>
'''


def render_promo_html(items, updated_at: str) -> str:
    cards = []
    ld_items = []

    for item in items:
        img_html = (
            f'<img src="{htmlmod.escape(item["image"])}" alt="{htmlmod.escape(item["name"])}" loading="lazy">'
            if item["image"] else ""
        )
        cards.append(f'''
    <div class="promo-card">
      {img_html}
      <div class="promo-card-body">
        <h2>{htmlmod.escape(item["name"])}</h2>
        <p>{htmlmod.escape(item["description"])}</p>
      </div>
    </div>''')

        ld_items.append({
            "@type": "Offer",
            "name": item["name"],
            "description": item["description"],
        })

    promo_ld = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": "Акції Mr.White 3",
        "itemListElement": ld_items,
    }

    return f'''<!doctype html>
<html lang="uk">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Акції — Кальян-бар Mr.White 3 | Київ</title>
<meta name="description" content="Актуальні акції кальян-бару Mr.White 3 у Києві. Оновлено {updated_at}.">
<link rel="canonical" href="https://mrwhite3.com.ua/promo.html">

<meta property="og:type" content="website">
<meta property="og:url" content="https://mrwhite3.com.ua/promo.html">
<meta property="og:title" content="Акції — Кальян-бар Mr.White 3">
<meta property="og:description" content="Актуальні акції Mr.White 3. Оновлено {updated_at}.">
<meta property="og:image" content="https://mrwhite3.com.ua/assets/img/lounge.webp">
<meta property="og:locale" content="uk_UA">

<link rel="icon" href="favicon.ico" sizes="32x32">
<link rel="icon" href="assets/img/favicon-32.png" type="image/png" sizes="32x32">
<link rel="icon" href="assets/img/favicon-16.png" type="image/png" sizes="16x16">
<link rel="apple-touch-icon" href="assets/img/apple-touch-icon.png">
<link rel="stylesheet" href="assets/css/style.css">
<script type="application/ld+json">{json.dumps(promo_ld, ensure_ascii=False)}</script>
</head>
<body>

{render_header()}

<main class="section promo-page">
  <div class="container">
    <h1>Акції Mr.White 3</h1>
    <p class="menu-updated">Оновлено {updated_at}. Актуальні акції — також у нашому
      <a href="https://stravopys.com/mrwhite-3/promo" target="_blank" rel="noopener">QR-меню на Stravopys</a>.</p>

    <div class="promo-grid">{"".join(cards)}</div>
  </div>
</main>

<footer class="site-footer">
  <div class="container footer-inner">
    <p>&copy; <span id="year"></span> Mr.White 3. Кальян-бар у Києві.</p>
    <p class="footer-note">Заклад для гостей 18+. Курити кальян шкідливо для здоров'я.</p>
  </div>
</footer>

<script src="assets/js/main.js"></script>
</body>
</html>
'''


def render_sitemap(updated_at: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://mrwhite3.com.ua/</loc>
    <changefreq>monthly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://mrwhite3.com.ua/menu.html</loc>
    <lastmod>{updated_at}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>https://mrwhite3.com.ua/promo.html</loc>
    <lastmod>{updated_at}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.6</priority>
  </url>
</urlset>
'''


def load_previous():
    if not DATA_FILE.exists():
        return {}
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def main():
    force = "--force" in sys.argv  # перегенерувати сторінки навіть без змін у даних (напр. після правки шаблону)

    print(f"Тягну меню й акції з {BASE_URL} ...")
    try:
        categories = scrape_menu()
        promo_items = scrape_promo()
    except Exception as exc:  # noqa: BLE001 - навмисно широкий except для звіту в консоль
        print(f"ПОМИЛКА: не вдалося оновити дані: {exc}", file=sys.stderr)
        sys.exit(1)

    previous = load_previous()
    categories_changed = previous.get("categories") != categories
    promo_changed = previous.get("promo") != promo_items

    if not categories_changed and not promo_changed and not force:
        print("Змін немає — файли не чіпаю.")
        sys.exit(0)

    updated_at = date.today().isoformat()

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(
            {"updated_at": updated_at, "categories": categories, "promo": promo_items},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )

    if categories_changed or force:
        MENU_HTML_FILE.write_text(render_menu_html(categories, updated_at), encoding="utf-8")
    if promo_changed or force:
        PROMO_HTML_FILE.write_text(render_promo_html(promo_items, updated_at), encoding="utf-8")

    SITEMAP_FILE.write_text(render_sitemap(updated_at), encoding="utf-8")

    total_items = sum(len(c["items"]) for c in categories)
    print(f"Оновлено. Меню: {len(categories)} категорій, {total_items} позицій. "
          f"Акції: {len(promo_items)}.")
    sys.exit(0)


if __name__ == "__main__":
    main()
