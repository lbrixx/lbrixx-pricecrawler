"""Lumibricks offer storage and cautious EUR price extraction."""
import re, json, time
from urllib.parse import urlparse
from datetime import datetime, timezone
from decimal import Decimal
import requests
import psycopg
from psycopg.rows import dict_row
from bs4 import BeautifulSoup

BASE='https://www.lumibricks.com'
HEADERS={'User-Agent':'Mozilla/5.0 (compatible; LBrixxPriceCrawler/1.0)', 'Accept-Language':'de-DE,de;q=0.9'}

def sku_key(value):
    raw=str(value or '').upper().split('#')[0].strip()
    raw=re.sub(r'[^A-Z0-9]','',raw)
    if raw.startswith('LB') and raw[2:].isdigit(): return raw[2:]
    if raw.startswith('L') and raw[1:].isdigit(): return raw[1:]
    if raw.startswith('F') and raw[1:].isdigit(): return raw[1:]
    return raw

def valid_url(url):
    p=urlparse(str(url).strip())
    return p.scheme=='https' and p.hostname in ('lumibricks.com','www.lumibricks.com') and p.path.startswith(('/de/products/','/products/'))

def canonical(url):
    if not valid_url(url): raise ValueError('Ungültige Lumibricks-Produktadresse')
    slug=urlparse(url).path.rstrip('/').split('/')[-1]
    return BASE+'/de/products/'+slug

def connect(url): return psycopg.connect(url,connect_timeout=12)

def ensure_merchant(url):
    with connect(url) as c, c.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id FROM public.merchants WHERE name='Lumibricks'")
        r=cur.fetchone()
        if not r:
            cur.execute("INSERT INTO public.merchants(name,is_active) VALUES('Lumibricks',true) RETURNING id")
            r=cur.fetchone()
        return r['id']

def overlay(url,df):
    df=df.copy()
    for k in ('lumibricks_url','lumibricks_price','lumibricks_shipping','lumibricks_total','lumibricks_availability','lumibricks_checked','lumibricks_error'):
        if k not in df: df[k]=''
    with connect(url) as c,c.cursor(row_factory=dict_row) as cur:
        cur.execute("""SELECT o.product_id,o.product_url,cp.price,cp.shipping_cost,cp.availability,cp.last_success_at,cp.error_message
          FROM public.offers o JOIN public.merchants m ON m.id=o.merchant_id
          LEFT JOIN public.current_prices cp ON cp.offer_id=o.id
          WHERE m.name='Lumibricks' AND o.is_active=true""")
        rows=cur.fetchall()
    lookup={str(v):i for i,v in df['db_id'].items()}
    for r in rows:
        idx=lookup.get(str(r['product_id']))
        if idx is None: continue
        def val(k): return '' if r[k] is None else str(r[k])
        df.at[idx,'lumibricks_url']=val('product_url')
        df.at[idx,'lumibricks_price']=val('price')
        df.at[idx,'lumibricks_shipping']='0.00' if r['price'] is not None else ''
        df.at[idx,'lumibricks_total']=val('price')
        df.at[idx,'lumibricks_availability']={'available':'Lieferbar','unavailable':'Nicht lieferbar'}.get(r['availability'],'Unbestätigt')
        df.at[idx,'lumibricks_checked']=r['last_success_at'].astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC') if r['last_success_at'] else ''
        df.at[idx,'lumibricks_error']=val('error_message')
    return df

def confirm(url,pid,product_url):
    clean=canonical(product_url)
    mid=ensure_merchant(url)
    with connect(url) as c,c.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT id FROM public.products WHERE id=%s AND is_deleted=false',(int(pid),))
        if not cur.fetchone(): raise ValueError('Produkt nicht vorhanden')
        cur.execute('SELECT product_id FROM public.offers WHERE merchant_id=%s AND product_url=%s AND product_id<>%s AND is_active=true',(mid,clean,int(pid)))
        if cur.fetchone(): raise ValueError('Lumibricks-Link ist bereits einem anderen Artikel zugeordnet')
        cur.execute("""INSERT INTO public.offers(product_id,merchant_id,product_url,is_active,match_status)
          VALUES(%s,%s,%s,true,'confirmed') ON CONFLICT(product_id,merchant_id)
          DO UPDATE SET product_url=excluded.product_url,is_active=true,match_status='confirmed'""",(int(pid),mid,clean))
    return clean

def catalog(limit_pages=4):
    """Public Shopify catalog; returns SKU-indexed candidates, no DB writes."""
    result={}; seen=set(); diagnostics=[]
    session=requests.Session();session.headers.update(HEADERS)
    for page in range(1,limit_pages+1):
        try:
            r=session.get(BASE+'/products.json',params={'limit':100,'page':page},timeout=25)
            if r.status_code==429:
                diagnostics.append('HTTP 429: Suche beendet');break
            r.raise_for_status()
            products=r.json().get('products',[])
        except Exception as e:
            diagnostics.append(f'Seite {page}: {e}');break
        if not products: break
        for product in products:
            handle=product.get('handle','')
            if not handle or handle in seen: continue
            seen.add(handle)
            variants=product.get('variants') or []
            for v in variants:
                key=sku_key(v.get('sku'))
                if not key: continue
                entry={'url':BASE+'/de/products/'+handle,'title':product.get('title',''),'sku':v.get('sku','')}
                result.setdefault(key,entry)
        if page<limit_pages: time.sleep(2)
    return result,diagnostics,len(seen)

def parse_eur(url):
    """Only accept explicit EUR price in structured data. Never interpret USD as EUR."""
    clean=canonical(url)
    session=requests.Session();session.headers.update(HEADERS)
    session.cookies.set('localization','DE',domain='.lumibricks.com')
    r=session.get(clean,params={'currency':'EUR'},timeout=30)
    if r.status_code==429: raise RuntimeError('HTTP 429: Lumibricks begrenzt Anfragen')
    r.raise_for_status()
    soup=BeautifulSoup(r.text,'html.parser')
    prices=[]
    for script in soup.select('script[type="application/ld+json"]'):
        try: data=json.loads(script.string or script.get_text())
        except (ValueError,TypeError): continue
        def visit(obj):
            if isinstance(obj,list):
                for x in obj: visit(x)
            elif isinstance(obj,dict):
                if obj.get('@type') in ('Offer','AggregateOffer') and str(obj.get('priceCurrency','')).upper()=='EUR':
                    try: prices.append(Decimal(str(obj.get('price'))))
                    except Exception: pass
                for v in obj.values():
                    if isinstance(v,(dict,list)): visit(v)
        visit(data)
    if not prices:
        raise ValueError('Kein ausdrücklich als EUR ausgewiesener Produktpreis gefunden; USD-Wert wird nicht gespeichert.')
    if len(set(prices))>1:
        raise ValueError('Mehrere unterschiedliche EUR-Angebotspreise gefunden; keine eindeutige Preiszuordnung.')
    price=prices[0]
    if not 0 < price < 100000: raise ValueError('Unplausibler EUR-Preis')
    text=soup.get_text(' ',strip=True).lower()
    availability='unavailable' if 'ausverkauft' in text or 'sold out' in text else 'unknown'
    return price,availability

def record(url,pid,product_url):
    clean=canonical(product_url)
    price,av=parse_eur(clean)
    mid=ensure_merchant(url)
    now=datetime.now(timezone.utc)
    with connect(url) as c,c.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT id FROM public.offers WHERE product_id=%s AND merchant_id=%s AND product_url=%s AND is_active=true',(int(pid),mid,clean))
        offer=cur.fetchone()
        if not offer: raise ValueError('Bitte Lumibricks-Link zuerst bestätigen')
        oid=offer['id']
        cur.execute('SELECT price,shipping_cost FROM public.current_prices WHERE offer_id=%s',(oid,))
        prev=cur.fetchone()
        cur.execute("""INSERT INTO public.current_prices(offer_id,price,shipping_cost,availability,fetch_status,last_success_at,last_attempt_at,error_message)
         VALUES(%s,%s,0,%s,'success',%s,%s,NULL)
         ON CONFLICT(offer_id) DO UPDATE SET price=excluded.price,shipping_cost=0,availability=excluded.availability,
         fetch_status='success',last_success_at=excluded.last_success_at,last_attempt_at=excluded.last_attempt_at,error_message=NULL""",(oid,price,av,now,now))
        if not prev or prev['price']!=price or prev['shipping_cost']!=0:
            cur.execute('INSERT INTO public.price_history(offer_id,price,shipping_cost,availability,recorded_at) VALUES(%s,%s,0,%s,%s)',(oid,price,av,now))
    return price


def is_valid_product_url(product_url):
    """Validate an exact Lumibricks product URL, without making network requests."""
    from urllib.parse import urlparse
    p=urlparse(str(product_url).strip())
    return (p.scheme=='https' and p.hostname in ('lumibricks.com','www.lumibricks.com')
            and '/products/' in p.path and bool(p.path.split('/products/',1)[1].strip('/')))

def set_link(url, pid, product_url):
    """Change or unlink an offer. Unlinking preserves current_prices and price_history."""
    clean=str(product_url or '').strip()
    if clean:
        if not is_valid_product_url(clean):
            raise ValueError('Ungültiger Lumibricks-Produktlink')
        return confirm(url,pid,clean)
    mid=ensure_merchant(url)
    with connect(url) as c, c.cursor(row_factory=dict_row) as cur:
        cur.execute('UPDATE public.offers SET is_active=false WHERE product_id=%s AND merchant_id=%s AND is_active=true',
                    (int(pid),mid))
    return ''
