"""Persistent, human-reviewed Brickmo catalog index sourced from public sitemaps.

No claim of complete coverage: each refresh is bounded and diagnostic information is returned.
"""
from __future__ import annotations
import gzip
import re
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin, parse_qs, unquote
import xml.etree.ElementTree as ET
import requests
import psycopg
from psycopg.rows import dict_row

BASE = 'https://www.brickmo.com'
SCHEMA = '''CREATE TABLE IF NOT EXISTS public.brickmo_catalog (
    product_url text PRIMARY KEY,
    manufacturer_sku text,
    product_title text,
    source_sitemap text,
    indexed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS brickmo_catalog_sku_idx ON public.brickmo_catalog(manufacturer_sku);'''
UA = {'User-Agent': 'LBrixxPriceCrawler/1.0 (+product catalog indexing; human review)', 'Accept': 'application/xml,text/xml,*/*'}
SKU_RE = re.compile(r'(?<![a-z0-9])(FO?\d{4,})(?![a-z0-9])', re.I)


def normalized_sku(s):
    s = str(s or '').upper().strip()
    m = re.fullmatch(r'FO?(\d{4,})', s)
    if m:
        return 'FO' + m.group(1)
    # Lumibricks short form L9081 in Odoo descriptions = LB9081 on Brickmo.
    m = re.fullmatch(r'L(B)?(\d{4,6})', s)
    return 'LB' + m.group(2) if m else s


def sku_from_url(url):
    p = urlparse(url)
    for value in parse_qs(p.query).get('number', []):
        m = SKU_RE.search(value)
        if m:
            return normalized_sku(m.group(1))
    m = SKU_RE.search(p.path.replace('-', ' '))
    return normalized_sku(m.group(1)) if m else ''


def valid_product_url(url):
    p = urlparse(url)
    if p.scheme not in ('http', 'https') or p.hostname not in ('www.brickmo.com', 'brickmo.com'):
        return False
    # Brickmo's Shopware detail URLs contain a numeric item ID and a product slug.
    return bool(re.search(r'/\d{3,}/[^/]+/?$', p.path)) and bool(sku_from_url(url))


def canonical(url):
    p = urlparse(url)
    return 'https://www.brickmo.com' + p.path.rstrip('/')


def discover(session, timeout=12):
    sources = [BASE + '/sitemap.xml', BASE + '/sitemap_index.xml', BASE + '/sitemap.xml.gz']
    notes = []
    try:
        r = session.get(BASE + '/robots.txt', timeout=timeout)
        notes.append('robots.txt HTTP ' + str(r.status_code))
        if r.ok:
            for line in r.text.splitlines():
                if line.lower().startswith('sitemap:'):
                    u = line.split(':', 1)[1].strip()
                    if urlparse(u).hostname in ('www.brickmo.com', 'brickmo.com'):
                        sources.insert(0, u)
    except requests.RequestException as exc:
        notes.append('robots.txt: ' + str(exc)[:110])
    return list(dict.fromkeys(sources)), notes


def extract_xml(response):
    raw = response.content
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    root = ET.fromstring(raw)
    name = root.tag.rsplit('}', 1)[-1]
    if name not in ('sitemapindex', 'urlset'):
        raise ValueError('Kein XML-Sitemap-Format: ' + name)
    locs = []
    for element in root.iter():
        if element.tag.rsplit('}', 1)[-1] == 'loc' and element.text:
            locs.append(element.text.strip())
    return name, locs


def exact_sku_in_url(url, sku):
    """Match a whole SKU token in a URL, not FO90540 when searching FO9054."""
    key = normalized_sku(sku)
    if not key:
        return False
    path = unquote(urlparse(url).path).upper()
    query = parse_qs(urlparse(url).query)
    candidates = [path] + [v.upper() for values in query.values() for v in values]
    forms = {key}
    m = re.fullmatch(r'FO(\d{4,})', key)
    if m:
        forms.add('F' + m.group(1))
    return any(re.search(r'(?<![A-Z0-9])' + re.escape(form) + r'(?![A-Z0-9])', text)
               for form in forms for text in candidates)


def refresh(db_url, max_sitemaps=30, max_products=10000, timeout=12, target_sku='FO9054'):
    session = requests.Session()
    session.headers.update(UA)
    queue, notes = discover(session, timeout)
    queue = deque(queue)
    seen = set()
    records = {}
    processed = 0
    samples = []
    sample_seen = set()
    target_sku = normalized_sku(target_sku)
    exact_matches = []
    url_count = 0
    while queue and processed < max_sitemaps and len(records) < max_products:
        url = queue.popleft()
        if url in seen or urlparse(url).hostname not in ('www.brickmo.com', 'brickmo.com'):
            continue
        seen.add(url)
        try:
            r = session.get(url, timeout=timeout)
            if not r.ok:
                notes.append(f'{url}: HTTP {r.status_code}')
                continue
            kind, locs = extract_xml(r)
            processed += 1
            if kind == 'sitemapindex':
                for loc in locs:
                    if loc not in seen:
                        queue.append(loc)
                notes.append(f'Sitemap-Index: {len(locs)} Unter-Sitemaps')
            else:
                url_count += len(locs)
                for loc in locs:
                    if exact_sku_in_url(loc, target_sku) and len(exact_matches) < 25:
                        exact_matches.append(loc)
                # Diagnostic: preserve representative real sitemap URLs even if SKU parsing fails.
                for loc in locs:
                    if len(samples) >= 20: break
                    if loc not in sample_seen and urlparse(loc).hostname in ('www.brickmo.com', 'brickmo.com'):
                        samples.append(loc)
                        sample_seen.add(loc)
                count = 0
                for loc in locs:
                    if valid_product_url(loc):
                        sku = sku_from_url(loc)
                        clean = canonical(loc)
                        title = urlparse(clean).path.rsplit('/', 1)[-1].replace('-', ' ')
                        records[clean] = (clean, sku, title, url)
                        count += 1
                        if len(records) >= max_products:
                            break
                notes.append(f'Produkt-Sitemap: {count} SKU-URLs von {len(locs)} URLs')
        except (requests.RequestException, ET.ParseError, OSError, ValueError) as exc:
            notes.append(f'{url}: {type(exc).__name__}: {str(exc)[:120]}')
    saved = 0
    if records:
        with psycopg.connect(db_url, connect_timeout=12) as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA)
                cur.executemany('''INSERT INTO public.brickmo_catalog(product_url,manufacturer_sku,product_title,source_sitemap,indexed_at)
                    VALUES (%s,%s,%s,%s,now()) ON CONFLICT(product_url) DO UPDATE SET
                    manufacturer_sku=excluded.manufacturer_sku,product_title=excluded.product_title,
                    source_sitemap=excluded.source_sitemap,indexed_at=now()''', list(records.values()))
            saved = len(records)
    return {'saved':saved, 'sitemaps':processed, 'pending_sitemaps':len(queue), 'notes':notes[-18:], 'truncated':bool(queue), 'sample_urls':samples, 'target_sku':target_sku, 'exact_matches':exact_matches, 'scanned_urls':url_count, 'complete':not bool(queue)}


def lookup(db_url, sku, limit=5):
    key = normalized_sku(sku)
    with psycopg.connect(db_url, connect_timeout=12, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.brickmo_catalog') AS t")
            if not cur.fetchone()['t']:
                return [], 'Brickmo-Katalog noch nicht aufgebaut.'
            cur.execute('''SELECT product_url, manufacturer_sku, product_title, indexed_at
                           FROM public.brickmo_catalog WHERE manufacturer_sku=%s
                           ORDER BY indexed_at DESC LIMIT %s''', (key, limit))
            rows = cur.fetchall()
            cur.execute('SELECT count(*) AS n FROM public.brickmo_catalog')
            total = cur.fetchone()['n']
    results = [{'url':r['product_url'],'title':r['product_title'], 'score':95,
                'reason':'Exakte SKU im Brickmo-Katalog (Link prüfen)', 'source':'Brickmo-Sitemap'} for r in rows]
    return results, f'{total} indexierte Brickmo-Produktlinks; {len(results)} exakte Treffer für {key}'

# Targeted, bounded category crawl. Product links are extracted from category
# listings; only exact SKU candidates are considered, and their product detail
# pages are checked before insertion. Never treat a listing hit as verified.
from bs4 import BeautifulSoup
from urllib.parse import urlencode

CATEGORY_START = 'https://www.brickmo.com/themenwelt/architektur/'
CATEGORY_HEADERS = {'User-Agent':'Mozilla/5.0 (compatible; LBrixxPriceCrawler/1.0)', 'Accept-Language':'de-DE,de;q=0.9'}

def _sku_tokens(sku):
    key = normalized_sku(sku)
    m = re.fullmatch(r'FO(\d{4,})', key)
    return {key, 'F'+m.group(1)} if m else {key}

def _detail_sku_confirmed(soup, sku):
    """Check SKU in the product detail content, not unrelated recommendations."""
    main = soup.select_one('.product--details') or soup.select_one('.product--detail') or soup.select_one('.product-details') or soup.select_one('main')
    if main is None: return False
    for node in main.select('script,nav,footer,aside,.product--recommendation,.product-slider,.product--crossselling'):
        node.decompose()
    content = main.get_text(' ', strip=True).upper()
    for token in _sku_tokens(sku):
        if re.search(r'(?<![A-Z0-9])'+re.escape(token)+r'(?![A-Z0-9])',content):
            return True
    return False

def crawl_category_for_sku(db_url, sku, category_url=CATEGORY_START, max_pages=8, timeout=15):
    """Find an exact SKU on public Brickmo category pages, no automatic matching.

    Bounded to max_pages and same-host URLs. The caller sees diagnostics even
    when Brickmo's listing HTML omits products or is blocked.
    """
    target = normalized_sku(sku)
    if not target: return {'matches':[], 'notes':['Artikelnummer fehlt.'], 'pages':0, 'links':0}
    parsed = urlparse(category_url)
    if parsed.scheme != 'https' or parsed.hostname not in ('www.brickmo.com','brickmo.com'):
        raise ValueError('Nur HTTPS-Kategorieseiten von brickmo.com erlaubt.')
    session = requests.Session()
    session.headers.update(CATEGORY_HEADERS)
    # Deterministic pagination: do not follow anchors or arbitrary ?p=22 links.
    # Each listing is visited once, and each canonical product URL is counted once.
    from urllib.parse import urlunparse
    base = urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip('/') + '/', '', '', ''))
    pages_to_check = max(1, min(int(max_pages), 20))
    candidates = {}; notes = []; unique_products = set()
    scanned_links = 0; checked_pages = 0
    for page_number in range(1, pages_to_check + 1):
        page = base if page_number == 1 else f'{base}?p={page_number}'
        checked_pages += 1
        try:
            response = session.get(page, timeout=timeout)
            notes.append(f'Kategorieseite {page_number}: HTTP {response.status_code} – {page}')
            if not response.ok:
                continue
            soup = BeautifulSoup(response.text, 'html.parser')
            new_links = 0
            for a in soup.select('a[href]'):
                href = urljoin(page, a.get('href', ''))
                pu = urlparse(href)
                if pu.hostname not in ('www.brickmo.com', 'brickmo.com'):
                    continue
                if not re.search(r'/\d{3,}/[^/]+/?$', pu.path):
                    continue
                clean = canonical(href)
                if clean in unique_products:
                    continue
                unique_products.add(clean)
                new_links += 1
                if exact_sku_in_url(href, target):
                    candidates[clean] = href
            scanned_links += new_links
            notes.append(f'  {new_links} neue eindeutige Produktlinks; '
                         f'{len(unique_products)} insgesamt; {len(candidates)} exakte SKU-Kandidaten')
            # A category may end before the requested page count; stop on an empty listing.
            if not new_links:
                notes.append('  Keine weiteren neuen Produkte – Suche beendet.')
                break
        except requests.RequestException as exc:
            notes.append(f'Kategorie: {type(exc).__name__}: {str(exc)[:130]}')
    verified = []
    for clean, url in list(candidates.items())[:12]:
        try:
            r = session.get(url, timeout=timeout)
            if not r.ok:
                notes.append(f'Produktseite HTTP {r.status_code}: {url}')
                continue
            soup = BeautifulSoup(r.text,'html.parser')
            if not _detail_sku_confirmed(soup,target):
                notes.append(f'Produktdetail-SKU nicht bestätigt: {url}')
                continue
            title = soup.select_one('h1')
            title = title.get_text(' ',strip=True) if title else urlparse(url).path.rsplit('/',1)[-1].replace('-',' ')
            verified.append({'url':clean,'title':title,'score':100,'reason':'SKU im Produktlink und Produktdetail bestätigt','source':'Brickmo-Kategorie'})
        except requests.RequestException as exc:
            notes.append(f'Produktprüfung: {type(exc).__name__}: {str(exc)[:100]}')
    if verified:
        with psycopg.connect(db_url,connect_timeout=12) as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA)
                cur.executemany('''INSERT INTO public.brickmo_catalog(product_url,manufacturer_sku,product_title,source_sitemap,indexed_at)
                    VALUES (%s,%s,%s,%s,now()) ON CONFLICT(product_url) DO UPDATE SET
                    manufacturer_sku=excluded.manufacturer_sku,product_title=excluded.product_title,
                    source_sitemap=excluded.source_sitemap,indexed_at=now()''',
                    [(v['url'],target,v['title'],'Brickmo-Kategoriesuche') for v in verified])
    return {'matches':verified,'notes':notes,'pages':checked_pages,'links':scanned_links,'candidates':len(candidates)}


# V28.7: Category-wide index; no modification of existing LBrixx offers.
def _category_sku_from_url(url):
    """Conservative brand-aware SKU extraction from Brickmo product slugs.

    Do not infer from Brickmo's internal numeric URL ID. Numbers alone are
    accepted only when the slug explicitly names a supported manufacturer.
    All URL-derived values remain *unconfirmed* until a human reviews them.
    """
    path = unquote(urlparse(url).path).lower().rstrip('/')
    slug = path.rsplit('/', 1)[-1]
    parts = [x for x in re.split(r'[-_]+', slug) if x]
    if not parts:
        return ''
    # FunWhole / LumiBricks: FO9054, F9054, FO-9054
    if 'funwhole' in parts or 'lumibricks' in parts:
        for match in re.finditer(r'(?<![a-z0-9])(?:fo?-?)(\d{4,5})(?![a-z0-9])', slug):
            return 'FO' + match.group(1)
        # LumiBricks also uses numeric manufacturer SKUs, e.g. 18010, 17011.
        # Require the identifier to be the final slug token to avoid model numbers in titles.
        m = re.search(r'(?:^|[-_])(lb\d{3,6}|l\d{4,6}|\d{4,6})$', slug)
        if m:
            return normalized_sku(m.group(1))
    # Other explicitly identified manufacturers: match trailing model number.
    brands = ('wange', 'mould', 'mouldking', 'cada', 'cobi', 'pantasy', 'reobrix')
    if any(token in parts for token in brands):
        m = re.search(r'(?:^|[-_])([a-z]{0,3}\d{3,7}[a-z]?)$', slug)
        if m:
            return m.group(1).upper()
    return ''


def _last_category_page(soup, category_path, safety_cap=250):
    """Read explicit pagination links, never follow fragment or filter links."""
    pages = [1]
    for a in soup.select('a[href]'):
        href = a.get('href', '')
        parsed = urlparse(href)
        if parsed.path and parsed.path.rstrip('/') != category_path.rstrip('/'):
            continue
        for raw in parse_qs(parsed.query).get('p', []):
            if raw.isdigit():
                number = int(raw)
                if 1 <= number <= safety_cap:
                    pages.append(number)
    return max(pages)


def index_category(db_url, category_url=CATEGORY_START, max_pages=250, timeout=15):
    """Index all confidently identifiable SKU URLs from one Brickmo category.

    A URL-derived SKU is a suggestion, not a verified product-detail SKU.
    No existing catalog entries are deleted. LBrixx product mappings remain unchanged.
    """
    from urllib.parse import urlunparse
    parsed = urlparse(category_url)
    if parsed.scheme != 'https' or parsed.hostname not in ('www.brickmo.com', 'brickmo.com'):
        raise ValueError('Nur HTTPS-Kategorieseiten von brickmo.com erlaubt.')
    base = urlunparse(('https', 'www.brickmo.com', parsed.path.rstrip('/') + '/', '', '', ''))
    cap = max(1, min(int(max_pages), 250))
    session = requests.Session()
    session.headers.update(CATEGORY_HEADERS)
    notes, records, all_links = [], {}, set()
    total_pages = None
    checked = 0
    for page_no in range(1, cap + 1):
        if total_pages is not None and page_no > total_pages:
            break
        url = base if page_no == 1 else f'{base}?p={page_no}'
        try:
            response = session.get(url, timeout=timeout)
            if not response.ok:
                notes.append(f'Seite {page_no}: HTTP {response.status_code}')
                break
            soup = BeautifulSoup(response.text, 'html.parser')
            if total_pages is None:
                total_pages = _last_category_page(soup, parsed.path, safety_cap=cap)
                notes.append(f'Zunächst erkannte Seitenanzahl: {total_pages}')
            else:
                discovered = _last_category_page(soup, parsed.path, safety_cap=cap)
                if discovered > total_pages:
                    total_pages = discovered
                    notes.append(f'Seitennavigation erweitert auf: {total_pages}')
            new = 0
            for a in soup.select('a[href]'):
                href = urljoin(url, a.get('href', ''))
                pu = urlparse(href)
                if pu.hostname not in ('www.brickmo.com', 'brickmo.com'):
                    continue
                if not re.search(r'/\d{3,}/[^/]+/?$', pu.path):
                    continue
                clean = canonical(href)
                if clean in all_links:
                    continue
                all_links.add(clean)
                new += 1
                sku = _category_sku_from_url(href)
                if sku:
                    slug = unquote(pu.path.rsplit('/', 1)[-1]).replace('-', ' ')
                    records[clean] = (clean, sku, slug, 'Brickmo-Kategorie: SKU aus URL (unbestätigt)')
            checked += 1
            notes.append(f'Seite {page_no}: {new} neue Produktlinks; {len(all_links)} insgesamt; {len(records)} SKU-Links')
            if new == 0:
                notes.append('Keine neuen Produktlinks, Kategorie beendet.')
                break
        except requests.RequestException as exc:
            notes.append(f'Seite {page_no}: {type(exc).__name__}: {str(exc)[:100]}')
            break
    if records:
        with psycopg.connect(db_url, connect_timeout=12) as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA)
                # Existing entries are kept; fresh links updated in a single transaction.
                cur.executemany("""INSERT INTO public.brickmo_catalog
                    (product_url,manufacturer_sku,product_title,source_sitemap,indexed_at)
                    VALUES (%s,%s,%s,%s,now())
                    ON CONFLICT(product_url) DO UPDATE SET
                    manufacturer_sku=CASE WHEN strpos(COALESCE(public.brickmo_catalog.source_sitemap, ''), 'bestätigt') > 0
                        THEN public.brickmo_catalog.manufacturer_sku ELSE excluded.manufacturer_sku END,
                    product_title=excluded.product_title,
                    source_sitemap=CASE
                        WHEN public.brickmo_catalog.source_sitemap='Brickmo-Kategoriesuche'
                        THEN public.brickmo_catalog.source_sitemap
                        ELSE excluded.source_sitemap END,
                    indexed_at=now()""", list(records.values()))
    return {'pages_detected':total_pages or 1,'pages_checked':checked,
            'links_found':len(all_links),'sku_links':len(records),
            'saved':len(records),'notes':notes,
            'complete':checked >= (total_pages or 1) and (total_pages or 1) <= cap,
            'sample_skus':sorted(set(row[1] for row in records.values()))[:20]}


def batch_match_skus(db_url, skus):
    """One read-only DB query to compare imported LBrixx SKUs with the index."""
    keys = list({normalized_sku(x) for x in skus if str(x or '').strip()})
    if not keys:
        return {}
    with psycopg.connect(db_url, connect_timeout=12, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.brickmo_catalog') AS t")
            if not cur.fetchone()['t']:
                return {}
            cur.execute("""SELECT manufacturer_sku,product_url,product_title,source_sitemap
                FROM public.brickmo_catalog WHERE manufacturer_sku = ANY(%s)
                ORDER BY indexed_at DESC""", (keys,))
            rows = cur.fetchall()
    results = {}
    for row in rows:
        results.setdefault(row['manufacturer_sku'], []).append(dict(row))
    return results


def enrich_category_skus(db_url, category_url, limit=25, timeout=15):
    """Bounded opt-in detail enrichment for links lacking SKU in a category.

    Does not update LBrixx offers; records a SKU only if the product page explicitly
    identifies one. Never guesses from generic text or other products.
    """
    import time
    from bs4 import BeautifulSoup
    category_path = urlparse(category_url).path.rstrip('/') + '/'
    cap = max(1, min(int(limit), 50))
    session = requests.Session()
    session.headers.update(CATEGORY_HEADERS)
    # Retrieve category pages to collect links not yet in catalog. Limit network requests.
    first = session.get(category_url, timeout=timeout)
    first.raise_for_status()
    first_soup = BeautifulSoup(first.text, 'html.parser')
    total = min(_last_category_page(first_soup, category_path, safety_cap=60), 60)
    urls = []
    seen = set()
    for page in range(1, total + 1):
        if page == 1:
            soup = first_soup
        else:
            r = session.get(category_url.rstrip('/') + '/?p=' + str(page), timeout=timeout)
            if not r.ok:
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
        for a in soup.select('a[href]'):
            u = urljoin(category_url, a.get('href', ''))
            if urlparse(u).hostname not in ('brickmo.com', 'www.brickmo.com'):
                continue
            if not re.search(r'/\d{3,}/[^/]+/?$', urlparse(u).path):
                continue
            u = canonical(u)
            if u not in seen and not _category_sku_from_url(u):
                seen.add(u)
                urls.append(u)
        if len(urls) >= cap:
            break
    notes, records, checked = [], [], 0
    for u in urls[:cap]:
        try:
            r = session.get(u, timeout=timeout)
            checked += 1
            if not r.ok:
                notes.append(f'{u}: HTTP {r.status_code}')
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
            # Check only the main product details, never related-product listings.
            main = soup.select_one('.product--details') or soup.select_one('.product--detail') or soup.select_one('main') or soup
            content = main.get_text(' ', strip=True)
            found = re.search(r'(?:Produkt-Nr\.|Artikel-Nr\.|Artikelnummer|Produktnummer)\s*:?\s*([A-Z]{0,4}[- ]?\d{3,7}[A-Z]?)\b', content, re.I)
            if not found:
                notes.append(f'{u}: keine eindeutig beschriftete Artikelnummer')
                continue
            sku = normalized_sku(found.group(1).replace(' ', '').replace('-', ''))
            title = soup.select_one('h1')
            title = title.get_text(' ', strip=True) if title else u.rsplit('/', 1)[-1].replace('-', ' ')
            records.append((u, sku, title, 'Brickmo-Produktdetails (SKU bestätigt)'))
        except requests.RequestException as exc:
            notes.append(f'{u}: {type(exc).__name__}: {str(exc)[:90]}')
        time.sleep(0.6)
    if records:
        with psycopg.connect(db_url, connect_timeout=12) as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA)
                cur.executemany("""INSERT INTO public.brickmo_catalog
                    (product_url,manufacturer_sku,product_title,source_sitemap,indexed_at)
                    VALUES (%s,%s,%s,%s,now())
                    ON CONFLICT(product_url) DO UPDATE SET
                    manufacturer_sku=excluded.manufacturer_sku,
                    product_title=excluded.product_title,
                    source_sitemap=excluded.source_sitemap,
                    indexed_at=now()""", records)
    return {'checked': checked, 'found': len(records), 'saved': len(records), 'notes': notes[:40]}
