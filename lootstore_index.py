"""Independent, review-only Lootstore Shopify collection index.

Does not change LBrixx products, Brickmo data or historical prices.
"""
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, urljoin, quote_plus
from bs4 import BeautifulSoup
import requests
import psycopg
from psycopg.rows import dict_row

COLLECTION = 'https://lootstore.de/collections/klemmbausteine'
SCHEMA = '''CREATE TABLE IF NOT EXISTS public.lootstore_catalog (
    product_url text PRIMARY KEY,
    manufacturer_sku text NOT NULL,
    product_title text NOT NULL,
    brand_name text NOT NULL,
    source_collection text NOT NULL,
    indexed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS lootstore_catalog_sku_idx ON public.lootstore_catalog(manufacturer_sku);'''
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; LBrixxPriceCrawler/1.0; catalog review)', 'Accept':'application/json'}

def norm(s):
    return re.sub(r'[^A-Z0-9_]', '', str(s or '').upper())

def brand(s):
    key = re.sub(r'[^a-z0-9]', '', str(s or '').casefold())
    return {'mattel':'mattelbrickshop', 'funwhole':'funwhole', 'lumibricks':'lumibricks'}.get(key, key)

def _product_handles_from_html(html_text):
    """Collect product handles from Shopify collection/search HTML, including sold-out cards."""
    soup = BeautifulSoup(html_text, 'html.parser')
    handles = set()
    for a in soup.select('a[href*="/products/"]'):
        href = a.get('href', '')
        match = re.search(r'/products/([a-zA-Z0-9_-]+)', href)
        if match:
            handles.add(match.group(1))
    return handles


def _title_url_sku(item):
    """Conservative Pantasy fallback: vendor + exact five-digit model in title and URL.

    Never infer a SKU when variants disagree or multiple model numbers occur.
    """
    if brand(item.get('brand')) != 'pantasy' or item.get('skus'):
        return None
    title = str(item.get('title') or '')
    path = urlparse(str(item.get('url') or '')).path.lower()
    title_numbers = set(re.findall(r'(?<!\d)\d{5}(?!\d)', title))
    url_numbers = set(re.findall(r'(?<!\d)\d{5}(?!\d)', path))
    common = title_numbers & url_numbers
    return next(iter(common)) if len(common) == 1 and len(title_numbers) == 1 else None


def _record_for_item(item):
    if not item or not item.get('brand'):
        return None, None
    skus = item.get('skus') or []
    if len(skus) == 1:
        sku = skus[0]
        return (item['url'], sku, item['title'], item['brand'], COLLECTION), 'variant'
    if not skus:
        sku = _title_url_sku(item)
        if sku:
            return (item['url'], sku, item['title'], item['brand'], COLLECTION), 'title_url'
    return None, None

def _fetch_product(session, handle, timeout=20):
    """Read canonical Shopify product JSON and return unambiguous SKU data."""
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', handle):
        return None
    response = session.get('https://lootstore.de/products/' + handle + '.js', timeout=timeout)
    response.raise_for_status()
    product = response.json()
    skus = {norm(v.get('sku')) for v in product.get('variants', []) if norm(v.get('sku'))}
    return {'url':'https://lootstore.de/products/' + handle,
            'title':str(product.get('title') or handle).strip(),
            'brand':str(product.get('vendor') or '').strip(),
            'skus':sorted(skus)}


def _html_collection_handles(session, max_pages, timeout, notes):
    """Supplement Shopify JSON with product cards from collection HTML."""
    found = set()
    for page in range(1, max_pages + 1):
        try:
            response = session.get(COLLECTION, params={'page':page}, timeout=timeout,
                                   headers={'Accept':'text/html'})
            response.raise_for_status()
            handles = _product_handles_from_html(response.text)
            notes.append(f'HTML-Kategorieseite {page}: {len(handles)} Produktlinks.')
            new = handles - found
            found.update(handles)
            if not handles or (page > 1 and not new):
                break
        except requests.RequestException as exc:
            notes.append(f'HTML-Kategorieseite {page}: {type(exc).__name__}: {str(exc)[:120]}')
            break
    return found

def _sitemap_product_handles(session, timeout, notes, max_sitemaps=30, max_handles=2500):
    """Read same-host Shopify product sitemaps; bound discovery and never follow off-site URLs."""
    pending = ['https://lootstore.de/sitemap.xml']
    visited = set()
    handles = set()
    while pending and len(visited) < max_sitemaps and len(handles) < max_handles:
        url = pending.pop(0)
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname not in ('lootstore.de', 'www.lootstore.de'):
            continue
        if url in visited:
            continue
        visited.add(url)
        try:
            response = session.get(url, timeout=timeout, headers={'Accept': 'application/xml,text/xml'})
            response.raise_for_status()
            root = ET.fromstring(response.content)
            locs = [el.text.strip() for el in root.iter() if el.tag.rsplit('}', 1)[-1] == 'loc' and el.text]
            for loc in locs:
                target = urlparse(loc)
                if target.scheme != 'https' or target.hostname not in ('lootstore.de', 'www.lootstore.de'):
                    continue
                path = target.path
                match = re.fullmatch(r'/products/([a-zA-Z0-9_-]+)/?', path)
                if match:
                    handles.add(match.group(1))
                elif path.endswith('.xml') and ('sitemap_products' in path or path == '/sitemap.xml'):
                    if loc not in visited and loc not in pending:
                        pending.append(loc)
                if len(handles) >= max_handles:
                    break
        except (requests.RequestException, ET.ParseError, ValueError) as exc:
            notes.append(f'Sitemap {urlparse(url).path}: {type(exc).__name__}: {str(exc)[:100]}')
    notes.append(f'Produktsitemap: {len(visited)} XML-Dateien geprüft, {len(handles)} Produktlinks entdeckt.')
    if pending or len(handles) >= max_handles:
        notes.append('Sitemap-Sicherheitslimit erreicht; Index möglicherweise unvollständig.')
    return handles

PROGRESS_SCHEMA = '''CREATE TABLE IF NOT EXISTS public.lootstore_index_progress (
    product_handle text PRIMARY KEY,
    status text NOT NULL,
    checked_at timestamptz NOT NULL DEFAULT now()
);'''


def index_collection(db_url, max_pages=10, timeout=20, batch_limit=20, delay_seconds=2):
    # Resume a polite, Pantasy-only sitemap crawl. Never retry already checked handles.

    # HTTP 429 ends the run immediately; successfully processed handles are committed.
    # Existing catalog rows are preserved, including manually indexed items.
    session = requests.Session()
    session.headers.update(HEADERS)
    notes = []
    saved = 0
    checked = 0
    fallback_count = 0
    limited = False
    batch_limit = min(max(int(batch_limit), 1), 20)
    delay_seconds = max(float(delay_seconds), 2.0)

    with psycopg.connect(db_url, connect_timeout=12) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            cur.execute(PROGRESS_SCHEMA)
            # Already indexed products must not be requested again.
            cur.execute("SELECT product_url FROM public.lootstore_catalog WHERE lower(brand_name) = 'pantasy'")
            existing = {urlparse(row[0]).path.rstrip('/').split('/')[-1] for row in cur.fetchall()}
            cur.execute('SELECT product_handle FROM public.lootstore_index_progress')
            completed = {row[0] for row in cur.fetchall()}
        conn.commit()

        try:
            sitemap_handles = _sitemap_product_handles(session, timeout, notes)
        except requests.RequestException as exc:
            notes.append(f'Sitemap konnte nicht gelesen werden: {exc}')
            sitemap_handles = set()
        # The product slug is a discovery hint only. Manufacturer is verified from Shopify JSON.
        pantasy_handles = sorted(h for h in sitemap_handles if 'pantasy' in h.casefold())
        pending = [h for h in pantasy_handles if h not in existing and h not in completed]
        notes.append(f'Pantasy-Kandidaten aus Sitemap: {len(pantasy_handles)} · bereits indexiert: {len(existing & set(pantasy_handles))} · früher geprüft: {len(completed & set(pantasy_handles))} · offen: {len(pending)}.')
        sample_skus = []
        for handle in pending[:batch_limit]:
            # Wait BEFORE each request, not after, to avoid bursts on reruns.
            time.sleep(delay_seconds)
            try:
                item = _fetch_product(session, handle, timeout)
            except requests.HTTPError as exc:
                response = exc.response
                if response is not None and response.status_code == 429:
                    retry_after = response.headers.get('Retry-After', '')
                    notes.append('HTTP 429: sofort gestoppt.' + (f' Retry-After: {retry_after}' if retry_after else ' Bitte später fortsetzen.'))
                    limited = True
                    break
                notes.append(f'Produkt {handle}: HTTP-Fehler {response.status_code if response is not None else "unbekannt"}; nicht als geprüft markiert.')
                break
            except (requests.RequestException, ValueError) as exc:
                notes.append(f'Produkt {handle}: {type(exc).__name__}; Lauf angehalten, später erneut versuchen.')
                break

            if brand(item.get('brand')) != 'pantasy':
                row, source = None, None
                status = 'other_brand'
            else:
                row, source = _record_for_item(item)
                status = 'indexed' if row else 'no_unique_sku'
            with conn.cursor() as cur:
                if row:
                    cur.execute('''INSERT INTO public.lootstore_catalog
                        (product_url,manufacturer_sku,product_title,brand_name,source_collection,indexed_at)
                        VALUES (%s,%s,%s,%s,%s,now()) ON CONFLICT (product_url) DO UPDATE SET
                        manufacturer_sku=excluded.manufacturer_sku,
                        product_title=excluded.product_title,brand_name=excluded.brand_name,
                        source_collection=excluded.source_collection,indexed_at=now()''', row)
                    saved += 1
                    sample_skus.append(row[1])
                    fallback_count += (source == 'title_url')
                cur.execute('''INSERT INTO public.lootstore_index_progress (product_handle,status,checked_at)
                    VALUES (%s,%s,now()) ON CONFLICT (product_handle) DO UPDATE SET
                    status=excluded.status,checked_at=now()''', (handle, status))
            conn.commit()
            checked += 1
        remaining = max(0, len(pending) - checked)
        notes.append(f'Dieser Durchlauf: {checked} Produktprüfungen, {saved} Pantasy-Links gespeichert, {fallback_count} Titel/URL-Erkennungen. Noch {remaining} Kandidaten offen.')
        if not limited and remaining:
            notes.append('Fortsetzung: Schaltfläche erneut anklicken. Bereits geprüfte Artikel werden übersprungen.')
        return {'saved':saved, 'pages_checked':0, 'products_seen':checked,
                'complete':not limited and remaining == 0 and bool(sitemap_handles),
                'html_added':0, 'sitemap_added':saved, 'fallback_count':fallback_count,
                'notes':notes, 'sample_skus':sample_skus, 'remaining':remaining,
                'rate_limited':limited, 'checked':checked}

def index_product_url(db_url, product_url, timeout=20):
    """Index one user-supplied Lootstore URL after verifying its vendor and SKU."""
    parsed = urlparse(str(product_url).strip())
    if parsed.scheme != 'https' or parsed.hostname not in ('lootstore.de', 'www.lootstore.de'):
        raise ValueError('Nur HTTPS-Produktlinks von lootstore.de sind erlaubt.')
    match = re.fullmatch(r'/products/([a-zA-Z0-9_-]+)/?', parsed.path)
    if not match:
        raise ValueError('Bitte einen gültigen Lootstore-Produktlink eingeben.')
    session = requests.Session()
    session.headers.update(HEADERS)
    item = _fetch_product(session, match.group(1), timeout)
    row, source = _record_for_item(item)
    if not row:
        raise ValueError('Keine eindeutige Artikelnummer: Hersteller, SKU oder Titel/URL prüfen.')
    with psycopg.connect(db_url, connect_timeout=12) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            cur.execute("""INSERT INTO public.lootstore_catalog
                (product_url,manufacturer_sku,product_title,brand_name,source_collection,indexed_at)
                VALUES (%s,%s,%s,%s,%s,now()) ON CONFLICT (product_url) DO UPDATE SET
                manufacturer_sku=excluded.manufacturer_sku,
                product_title=excluded.product_title, brand_name=excluded.brand_name,
                source_collection=excluded.source_collection, indexed_at=now()""", row)
    return {'sku':row[1], 'title':row[2], 'brand':row[3], 'source':source, 'url':row[0]}

def match_key(s):
    key = norm(s)
    for prefix in ('PANTASY', 'PAN'):
        if key.startswith(prefix) and len(key) > len(prefix):
            return key[len(prefix):]
    return key

def batch_match_skus(db_url, skus):
    keys = sorted({match_key(s) for s in skus if match_key(s)})
    if not keys:
        return {}
    with psycopg.connect(db_url, connect_timeout=12, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.lootstore_catalog') AS table_name")
            if not cur.fetchone()['table_name']:
                return {}
            cur.execute('''SELECT product_url,manufacturer_sku,product_title,brand_name
                FROM public.lootstore_catalog WHERE lower(regexp_replace(brand_name, '[^a-zA-Z0-9]', '', 'g')) = 'pantasy' ''')
            rows = cur.fetchall()
    result = {key:[] for key in keys}
    for row in rows:
        key = match_key(row['manufacturer_sku'])
        if key in result:
            result[key].append(dict(row))
    return result


def diagnose_sku(db_url, sku, timeout=18, product_url=''):
    """Read-only diagnosis: stored index, Shopify search, HTML search and direct URL.

    No automatic match or write. Product title matches are reported separately
    from verified variant-SKU matches.
    """
    key = match_key(sku)
    if not re.fullmatch(r'[A-Z0-9_]{3,30}', key):
        raise ValueError('Bitte eine gültige Artikelnummer eingeben.')
    stored = []
    with psycopg.connect(db_url, connect_timeout=12, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.lootstore_catalog') AS table_name")
            if cur.fetchone()['table_name']:
                cur.execute("""SELECT product_url, manufacturer_sku, product_title, brand_name
                    FROM public.lootstore_catalog""")
                stored = [dict(r) for r in cur.fetchall()
                          if match_key(r['manufacturer_sku']) == key]
    session = requests.Session()
    session.headers.update(HEADERS)
    live = []
    unverified = []
    errors = []
    handles = set()
    if product_url.strip():
        parsed = urlparse(product_url.strip())
        if parsed.scheme != 'https' or parsed.hostname not in ('lootstore.de', 'www.lootstore.de'):
            raise ValueError('Nur HTTPS-Produktlinks von lootstore.de sind erlaubt.')
        match = re.fullmatch(r'/products/([a-zA-Z0-9_-]+)/?', parsed.path)
        if not match:
            raise ValueError('Bitte eine Lootstore-Produkt-URL angeben.')
        handles.add(match.group(1))
    try:
        response = session.get('https://lootstore.de/search/suggest.json',
            params={'q':key,'resources[type]':'product','resources[limit]':10}, timeout=timeout)
        response.raise_for_status()
        products = response.json().get('resources',{}).get('results',{}).get('products',[])
        for p in products:
            handle = str(p.get('handle') or '').strip()
            url = str(p.get('url') or '')
            if not handle and '/products/' in url:
                handle = url.split('/products/',1)[1].split('?',1)[0].split('/',1)[0]
            if re.fullmatch(r'[a-zA-Z0-9_-]+',handle):
                handles.add(handle)
    except (requests.RequestException, ValueError) as exc:
        errors.append(f'Shopify-Suche: {type(exc).__name__}: {str(exc)[:110]}')
    # Theme search HTML can surface items excluded from predictive search.
    try:
        response = session.get('https://lootstore.de/search',
            params={'q':key, 'type':'product'}, timeout=timeout,
            headers={'Accept':'text/html'})
        response.raise_for_status()
        handles.update(_product_handles_from_html(response.text))
    except requests.RequestException as exc:
        errors.append(f'HTML-Suche: {type(exc).__name__}: {str(exc)[:110]}')
    for handle in sorted(handles)[:35]:
        try:
            item = _fetch_product(session, handle, timeout)
            if not item:
                continue
            item['pantasy'] = brand(item['brand']) == 'pantasy'
            if any(match_key(s) == key for s in item['skus']) or _title_url_sku(item) == key:
                item['source'] = 'Varianten-SKU' if any(match_key(s) == key for s in item['skus']) else 'Pantasy: Titel und URL'
                live.append(item)
            elif re.search(r'(?<![0-9])' + re.escape(key) + r'(?![0-9])', item['title'], re.I):
                item['reason'] = 'Artikelnummer nur im Titel, nicht in der Shopify-Varianten-SKU bestätigt'
                unverified.append(item)
        except (requests.RequestException, ValueError) as exc:
            errors.append(f'Produkt {handle}: {type(exc).__name__}: {str(exc)[:110]}')
    return {'sku':key, 'stored':stored, 'live':live, 'unverified':unverified,
            'checked_handles':len(handles), 'errors':errors}
