"""Independent, review-only Lootstore Shopify collection index.

Does not change LBrixx products, Brickmo data or historical prices.
"""
import re
import time
from urllib.parse import urlparse
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

def index_collection(db_url, max_pages=10, timeout=20):
    """Fetch collection-scoped Shopify JSON; only SKU-bearing variants are indexed."""
    session = requests.Session()
    session.headers.update(HEADERS)
    records = {}
    notes = []
    checked = 0
    complete = False
    product_count = 0
    cap = min(max(1, int(max_pages)), 25)
    for page in range(1, cap+1):
        url = f'{COLLECTION}/products.json?limit=250&page={page}'
        try:
            response = session.get(url, timeout=timeout)
            if response.status_code == 429:
                notes.append(f'Seite {page}: HTTP 429 – bitte später erneut versuchen.')
                break
            response.raise_for_status()
            products = response.json().get('products')
            if not isinstance(products, list):
                notes.append(f'Seite {page}: keine Shopify-Produktliste im JSON.')
                break
            checked += 1
            if not products:
                complete = True
                notes.append(f'Seite {page}: keine weiteren Produkte.')
                break
            product_count += len(products)
            for product in products:
                handle = str(product.get('handle') or '').strip()
                if not re.fullmatch(r'[a-zA-Z0-9_-]+', handle):
                    continue
                product_url = 'https://lootstore.de/products/' + handle
                title = str(product.get('title') or handle).strip()
                vendor = str(product.get('vendor') or '').strip()
                if not vendor:
                    continue  # Unknown manufacturer must never create a match.
                for variant in product.get('variants') or []:
                    sku = norm(variant.get('sku'))
                    if not sku or len(sku) < 3:
                        continue
                    records[(product_url, sku)] = (product_url, sku, title, vendor, COLLECTION)
            notes.append(f'Seite {page}: {len(products)} Produkte, {len(records)} eindeutige SKU-Varianten.')
            if len(products) < 250:
                complete = True
                break
            time.sleep(0.25)
        except (requests.RequestException, ValueError) as exc:
            notes.append(f'Seite {page}: {type(exc).__name__}: {str(exc)[:140]}')
            break
    saved = 0
    if records:
        # A single URL may have multiple variants. Store the first variant only
        # when SKU is unambiguous; ambiguous multi-SKU products need manual review.
        by_url = {}
        ambiguous = set()
        for row in records.values():
            if row[0] in by_url and by_url[row[0]][1] != row[1]:
                ambiguous.add(row[0])
            else:
                by_url[row[0]] = row
        for url in ambiguous:
            by_url.pop(url, None)
        if ambiguous:
            notes.append(f'{len(ambiguous)} Produkte mit mehreren SKUs übersprungen (manuelle Prüfung).')
        if by_url:
            with psycopg.connect(db_url, connect_timeout=12) as conn:
                with conn.cursor() as cur:
                    cur.execute(SCHEMA)
                    cur.executemany('''INSERT INTO public.lootstore_catalog
                        (product_url,manufacturer_sku,product_title,brand_name,source_collection,indexed_at)
                        VALUES (%s,%s,%s,%s,%s,now()) ON CONFLICT (product_url) DO UPDATE SET
                        manufacturer_sku=excluded.manufacturer_sku,
                        product_title=excluded.product_title, brand_name=excluded.brand_name,
                        source_collection=excluded.source_collection, indexed_at=now()''', list(by_url.values()))
            saved = len(by_url)
    return {'saved':saved, 'pages_checked':checked, 'products_seen':product_count,
            'complete':complete, 'notes':notes, 'sample_skus':[v[1] for v in list(records.values())[:15]]}

def batch_match_skus(db_url, skus):
    keys = sorted({norm(s) for s in skus if norm(s)})
    if not keys:
        return {}
    with psycopg.connect(db_url, connect_timeout=12, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.lootstore_catalog') AS table_name")
            if not cur.fetchone()['table_name']:
                return {}
            cur.execute('''SELECT product_url,manufacturer_sku,product_title,brand_name
                FROM public.lootstore_catalog WHERE manufacturer_sku = ANY(%s)''', (keys,))
            rows = cur.fetchall()
    result = {key:[] for key in keys}
    for row in rows:
        result[row['manufacturer_sku']].append(dict(row))
    return result
