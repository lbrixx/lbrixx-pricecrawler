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
    return 'FO' + m.group(1) if m else s


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
    queue = deque([category_url])
    visited = set(); candidates = {}; notes = []; scanned_links = 0
    while queue and len(visited) < max(1,min(int(max_pages),20)):
        page = queue.popleft()
        if page in visited: continue
        visited.add(page)
        try:
            response = session.get(page,timeout=timeout)
            notes.append(f'Kategorieseite {len(visited)}: HTTP {response.status_code} – {page}')
            if not response.ok: continue
            soup = BeautifulSoup(response.text,'html.parser')
            page_links = 0
            for a in soup.select('a[href]'):
                href = urljoin(page, a.get('href',''))
                pu = urlparse(href)
                if pu.hostname not in ('www.brickmo.com','brickmo.com'): continue
                if re.search(r'/\d{3,}/[^/]+/?$',pu.path):
                    page_links += 1
                    if exact_sku_in_url(href,target):
                        clean = canonical(href)
                        candidates[clean] = href
                # Follow category pagination only, not arbitrary site links.
                if ('page=' in pu.query or 'p=' in pu.query) and pu.path.rstrip('/') == parsed.path.rstrip('/'):
                    if href not in visited and href not in queue and len(queue) < 20:
                        queue.append(href)
            scanned_links += page_links
            notes.append(f'  {page_links} Produktlinks, {len(candidates)} exakte SKU-URL-Kandidaten')
            # Shopware pagination often uses ?p=N without an explicit next link.
            if not queue and page_links and len(visited) < max_pages:
                n = len(visited)+1
                next_page = category_url + ('&' if '?' in category_url else '?') + urlencode({'p':n})
                if next_page not in visited: queue.append(next_page)
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
    return {'matches':verified,'notes':notes,'pages':len(visited),'links':scanned_links,'candidates':len(candidates)}
