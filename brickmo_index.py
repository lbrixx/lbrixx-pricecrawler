"""Persistent, human-reviewed Brickmo catalog index sourced from public sitemaps.

No claim of complete coverage: each refresh is bounded and diagnostic information is returned.
"""
from __future__ import annotations
import gzip
import re
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin, parse_qs
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


def refresh(db_url, max_sitemaps=30, max_products=10000, timeout=12):
    session = requests.Session()
    session.headers.update(UA)
    queue, notes = discover(session, timeout)
    queue = deque(queue)
    seen = set()
    records = {}
    processed = 0
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
    return {'saved':saved, 'sitemaps':processed, 'pending_sitemaps':len(queue), 'notes':notes[-18:], 'truncated':bool(queue)}


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
