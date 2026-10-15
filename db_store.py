"""PostgreSQL storage for LBrixx. Only server-side psycopg connection; no public API key."""
import psycopg
from psycopg.rows import dict_row
from datetime import datetime, timezone
from decimal import Decimal
import pandas as pd

FIELDS=['db_id','odoo_id','hidden','deleted','sku','ean','lbrixx_sku','description','lbrixx_price','brickmo_url','photo','brickmo_photo','brickmo_price','shipping','total','availability','last_checked','error','lootstore_url','lootstore_price','lootstore_shipping','lootstore_total','lootstore_availability','lootstore_checked','lootstore_error']

def connect(url):
    return psycopg.connect(url,connect_timeout=12,autocommit=False)

def number(v):
    try: return Decimal(str(v).replace(',','.').strip()) if str(v).strip() else None
    except (ValueError,ArithmeticError): return None

def date(v):
    try:
        return datetime.strptime(str(v).strip(),'%Y-%m-%d %H:%M UTC').replace(tzinfo=timezone.utc)
    except (ValueError,TypeError): return None

def load(url):
    with connect(url) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute('''SELECT p.id db_id, o.odoo_id, p.is_hidden hidden, p.is_deleted deleted,
          p.manufacturer_sku sku, p.ean, o.odoo_sku lbrixx_sku, p.name description,
          o.sale_price lbrixx_price, p.image_url photo,
          b.product_url brickmo_url, bp.price brickmo_price, bp.shipping_cost shipping,
          bp.availability brickmo_availability, bp.last_success_at brickmo_checked,
          bp.fetch_status brickmo_status, bp.error_message brickmo_error,
          l.product_url lootstore_url, lp.price lootstore_price, lp.shipping_cost lootstore_shipping,
          lp.availability lootstore_availability, lp.last_success_at lootstore_checked,
          lp.fetch_status lootstore_status, lp.error_message lootstore_error
          FROM public.products p
          LEFT JOIN public.odoo_products o ON o.product_id=p.id
          LEFT JOIN public.offers b ON b.product_id=p.id AND b.merchant_id=(SELECT id FROM public.merchants WHERE name='Brickmo')
          LEFT JOIN public.current_prices bp ON bp.offer_id=b.id
          LEFT JOIN public.offers l ON l.product_id=p.id AND l.merchant_id=(SELECT id FROM public.merchants WHERE name='Lootstore')
          LEFT JOIN public.current_prices lp ON lp.offer_id=l.id
          ORDER BY p.id''')
        records=cur.fetchall()
    rows=[]
    for r in records:
        def s(k): return '' if r.get(k) is None else str(r[k])
        def status(k): return {'available':'Lieferbar','unavailable':'Nicht lieferbar','unknown':'Unbestätigt'}.get(r.get(k),'Unbestätigt')
        def stamp(k):
            v=r.get(k)
            return v.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC') if v else ''
        def total(a,b):
            x,y=number(r.get(a)),number(r.get(b))
            return str(x+(y or 0)) if x is not None else ''
        rows.append(dict(db_id=s('db_id'),odoo_id=s('odoo_id'),hidden='1' if r['hidden'] else '',deleted='1' if r['deleted'] else '',sku=s('sku'),ean=s('ean'),lbrixx_sku=s('lbrixx_sku'),description=s('description'),lbrixx_price=s('lbrixx_price'),photo=s('photo'),brickmo_photo='',brickmo_url=s('brickmo_url'),brickmo_price=s('brickmo_price'),shipping=s('shipping'),total=total('brickmo_price','shipping'),availability=status('brickmo_availability'),last_checked=stamp('brickmo_checked'),error=s('brickmo_error') if r.get('brickmo_status')=='error' else '',lootstore_url=s('lootstore_url'),lootstore_price=s('lootstore_price'),lootstore_shipping=s('lootstore_shipping'),lootstore_total=total('lootstore_price','lootstore_shipping'),lootstore_availability=status('lootstore_availability'),lootstore_checked=stamp('lootstore_checked'),lootstore_error=s('lootstore_error') if r.get('lootstore_status')=='error' else ''))
    return pd.DataFrame(rows,columns=FIELDS).fillna('')

def save(url,df):
    """Upsert all current rows transactionally, preserving existing history.
    History is written only when a new successful timestamp or price arrives.
    """
    with connect(url) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT id,name FROM public.merchants')
        merchants={r['name']:r['id'] for r in cur.fetchall()}
        for _,r in df.iterrows():
            d=r.to_dict()
            val=lambda k: str(d.get(k,'') or '').strip()
            pid=int(val('db_id')) if val('db_id').isdigit() else None
            image=val('photo')
            # Persist small 128px thumbnails as data URIs in image_url.
            # This avoids a separate Supabase Storage configuration.
            params=(val('sku') or None,val('ean') or None,val('description') or 'Unbenannter Artikel',image or None,val('hidden') in ('1','True','true'),val('deleted') in ('1','True','true'))
            if pid:
                cur.execute('''UPDATE public.products SET manufacturer_sku=%s,ean=%s,name=%s,image_url=COALESCE(%s,image_url),is_hidden=%s,is_deleted=%s,updated_at=now() WHERE id=%s RETURNING id''',(*params,pid))
                found=cur.fetchone()
                if not found: raise ValueError(f'Produkt-ID {pid} existiert nicht mehr. Bitte App neu laden.')
            else:
                cur.execute('''INSERT INTO public.products(manufacturer_sku,ean,name,image_url,is_hidden,is_deleted) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id''',params)
                pid=cur.fetchone()['id']
            oid=val('odoo_id')
            if oid:
                cur.execute('''INSERT INTO public.odoo_products(product_id,odoo_id,odoo_sku,odoo_ean,sale_price,last_import_at)
                  VALUES (%s,%s,%s,%s,%s,now()) ON CONFLICT(product_id) DO UPDATE SET
                  odoo_id=excluded.odoo_id,odoo_sku=excluded.odoo_sku,odoo_ean=excluded.odoo_ean,
                  sale_price=excluded.sale_price,updated_at=now()''',
                  (pid,int(oid),val('lbrixx_sku') or None,val('ean') or None,number(val('lbrixx_price'))))
            for merchant,pre in [('Brickmo','brickmo'),('Lootstore','lootstore')]:
                mid=merchants[merchant]
                link=val('brickmo_url' if pre=='brickmo' else 'lootstore_url')
                if not link:
                    cur.execute('UPDATE public.offers SET is_active=false WHERE product_id=%s AND merchant_id=%s',(pid,mid))
                    continue
                cur.execute('''INSERT INTO public.offers(product_id,merchant_id,product_url,is_active)
                  VALUES(%s,%s,%s,true) ON CONFLICT(product_id,merchant_id) DO UPDATE SET
                  product_url=excluded.product_url,is_active=true RETURNING id''',(pid,mid,link))
                offer=cur.fetchone()['id']
                price=number(val('brickmo_price' if pre=='brickmo' else 'lootstore_price'))
                shipping=number(val('shipping' if pre=='brickmo' else 'lootstore_shipping'))
                availability=val('availability' if pre=='brickmo' else 'lootstore_availability')
                av={'Lieferbar':'available','Nicht lieferbar':'unavailable'}.get(availability,'unknown')
                stamp=date(val('last_checked' if pre=='brickmo' else 'lootstore_checked'))
                error=val('error' if pre=='brickmo' else 'lootstore_error')
                cur.execute('SELECT price,last_success_at FROM public.current_prices WHERE offer_id=%s',(offer,))
                previous=cur.fetchone()
                changed=bool(stamp and price is not None and (not previous or previous['last_success_at']!=stamp or previous['price']!=price))
                # Preserve successful price/timestamp on errors.
                if error:
                    cur.execute('''INSERT INTO public.current_prices(offer_id,fetch_status,error_message,last_attempt_at)
                       VALUES(%s,'error',%s,now()) ON CONFLICT(offer_id) DO UPDATE SET
                       fetch_status='error',error_message=excluded.error_message,last_attempt_at=now()''',(offer,error[:2000]))
                else:
                    cur.execute('''INSERT INTO public.current_prices(offer_id,price,shipping_cost,availability,fetch_status,last_success_at,last_attempt_at,error_message)
                      VALUES(%s,%s,%s,%s,%s,%s,now(),NULL) ON CONFLICT(offer_id) DO UPDATE SET
                      price=COALESCE(excluded.price,public.current_prices.price),
                      shipping_cost=COALESCE(excluded.shipping_cost,public.current_prices.shipping_cost),
                      availability=excluded.availability,fetch_status=excluded.fetch_status,
                      last_success_at=COALESCE(excluded.last_success_at,public.current_prices.last_success_at),
                      last_attempt_at=now(),error_message=NULL''',
                      (offer,price,shipping,av,'success' if stamp else 'pending',stamp))
                    if changed:
                        cur.execute('INSERT INTO public.price_history(offer_id,price,shipping_cost,availability,recorded_at) VALUES(%s,%s,%s,%s,%s)',(offer,price,shipping,av,stamp))
    return load(url)

def history(url,pid):
    with connect(url) as conn,conn.cursor(row_factory=dict_row) as cur:
        cur.execute('''SELECT m.name merchant,h.price,h.shipping_cost,h.recorded_at FROM public.price_history h
          JOIN public.offers o ON o.id=h.offer_id JOIN public.merchants m ON m.id=o.merchant_id
          WHERE o.product_id=%s ORDER BY h.recorded_at''',(int(pid),))
        return pd.DataFrame(cur.fetchall())
