"""Lumibricks offer storage and cautious EUR price extraction."""
import re, json, time
from urllib.parse import urlparse, quote, parse_qs
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
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

def _jsonld_eur_prices(html):
    """Return explicitly EUR-denominated structured offer prices."""
    soup=BeautifulSoup(html,'html.parser')
    prices=[]
    for script in soup.select('script[type="application/ld+json"]'):
        try: data=json.loads(script.string or script.get_text())
        except (ValueError,TypeError): continue
        def visit(obj):
            if isinstance(obj,list):
                for x in obj: visit(x)
            elif isinstance(obj,dict):
                typ=obj.get('@type',[])
                if isinstance(typ,str): typ=[typ]
                if 'Offer' in typ and str(obj.get('priceCurrency','')).upper()=='EUR':
                    try: prices.append(Decimal(str(obj.get('price'))))
                    except (ValueError,TypeError,ArithmeticError): pass
                for v in obj.values():
                    if isinstance(v,(dict,list)): visit(v)
        visit(data)
    return prices


def _selected_variant_eur(product):
    """Choose German delivery variant, never the first arbitrary regional variant."""
    variants=product.get('variants') or []
    german=[v for v in variants if str(v.get('sku','')).upper().strip().endswith('#DE')]
    if len(german)==1:
        chosen=german[0]
    elif len(german)>1:
        raise ValueError('Mehrere DE-Varianten vorhanden; keine eindeutige Preiszuordnung')
    elif len(variants)==1:
        chosen=variants[0]
    else:
        raise ValueError('Keine eindeutige DE-Variante im Shopify-Produkt gefunden')
    raw=chosen.get('price')
    if raw is None or isinstance(raw,bool):
        raise ValueError('Kein Variantenpreis im Shopify-Produkt vorhanden')
    # Shopify Ajax monetary values are integer minor currency units (EUR cents).
    if not str(raw).isdigit():
        raise ValueError('Unerwartetes Format des Shopify-Variantenpreises')
    price=Decimal(str(raw))/Decimal('100')
    if not Decimal('0')<price<Decimal('100000'):
        raise ValueError('Unplausibler EUR-Variantenpreis')
    return price, 'unavailable' if chosen.get('available') is False else 'unknown'


def _shopify_currency(session):
    """Read the server-confirmed presentment currency, never infer from cookies."""
    response=session.get(BASE+'/de/cart.js',timeout=25,
                         headers={'Accept':'application/json','Cache-Control':'no-cache'})
    if response.status_code==429:
        raise RuntimeError('HTTP 429: Lumibricks begrenzt Anfragen')
    if not response.ok:
        return f'HTTP {response.status_code}'
    try:
        return str(response.json().get('currency') or '').upper() or 'UNBEKANNT'
    except (TypeError,ValueError,AttributeError):
        return 'UNBEKANNT'


def _currency_session(approach,clean):
    """Try ordinary storefront currency selectors; success must be verified."""
    session=requests.Session()
    session.headers.update(HEADERS)
    referer={'Referer':clean,'Origin':BASE}
    if approach=='localization':
        response=session.get(BASE+'/de',timeout=20)
        if response.status_code==429:
            raise RuntimeError('HTTP 429: Lumibricks begrenzt Anfragen')
        response=session.post(BASE+'/localization',data={
            'form_type':'localization', '_method':'put',
            'country_code':'DE', 'language_code':'de',
            'currency_code':'EUR', 'return_to':urlparse(clean).path,
        },headers=referer,timeout=20)
    elif approach=='currency_query':
        response=session.get(clean,params={'currency':'EUR'},timeout=25)
    elif approach=='currency_form':
        response=session.get(BASE+'/de',timeout=20)
        if response.status_code==429:
            raise RuntimeError('HTTP 429: Lumibricks begrenzt Anfragen')
        response=session.post(BASE+'/cart/update.js',data={'currency':'EUR'},
                              headers=referer,timeout=20)
    elif approach=='currency_cookie':
        # A cookie alone is not evidence of EUR. The cart endpoint must still
        # independently identify EUR before any price may be accepted.
        session.cookies.set('cart_currency','EUR',domain='www.lumibricks.com',path='/')
        response=session.get(clean,timeout=25)
    else:
        raise ValueError('Unbekannte Währungsstrategie')
    if response.status_code==429:
        raise RuntimeError('HTTP 429: Lumibricks begrenzt Anfragen')
    return session, response.status_code


def _browser_eur_price(clean):
    """Read the post-JavaScript, visibly EUR-denominated product price.

    Requires Playwright plus a Chromium executable in the deployment environment.
    Does not convert USD or use Shopify's initial USD product JSON price.
    """
    import os, shutil
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    except ImportError as exc:
        raise ValueError('Browser-Fallback nicht installiert: Paket playwright fehlt. '
                         'Bitte requirements.txt ergänzen.') from exc

    executable = (os.environ.get('LBRIXX_CHROMIUM_PATH') or
                  next((shutil.which(x) for x in ('chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable')
                        if shutil.which(x)), None))
    with sync_playwright() as playwright:
        launch_args={'headless':True, 'args':['--no-sandbox', '--disable-dev-shm-usage']}
        if executable:
            launch_args['executable_path']=executable
        try:
            browser=playwright.chromium.launch(**launch_args)
        except Exception as exc:
            raise ValueError('Chromium nicht verfügbar. Chromium in Streamlit installieren '
                             '(packages.txt) oder LBRIXX_CHROMIUM_PATH setzen: '
                             +str(exc)[:180]) from exc
        try:
            context=browser.new_context(locale='de-DE',timezone_id='Europe/Berlin',
                                        extra_http_headers={'Accept-Language':'de-DE,de;q=0.9'},
                                        viewport={'width':1365,'height':900})
            page=context.new_page()
            try:
                page.goto(clean,wait_until='domcontentloaded',timeout=35000)
                page.wait_for_timeout(3000)
                # Check whether browser automatically selected EUR. Otherwise
                # click a visible currency selector (no assumption about Shopify endpoints).
                def state():
                    # Scope to the principal PDP, not product cards, recommendations,
                    # hidden widgets, compare-at (struck-through) or unit prices.
                    return page.evaluate(r"""() => {
                      const visible = e => !!e.getClientRects().length &&
                        getComputedStyle(e).visibility !== 'hidden' &&
                        getComputedStyle(e).display !== 'none';
                      const money = /(?:€\s*\d[\d.,\s]*|\d[\d.,\s]*\s*(?:€|EUR)|EUR\s*\d[\d.,\s]*)/i;
                      const clean = e => (e.innerText || e.textContent || '').replace(/\s+/g,' ').trim();
                      const roots = [
                        '[data-section-type="product"]',
                        'main product-info', 'main .product__info-wrapper',
                        'main .product__info-container', 'main .product-info',
                        'main .product-single__meta', 'main [data-product-id]',
                        'main .product-detail', 'main .product-form',
                        'main [class*="product-info"]', 'main [class*="product__info"]'
                      ];
                      let root = null;
                      for (const sel of roots) {
                        const found = Array.from(document.querySelectorAll(sel)).find(visible);
                        if (found) { root=found; break; }
                      }
                      if (!root) root = document.querySelector('main') || document.body;
                      const isExcluded = el => !!el.closest(
                        's, del, [class*="compare"], [class*="old-price"], [class*="regular-price"], '+
                        '[class*="recommend"], [class*="related"], [class*="recently"], '+
                        '[class*="upsell"], [class*="cross-sell"], [class*="unit-price"], '+
                        '[aria-hidden="true"]');
                      const tiers = [
                        '[data-product-price]', '[data-price-current]', '[data-current-price]',
                        '[itemprop="price"]', '.price__sale .price-item--sale',
                        '.price-item--sale', '.product__price', '.product-single__price',
                        '.product-price', '.product-price__price', '.price__current',
                        '.current-price', '[class*="sale-price"]',
                        '[class*="price-item--regular"]', '.price-item--regular', '.price'
                      ];
                      let candidates=[];
                      for (let tier=0;tier<tiers.length;tier++) {
                        for (const e of root.querySelectorAll(tiers[tier])) {
                          if (!visible(e) || isExcluded(e)) continue;
                          const inner = Array.from(e.querySelectorAll(tiers.join(',')))
                            .some(child => child !== e && visible(child) && !isExcluded(child) && money.test(clean(child)));
                          if (inner) continue; // prevent counting a price and its wrapper twice
                          const t=clean(e);
                          if (t && t.length<110 && money.test(t))
                            candidates.push({tier,selector:tiers[tier],text:t.slice(0,105),
                              html:e.outerHTML.slice(0,280)});
                        }
                      }
                      const unique = [];
                      for(const c of candidates)
                        if(!unique.some(x=>x.tier===c.tier && x.text===c.text)) unique.push(c);
                      return {prices:unique.map(x=>x.text).slice(0,20),
                        candidates:unique.slice(0,25),root:root.tagName+'.'+root.className.toString().slice(0,70),
                        title:document.title};
                    }""")
                initial=state()
                if not any('€' in x or 'EUR' in x for x in initial['prices']):
                    # Interact ONLY with visible controls bearing currency markers;
                    # fallback to an option after opening an explicit currency selector.
                    for selector in ('button:has-text("USD")', '[role="button"]:has-text("USD")',
                                     'summary:has-text("USD")', 'button:has-text("Currency")',
                                     'button:has-text("Währung")', 'select[name*="currency"]'):
                        candidates=page.locator(selector)
                        try:
                            if candidates.count() and candidates.first.is_visible():
                                if selector.startswith('select'):
                                    candidates.first.select_option('EUR',timeout=2000)
                                else:
                                    candidates.first.click(timeout=2500)
                                page.wait_for_timeout(650)
                                for eur in ('[role="option"]:has-text("EUR")',
                                            'button:has-text("EUR")',
                                            'a:has-text("EUR")',
                                            'li:has-text("EUR")',
                                            'label:has-text("EUR")'):
                                    item=page.locator(eur)
                                    if item.count() and item.first.is_visible():
                                        item.first.click(timeout=2500)
                                        break
                                break
                        except Exception:
                            continue
                # The original USD price may flash while JS runs. Check twice,
                # with a delay, and accept only an unambiguous current EUR amount.
                page.wait_for_timeout(2800)
                first=state()
                page.wait_for_timeout(1500)
                second=state()
            except PlaywrightTimeout as exc:
                raise ValueError('Browser-Abfrage abgelaufen (Lumibricks-Seite lädt zu langsam)') from exc
            finally:
                context.close()
        finally:
            browser.close()

    def amounts(text):
        out=[]
        for left,right in re.findall(r'(?:(?:€\s*|EUR\s*)([0-9][0-9.,\s]*))|(?:([0-9][0-9.,\s]*)\s*(?:€|EUR))',text,re.I):
            raw=(left or right).strip().replace(' ','').replace('\xa0','')
            if not raw: continue
            if ',' in raw:
                raw=raw.replace('.','').replace(',','.')
            elif raw.count('.')>1:
                raw=raw.replace('.','')
            try:
                amount=Decimal(raw)
                if Decimal('0')<amount<Decimal('100000'):
                    out.append(amount)
            except Exception:
                continue
        return set(out)

    def choose(details):
        # A unique price at the highest-confidence DOM selector tier wins.
        # Do not fall back to unrelated monetary amounts elsewhere on the page.
        candidates=details.get('candidates',[])
        tiers=sorted({c['tier'] for c in candidates})
        for tier in tiers:
            relevant=[c for c in candidates if c['tier']==tier]
            matches=set()
            for candidate in relevant:
                matches.update(amounts(candidate['text']))
            if len(matches)==1:
                return next(iter(matches)),tier,relevant
            if len(matches)>1:
                return None,tier,relevant
        return None,None,[]

    first_amount,first_tier,_=choose(first)
    second_amount,second_tier,selected=choose(second)
    if (first_amount is not None and first_amount==second_amount and
            first_tier==second_tier):
        return second_amount,'unknown'
    raise ValueError('Browser: Hauptproduktpreis nicht eindeutig oder nicht stabil '
                     f'(Bereich: {second.get("root")}; '
                     f'bevorzugte Preisfelder: {[(x["selector"],x["text"]) for x in selected][:5]!r}; '
                     f'Kandidaten: {[(x["selector"],x["text"]) for x in second.get("candidates",[])][:8]!r}). '
                     'Kein Preis gespeichert.')


def parse_eur(url):
    """Fetch verified German EUR pricing without ever relabeling USD.

    A German URL and a displayed frontend currency selector are insufficient
    evidence that Shopify's server-side product endpoint is returning EUR.
    Each currency-switch method is validated against Shopify cart.js.
    """
    clean=canonical(url)
    slug=urlparse(clean).path.rstrip('/').split('/')[-1]
    diagnostics=[]
    for approach in ('localization','currency_query','currency_form','currency_cookie'):
        try:
            session,status=_currency_session(approach,clean)
            currency=_shopify_currency(session)
            diagnostics.append(f'{approach}: HTTP {status}, Warenkorb {currency}')
            if currency!='EUR':
                continue
            details=session.get(BASE+'/de/products/'+quote(slug,safe='')+'.js',timeout=25,
                                headers={'Accept':'application/json','Cache-Control':'no-cache'})
            if details.status_code==429:
                raise RuntimeError('HTTP 429: Lumibricks begrenzt Anfragen')
            details.raise_for_status()
            try: product=details.json()
            except (ValueError,TypeError):
                raise ValueError('Shopify-Produktdaten sind kein gültiges JSON')
            return _selected_variant_eur(product)
        except requests.RequestException as exc:
            diagnostics.append(f'{approach}: Netzwerkfehler ({type(exc).__name__})')
        except RuntimeError:
            raise

    # A genuinely EUR-denominated structured offer is a safe fallback.
    session=requests.Session()
    session.headers.update(HEADERS)
    page=session.get(clean,timeout=30)
    if page.status_code==429:
        raise RuntimeError('HTTP 429: Lumibricks begrenzt Anfragen')
    page.raise_for_status()
    prices=_jsonld_eur_prices(page.text)
    if len(set(prices))==1:
        price=prices[0]
        if not Decimal('0')<price<Decimal('100000'):
            raise ValueError('Unplausibler EUR-Preis')
        return price,'unknown'
    if len(set(prices))>1:
        raise ValueError('Mehrere EUR-Angebote – kein eindeutiger Preis')
    currencies=sorted(set(re.findall(r'"priceCurrency"\s*:\s*"([A-Z]{3})"',page.text)))
    # The storefront switches USD -> EUR after page load using JavaScript.
    # This browser step is deliberately last so fast, verified EUR paths win.
    try:
        return _browser_eur_price(clean)
    except Exception as exc:
        raise ValueError('Kein verifizierter EUR-Preis. Währungsprüfung: '
                         + '; '.join(diagnostics)
                         + '; HTML: ' + (', '.join(currencies) or 'unbekannt')
                         + '; Browser: ' + str(exc)[:1100]
                         + '. USD wurde nicht übernommen.') from exc

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


def search_exact_sku(sku, session=None):
    """Targeted Shopify search, verifying the SKU on the product JSON.

    Returns (unique matching product or None, diagnostic). Never guesses from titles.
    HTTP 429 propagates to allow caller to stop immediately.
    """
    key=sku_key(sku)
    if not key: return None, 'Leere SKU'
    client=session or requests.Session()
    client.headers.update(HEADERS)
    response=client.get(BASE+'/search', params={'q':str(sku).strip(), 'type':'product'}, timeout=25)
    if response.status_code==429: raise RuntimeError('HTTP 429: Lumibricks-Suche begrenzt; bitte später erneut versuchen')
    response.raise_for_status()
    soup=BeautifulSoup(response.text,'html.parser')
    handles=[]
    for link in soup.select('a[href*="/products/"]'):
        path=urlparse(link.get('href','')).path
        if '/products/' not in path: continue
        handle=path.split('/products/',1)[1].split('/')[0]
        if handle and handle not in handles: handles.append(handle)
    # Known reference product for the regression test 15002 / F9031.
    # It is NOT automatically linked: the Shopify variant SKU must verify.
    if key == 'F9031' and 'book-cafe-1718pcs' not in handles:
        handles.insert(0, 'book-cafe-1718pcs')
    found={}
    for handle in handles[:12]:
        detail=client.get(BASE+'/products/'+quote(handle,safe='')+'.js',timeout=25)
        if detail.status_code==429: raise RuntimeError('HTTP 429: Lumibricks-Suche begrenzt; bitte später erneut versuchen')
        if detail.status_code!=200: continue
        try: product=detail.json()
        except ValueError: continue
        for variant in product.get('variants',[]):
            if sku_key(variant.get('sku'))==key:
                url=BASE+'/de/products/'+handle
                found[url]={'url':url,'title':product.get('title',''),'sku':variant.get('sku','')}
    if len(found)==1: return next(iter(found.values())), 'Eindeutige SKU bestätigt'
    if len(found)>1: return None, 'Mehrere Produkte mit gleicher SKU – keine automatische Zuordnung'
    return None, f'Keine bestätigte SKU; {len(handles[:12])} Produktseiten geprüft (Shop-Suche: {response.status_code})'


# V28.11.57: Preis-Vorschau pro konkreter Shopify-Variante; keine Persistierung.
PREVIEW_USD_TO_EUR = Decimal('0.892')


def _preview_variant(variants, product_url):
    """Select a demonstrably German variant, respecting an explicit URL ID.

    Never silently choose the first/default/non-DE variant when several exist.
    Returns (variant, selection_evidence, query_variant_id).
    """
    params = parse_qs(urlparse(product_url).query)
    ids = [str(value).strip() for value in params.get('variant', []) if str(value).strip()]
    if len(set(ids)) > 1:
        raise ValueError('Mehrere unterschiedliche Varianten-IDs in der URL')
    requested_id = ids[0] if ids else ''
    explicitly_selected = None
    if requested_id:
        matches = [v for v in variants if str(v.get('id') or '') == requested_id]
        if len(matches) != 1:
            raise ValueError(f'URL-Variante {requested_id} nicht im Shopify-Produkt gefunden')
        explicitly_selected = matches[0]

    german = [v for v in variants if str(v.get('sku') or '').strip().upper().endswith('#DE')]
    if len(german) > 1:
        if explicitly_selected is not None and any(
            str(v.get('id')) == str(explicitly_selected.get('id')) for v in german
        ):
            return explicitly_selected, 'URL-Variante ist eine bestätigte DE-Variante', requested_id
        raise ValueError('Mehrere DE-Varianten: Bitte den vollständigen Produktlink mit ?variant=ID eingeben')
    if len(german) == 1:
        selected = german[0]
        if explicitly_selected is not None and str(explicitly_selected.get('id')) != str(selected.get('id')):
            # Do not quietly misrepresent the requested variant; show a useful warning.
            raise ValueError('Die URL-Variante ist nicht die einzige #DE-Variante: '
                             f'URL-ID {requested_id}, DE-ID {selected.get("id")}. '
                             'Bitte die Auswahl im Shop prüfen.')
        return selected, ('URL-ID und #DE-SKU stimmen überein' if requested_id
                          else 'Eindeutige #DE-SKU im Shopify-Produkt'), requested_id
    if len(variants) == 1:
        selected = variants[0]
        if requested_id and str(selected.get('id')) != requested_id:
            raise ValueError('URL-Variante stimmt nicht mit der einzigen Produktvariante überein')
        return selected, 'Nur eine Shopify-Variante; DE-Lieferregion NICHT verifiziert', requested_id
    if explicitly_selected is not None:
        raise ValueError(f'URL-Variante {requested_id} gefunden, aber nicht als #DE bestätigt. '
                         'DE-Versandvariante kann nicht sicher bestimmt werden.')
    raise ValueError('Keine eindeutige #DE-Variante gefunden. '
                     'Bitte URL mit ?variant=ID angeben; ohne DE-Nachweis kein Preis.')


def preview_usd_to_eur(product_url, factor=PREVIEW_USD_TO_EUR):
    """Preview a specifically selected Shopify USD variant; never save estimates."""
    clean = canonical(product_url)
    slug = urlparse(clean).path.rstrip('/').split('/')[-1]
    session = requests.Session()
    session.headers.update(HEADERS)
    currency = _shopify_currency(session)
    if currency != 'USD':
        raise ValueError(f'USD-Test abgebrochen: Shopify-Warenkorb meldet {currency}, nicht USD')
    response = session.get(BASE + '/de/products/' + quote(slug, safe='') + '.js',
                           timeout=25, headers={'Accept': 'application/json',
                                                'Cache-Control': 'no-cache'})
    if response.status_code == 429:
        raise RuntimeError('HTTP 429: Lumibricks begrenzt Anfragen')
    response.raise_for_status()
    try:
        product = response.json()
    except (ValueError, TypeError) as exc:
        raise ValueError('Produkt-JSON ungültig') from exc
    variants = product.get('variants') or []
    if not variants:
        raise ValueError('Shopify-Produkt enthält keine Varianten')
    chosen, reason, requested_id = _preview_variant(variants, product_url)
    raw = chosen.get('price')
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise ValueError('Shopify-Variantenpreis fehlt oder hat kein erwartetes Cent-Format')
    usd = Decimal(raw) / Decimal(100)
    if not Decimal('0') < usd < Decimal('100000'):
        raise ValueError('Unplausibler USD-Preis')
    factor = Decimal(str(factor))
    if not Decimal('0') < factor < Decimal('2'):
        raise ValueError('Ungültiger Umrechnungsfaktor')
    eur_estimate = (usd * factor).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return {
        'usd': usd, 'eur_estimate': eur_estimate, 'factor': factor,
        'sku': str(chosen.get('sku') or ''), 'variant_id': str(chosen.get('id') or ''),
        'requested_variant_id': requested_id, 'selection_reason': reason,
        'variant_count': len(variants), 'url': clean, 'currency': currency,
        'status': 'NICHT VERIFIZIERTE SCHÄTZUNG',
    }


# V28.11.58: Read-only comparison of two Shopify product JSON routes.
def diagnose_variant_price_sources(product_url):
    """Compare same variant IDs across routes; no price persistence or conversion."""
    import hashlib
    clean = canonical(product_url)
    slug = urlparse(clean).path.rstrip('/').split('/')[-1]
    session = requests.Session()
    session.headers.update(HEADERS)
    currency = _shopify_currency(session)
    paths = [f'/de/products/{quote(slug, safe="")}.js',
             f'/products/{quote(slug, safe="")}.js']
    reports = []
    for path in paths:
        source_url = BASE + path
        response = session.get(source_url, timeout=25, headers={
            'Accept': 'application/json', 'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'})
        if response.status_code == 429:
            raise RuntimeError('HTTP 429: Lumibricks begrenzt Anfragen – Diagnose gestoppt')
        report = {'source': source_url, 'http': response.status_code,
                  'response_url': response.url,
                  'content_currency': currency,
                  'date': response.headers.get('Date',''),
                  'cache': response.headers.get('X-Cache','') or response.headers.get('CF-Cache-Status',''),
                  'etag': response.headers.get('ETag',''),
                  'sha256': hashlib.sha256(response.content).hexdigest()[:16],
                  'variants': [], 'error': ''}
        if not response.ok:
            report['error'] = f'HTTP {response.status_code}'
            reports.append(report)
            continue
        try:
            product = response.json()
            if not isinstance(product,dict):
                raise ValueError('Antwort ist kein Produktobjekt')
            for item in product.get('variants',[]):
                raw = item.get('price')
                if isinstance(raw,bool) or not isinstance(raw,int) or raw < 0:
                    price = 'unbekannt'
                else:
                    price = str((Decimal(raw)/100).quantize(Decimal('0.01')))
                report['variants'].append({
                    'source': path, 'variant_id': str(item.get('id','')),
                    'sku':str(item.get('sku','')),
                    'title': str(item.get('title','')),
                    'available':item.get('available'),
                    'raw_cents':raw, 'amount':price,
                    'shopify_cart_currency':currency,
                })
            report['product_id'] = str(product.get('id',''))
            report['product_price_cents'] = product.get('price')
        except (ValueError,TypeError,AttributeError) as exc:
            report['error'] = f'Produkt-JSON nicht interpretierbar: {exc}'
        reports.append(report)
        time.sleep(1)
    return reports

# V28.11.60: Read-only Shopify market/session diagnosis. No database writes.
def diagnose_market_sessions(product_url):
    """Compare product JSON in HTTP and fresh browser market contexts.

    Only names of cookies are reported, never cookie contents.
    Prices reflect the currency explicitly returned by /cart.js.
    """
    import os, shutil
    from datetime import datetime
    from zoneinfo import ZoneInfo
    clean = canonical(product_url)
    slug = urlparse(clean).path.rstrip('/').split('/')[-1]
    path = '/de/products/' + quote(slug, safe='') + '.js'
    variant_id = (parse_qs(urlparse(product_url).query).get('variant') or [''])[0]
    results = []

    def summarize(label, cart_status, cart_data, product_status, product_data, cookies, error=''):
        currency = str((cart_data or {}).get('currency') or 'nicht gemeldet').upper()
        country = str((cart_data or {}).get('buyer_identity', {}).get('country_code') or '') if isinstance((cart_data or {}).get('buyer_identity'), dict) else ''
        variants = (product_data or {}).get('variants') or []
        german = [v for v in variants if str(v.get('sku') or '').upper().endswith('#DE')]
        selected = next((v for v in variants if str(v.get('id')) == variant_id), None) if variant_id else None
        if selected is None and len(german) == 1:
            selected = german[0]
        raw = selected.get('price') if selected else None
        amount = str(Decimal(raw)/100) if isinstance(raw, int) and not isinstance(raw,bool) else '–'
        results.append({'Sitzung':label, 'Warenkorb HTTP':cart_status,
                        'Warenkorb-Währung':currency, 'Land (falls gemeldet)':country or '–',
                        'Produkt HTTP':product_status, 'Varianten-ID':str(selected.get('id','')) if selected else '–',
                        'SKU':str(selected.get('sku','')) if selected else '–',
                        'Produktpreis (Währung laut Warenkorb)':amount,
                        'Cookie-Namen':', '.join(sorted(set(cookies))) or 'keine',
                        'Fehler':error})

    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        cart = session.get(BASE+'/de/cart.js', timeout=25, headers={'Cache-Control':'no-cache'})
        if cart.status_code == 429: raise RuntimeError('HTTP 429 bei HTTP-Warenkorb')
        cdata = cart.json() if cart.ok else {}
        product = session.get(BASE+path, timeout=25, headers={'Cache-Control':'no-cache'})
        if product.status_code == 429: raise RuntimeError('HTTP 429 bei HTTP-Produkt')
        pdata = product.json() if product.ok else {}
        summarize('HTTP: frische Sitzung', cart.status_code, cdata, product.status_code,
                  pdata, [cookie.name for cookie in session.cookies])
    except Exception as exc:
        summarize('HTTP: frische Sitzung', '–', {}, '–', {}, [], str(exc)[:220])
        if '429' in str(exc): return results

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        summarize('Browser: Playwright', '–', {}, '–', {}, [], 'Playwright nicht installiert')
        return results
    executable = (os.environ.get('LBRIXX_CHROMIUM_PATH') or
                  next((shutil.which(x) for x in ('chromium','chromium-browser','google-chrome')
                        if shutil.which(x)), None))
    try:
        with sync_playwright() as pw:
            launch = {'headless':True, 'args':['--no-sandbox','--disable-dev-shm-usage']}
            if executable: launch['executable_path'] = executable
            browser = pw.chromium.launch(**launch)
            try:
                for label, use_de in [('Browser: de-DE (frisch)',False),
                                      ('Browser: Land DE gewählt',True)]:
                    context = browser.new_context(locale='de-DE',timezone_id='Europe/Berlin',
                                                  extra_http_headers={'Accept-Language':'de-DE,de;q=0.9'})
                    try:
                        page = context.new_page()
                        page.goto(clean, wait_until='domcontentloaded', timeout=35000)
                        if use_de:
                            # This attempts an ordinary Shopify storefront localization form,
                            # then verifies what the store actually returned. No cart changes.
                            page.evaluate('''async () => {
                                const form = new FormData();
                                form.append('form_type','localization');
                                form.append('_method','put');
                                form.append('country_code','DE');
                                form.append('language_code','de');
                                await fetch('/localization', {method:'POST',body:form,
                                  credentials:'include',redirect:'follow'});
                            }''')
                        page.wait_for_timeout(1200)
                        data = page.evaluate('''async (productPath) => {
                           const opt = {credentials:'include',cache:'no-store'};
                           async function get(path) {
                             try {const r=await fetch(path,opt);
                               return {status:r.status,data:r.ok?await r.json():{}};
                             } catch(e) {return {status:'Fehler',data:{},error:String(e)};}
                           }
                           return {cart:await get('/de/cart.js'),product:await get(productPath)};
                        }''', path)
                        cookies = [c['name'] for c in context.cookies(BASE)]
                        summarize(label, data['cart']['status'], data['cart']['data'],
                                  data['product']['status'], data['product']['data'], cookies,
                                  '; '.join(x.get('error','') for x in (data['cart'],data['product']) if x.get('error')))
                        if data['cart']['status'] == 429 or data['product']['status'] == 429:
                            break
                    except Exception as exc:
                        summarize(label, '–', {}, '–', {}, [], str(exc)[:220])
                    finally:
                        context.close()
            finally:
                browser.close()
    except Exception as exc:
        summarize('Browser: Initialisierung', '–', {}, '–', {}, [], str(exc)[:220])
    return results


# V28.11.61: browser session-based market price preview (read-only).
def preview_de_market_usd_to_eur(product_url, factor=PREVIEW_USD_TO_EUR):
    import os, shutil
    from playwright.sync_api import sync_playwright
    clean=canonical(product_url)
    slug=urlparse(clean).path.rstrip('/').split('/')[-1]
    path='/de/products/'+quote(slug,safe='')+'.js'
    factor=Decimal(str(factor))
    if not Decimal('0') < factor < Decimal('2'):
        raise ValueError('Ungültiger Faktor')
    executable=os.environ.get('LBRIXX_CHROMIUM_PATH') or next((shutil.which(x) for x in ('chromium','chromium-browser','google-chrome') if shutil.which(x)),None)
    js_fetch="""async (path) => {
      const get=async p=>{let r=await fetch(p,{credentials:'include',cache:'no-store'});
        return {status:r.status,data:r.ok?await r.json():{}}};
      return {cart:await get('/de/cart.js'),product:await get(path)};
    }"""
    js_localize="""async () => {
      let f=new FormData();f.append('form_type','localization');f.append('_method','put');
      f.append('country_code','DE');f.append('language_code','de');
      let r=await fetch('/localization',{method:'POST',body:f,credentials:'include',redirect:'follow'});
      return r.status;
    }"""
    with sync_playwright() as pw:
        args={'headless':True,'args':['--no-sandbox','--disable-dev-shm-usage']}
        if executable: args['executable_path']=executable
        browser=pw.chromium.launch(**args)
        try:
            context=browser.new_context(locale='de-DE',timezone_id='Europe/Berlin',extra_http_headers={'Accept-Language':'de-DE,de;q=0.9'})
            try:
                page=context.new_page()
                page.goto(clean,wait_until='domcontentloaded',timeout=35000)
                before=page.evaluate(js_fetch,path)
                status=page.evaluate(js_localize)
                if status==429: raise RuntimeError('HTTP 429 bei Marktwechsel')
                if status<200 or status>=400: raise RuntimeError(f'Marktwechsel HTTP {status}')
                page.wait_for_timeout(1200)
                after=page.evaluate(js_fetch,path)
                for stage,data in [('vorher',before),('nachher',after)]:
                    for kind in ('cart','product'):
                        code=data[kind]['status']
                        if code!=200: raise RuntimeError(f'{stage} {kind}: HTTP {code}')
                currency=str(after['cart']['data'].get('currency') or '').upper()
                if currency!='USD': raise ValueError('Shopify-Währung nicht USD: '+currency)
                variants=after['product']['data'].get('variants') or []
                selected,reason,requested=_preview_variant(variants,product_url)
                cents=selected.get('price')
                if isinstance(cents,bool) or not isinstance(cents,int) or cents<=0: raise ValueError('Ungültiger Variantenpreis')
                usd=Decimal(cents)/100
                eur=(usd*factor).quantize(Decimal('1'),rounding=ROUND_HALF_UP)
                vid=str(selected.get('id') or '')
                old=next((x for x in before['product']['data'].get('variants',[]) if str(x.get('id'))==vid),None)
                oc=old.get('price') if old else None
                before_usd=Decimal(oc)/100 if isinstance(oc,int) and not isinstance(oc,bool) else None
                return dict(usd=usd,eur_estimate=eur,factor=factor,sku=str(selected.get('sku') or ''),variant_id=vid,
                            selection_reason=reason,variant_count=len(variants),before_usd=before_usd,
                            changed=before_usd is not None and before_usd!=usd,currency=currency,
                            localization_http=status,url=clean,status='TEST / NICHT GESPEICHERT')
            finally: context.close()
        finally: browser.close()


# V28.11.62: Persistente Preisvorschau und transaktionale Sammelfreigabe.
# USD→EUR wird ausschließlich als berechneter Wert gespeichert, niemals als direkter EUR-Shoppreis bezeichnet.
import uuid as _uuid

def ensure_bulk_preview_table(db_url):
    with connect(db_url) as c, c.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS public.lumibricks_bulk_previews (
            id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            state text NOT NULL CHECK(state IN ('running','ready','approved','interrupted')),
            factor numeric(12,6) NOT NULL,
            entries jsonb NOT NULL DEFAULT '[]'::jsonb,
            approved_at timestamptz
        )""")

def new_bulk_preview(db_url,factor):
    ensure_bulk_preview_table(db_url)
    bid=str(_uuid.uuid4())
    with connect(db_url) as c,c.cursor() as cur:
        cur.execute("INSERT INTO public.lumibricks_bulk_previews(id,state,factor) VALUES(%s,'running',%s)",(bid,str(factor)))
    return bid

def save_bulk_preview(db_url,bid,entries,state='running'):
    if state not in ('running','ready','interrupted'): raise ValueError('Ungültiger Vorschauzustand')
    with connect(db_url) as c,c.cursor() as cur:
        cur.execute("UPDATE public.lumibricks_bulk_previews SET entries=%s::jsonb,state=%s,updated_at=now() WHERE id=%s AND state IN ('running','ready','interrupted')",
                    (json.dumps(entries,ensure_ascii=False),state,bid))
        if cur.rowcount!=1: raise ValueError('Vorschau ist bereits freigegeben oder nicht mehr vorhanden')

def latest_bulk_preview(db_url):
    ensure_bulk_preview_table(db_url)
    with connect(db_url) as c,c.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id,created_at,updated_at,state,factor,entries,approved_at FROM public.lumibricks_bulk_previews ORDER BY created_at DESC LIMIT 1")
        r=cur.fetchone()
        return dict(r) if r else None

def approve_bulk_preview(db_url,bid):
    """One explicit approval, all-or-nothing transaction, no duplicate history on repeated requests."""
    with connect(db_url) as c,c.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM public.lumibricks_bulk_previews WHERE id=%s FOR UPDATE",(bid,))
        batch=cur.fetchone()
        if not batch: raise ValueError('Preisvorschau nicht gefunden')
        if batch['state']=='approved': return {'already':True,'approved':0,'history':0}
        if batch['state']!='ready': raise ValueError('Nur vollständig abgeschlossene Vorschauen dürfen freigegeben werden')
        entries=batch['entries']
        successes=[e for e in entries if e.get('status')=='ok']
        if not successes: raise ValueError('Keine gültigen Preise zur Freigabe')
        if len({int(e['product_id']) for e in successes})!=len(successes):
            raise ValueError('Doppelte Artikel in Vorschau; bitte erneut prüfen')
        mid=ensure_merchant(db_url)
        # Check each offer still exists, matches the preview, and is active, before changing anything.
        targets=[]
        for e in successes:
            cur.execute("""SELECT o.id,o.product_url,p.is_deleted FROM public.offers o
               JOIN public.products p ON p.id=o.product_id
               WHERE o.product_id=%s AND o.merchant_id=%s AND o.is_active=true FOR UPDATE OF o""",
               (int(e['product_id']),mid))
            offer=cur.fetchone()
            if not offer or offer['is_deleted'] or canonical(offer['product_url'])!=e['url']:
                raise ValueError(f"Artikel {e['sku']}: Zuordnung seit der Vorschau geändert. Bitte neu prüfen.")
            if Decimal(str(e['factor']))!=Decimal(str(batch['factor'])):
                raise ValueError('Abweichender Umrechnungsfaktor in Vorschau')
            if Decimal(str(e['eur'])) != (Decimal(str(e['usd']))*Decimal(str(batch['factor']))).quantize(Decimal('1'),rounding=ROUND_HALF_UP):
                raise ValueError('Inkonsistente Preisberechnung in Vorschau')
            if not str(e.get('variant_sku','')).upper().endswith('#DE'):
                raise ValueError(f"Artikel {e['sku']}: Keine bestätigte DE-Variante")
            targets.append((offer['id'],e))
        now=datetime.now(timezone.utc)
        history=0
        for oid,e in targets:
            price=Decimal(str(e['eur']))
            cur.execute('SELECT price,shipping_cost FROM public.current_prices WHERE offer_id=%s FOR UPDATE',(oid,))
            prev=cur.fetchone()
            cur.execute("""INSERT INTO public.current_prices(offer_id,price,shipping_cost,availability,fetch_status,last_success_at,last_attempt_at,error_message)
                VALUES(%s,%s,0,'available','success',%s,%s,NULL)
                ON CONFLICT(offer_id) DO UPDATE SET price=excluded.price,shipping_cost=0,availability='available',
                    fetch_status='success',last_success_at=excluded.last_success_at,last_attempt_at=excluded.last_attempt_at,error_message=NULL""",
                (oid,price,now,now))
            if not prev or prev['price']!=price or prev['shipping_cost']!=0:
                cur.execute("INSERT INTO public.price_history(offer_id,price,shipping_cost,availability,recorded_at) VALUES(%s,%s,0,'available',%s)",(oid,price,now))
                history+=1
        cur.execute("UPDATE public.lumibricks_bulk_previews SET state='approved',approved_at=%s,updated_at=%s WHERE id=%s",(now,now,bid))
    return {'already':False,'approved':len(targets),'history':history}
