import os, re, io, csv, json, base64, hashlib, html, time
from urllib.parse import urlencode
from datetime import datetime, timezone
from urllib.parse import urlparse
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
import db_store
import quickmatch
import brickmo_index
import lootstore_index
import lumibricks_store

st.set_page_config(page_title='LBrixx Preisvergleich', layout='wide', page_icon='lbrixx_logo.png')
DATA_FILE = 'lbrixx_products.csv'
COLUMNS = ['db_id','ean','odoo_id','brand_id','brand_name','hidden','deleted','sku','lbrixx_sku','description','lbrixx_price','brickmo_url','photo','brickmo_photo','brickmo_price','shipping','total','availability','last_checked','error','lootstore_url','lootstore_price','lootstore_shipping','lootstore_total','lootstore_availability','lootstore_checked','lootstore_error','lumibricks_url','lumibricks_price','lumibricks_shipping','lumibricks_total','lumibricks_availability','lumibricks_checked','lumibricks_error']
SEEDS = [
 {'sku':'FO9060','lbrixx_sku':'17003','description':'FunWhole Cyberpunk Future Bus','lbrixx_price':'','brickmo_url':'https://www.brickmo.com/themenwelt/fahrzeuge/82179/funwhole-cyberpunk-fo9060-future-bus-fo9060?number=FO9060'},
 {'sku':'FO9043','lbrixx_sku':'','description':'FunWhole Cyberpunk Polizeiauto','lbrixx_price':'','brickmo_url':'https://www.brickmo.com/themenwelt/fahrzeuge/82178/funwhole-cyberpunk-fo9043-polizeiauto-fo9043'},
]

def secrets(name, default=''):
    try: return st.secrets.get(name, default)
    except Exception: return default

DB_URL=str(secrets('SUPABASE_DB_URL',''))
if not DB_URL:
    st.error('SUPABASE_DB_URL fehlt in Streamlit Secrets. Die Datenbank wird nicht verändert.')
    st.stop()

def load():
    return lumibricks_store.overlay(DB_URL,db_store.load(DB_URL)),None

def normalize(df):
    for c in COLUMNS:
        if c not in df.columns: df[c]=''
    return df[COLUMNS].fillna('').astype(str).reset_index(drop=True)

def save(df):
    stored=db_store.save(DB_URL,normalize(df))
    st.session_state.products=normalize(lumibricks_store.overlay(DB_URL,stored))

def save_changed_rows(df, indices):
    """Persist only modified product rows, never rewrite the entire catalog."""
    unique=list(dict.fromkeys(int(i) for i in indices))
    if not unique:
        return
    db_store.save(DB_URL, normalize(df.loc[unique].copy()))
    st.session_state.products=normalize(lumibricks_store.overlay(DB_URL,db_store.load(DB_URL)))

def set_product_flag(row, field, enabled):
    product_id=str(row.get('db_id','')).strip()
    if not product_id.isdigit():
        raise ValueError('Datenbank-ID fehlt; bitte Ansicht aktualisieren.')
    db_store.set_product_flag(DB_URL,int(product_id),field,enabled)
    products=st.session_state.products
    mask=products['db_id'].astype(str)==product_id
    products.loc[mask,field]='1' if enabled else ''
    st.session_state.products=products

def euro_num(x):
    if x is None or str(x).strip()=='': return None
    s=str(x).strip().replace('€','').replace(' ','').replace('\xa0','')
    if ',' in s: s=s.replace('.','').replace(',','.')
    try: return round(float(s),2)
    except ValueError: return None

def fmt(x):
    n=euro_num(x)
    return f'{n:,.2f} €'.replace(',','X').replace('.',',').replace('X','.') if n is not None else '–'

def brickmo_match_keys(product):
    """Primary Odoo SKU plus explicit parenthesized manufacturer codes in description.

    Never modify the stored Odoo SKU. Only use conservative code-shaped aliases.
    """
    values = [str(product.get('sku', '') or '').strip()]
    description = str(product.get('description', '') or '')
    # e.g. '15002 (F9031) Book Cafe'; accept FO/F aliases and other alphanumeric
    # manufacturer codes only when explicitly parenthesized.
    for raw in re.findall(r'\(([A-Za-z]{1,6}[- ]?\d{3,8})\)', description):
        values.append(raw.replace(' ', '').replace('-', ''))
    # Lumibricks/FunWhole also uses purely numeric legacy SKUs in parentheses,
    # e.g. "L9118 (17007) ...". These must be matching keys, not Odoo SKU edits.
    brand_for_codes = str(product.get('brand_name', '') or '').strip().casefold()
    if brand_for_codes in ('lumibricks', 'funwhole'):
        for raw in re.findall(r'\((\d{4,8})\)', description):
            values.append(raw)
        # Also accept explicitly labelled references, but not arbitrary numbers
        # such as piece counts or dimensions from a product description.
        for raw in re.findall(r'(?i)\b(?:sku|artikelnummer|artikelnr\.?|ref(?:erenz)?\.?)\s*[:#-]?\s*(\d{4,8})\b', description):
            values.append(raw)
    # Odoo names may start with the manufacturer's code followed by the
    # internal SKU, e.g. "L9087 (20007) Alien Frontier: Bounty Hub".
    # Accept only an explicit alphanumeric code at the very beginning;
    # never interpret arbitrary words or numeric product descriptions as SKUs.
    leading = re.match(r'^\s*([A-Za-z]{1,6}[- ]?\d{3,8})(?=\s|[-–—:,(]|$)', description)
    if leading:
        values.append(leading.group(1).replace(' ', '').replace('-', ''))
    # Wange uses W5223 at Brickmo, while Odoo often stores 5223.
    # Only expand aliases when the imported Odoo brand is explicitly Wange.
    brand = str(product.get('brand_name', '') or '').strip().casefold()
    if brand == 'wange':
        for raw in list(values):
            code = brickmo_index.normalized_sku(raw)
            if re.fullmatch(r'\d{3,7}', code):
                values.append('W' + code)
            elif re.fullmatch(r'W\d{3,7}', code):
                values.append(code[1:])
    # Lumibricks: Odoo may store numeric 11015, while Brickmo uses LB11015.
    # Apply aliases ONLY when the Odoo brand is explicitly Lumibricks.
    if brand == 'lumibricks':
        for raw in list(values):
            code = brickmo_index.normalized_sku(raw)
            if re.fullmatch(r'\d{4,6}', code):
                values.append('LB' + code)
            elif re.fullmatch(r'LB\d{4,6}', code):
                values.append(code[2:])
    # Mattel Brick Shop: apply the retailer suffix to ALL Mattel SKUs,
    # not only JFT16 or a restricted letter/digit pattern.
    # Aliases are for searching only; never change the Odoo SKU.
    if brand in ('mattel brick shop', 'mattel'):
        for raw in list(values):
            code = brickmo_index.normalized_sku(raw)
            if not code or not re.fullmatch(r'[A-Z0-9_-]+', code):
                continue
            if code.endswith('_MA'):
                if len(code) > 3:
                    values.append(code[:-3])
            else:
                values.append(code + '_MA')
    return list(dict.fromkeys(brickmo_index.normalized_sku(x) for x in values if x))

def valid_url(url):
    p=urlparse(url.strip())
    return p.scheme=='https' and p.hostname in ('brickmo.com','www.brickmo.com')

def parse_product(url):
    if not valid_url(url): raise ValueError('Nur HTTPS-Produktlinks von brickmo.com erlaubt.')
    r=requests.get(url,headers={'User-Agent':'Mozilla/5.0 (compatible; LBrixxPriceMonitor/1.0)','Accept-Language':'de-DE,de;q=0.9'},timeout=25)
    r.raise_for_status()
    soup=BeautifulSoup(r.text,'html.parser')
    main=soup.select_one('.product--details') or soup.select_one('.product-details') or soup.select_one('.product--detail') or soup.select_one('[itemtype*="schema.org/Product"]') or soup.select_one('main') or soup
    title=(soup.select_one('h1').get_text(' ',strip=True) if soup.select_one('h1') else soup.title.get_text(' ',strip=True) if soup.title else '')
    # Prefer product detail pricing over recommendations; do not silently take arbitrary site-wide prices.
    price=None
    for root in [main]:
        for selector in ['.product--price .price--default','.product--price .price--content','.product--price','[itemprop="price"]']:
            for node in root.select(selector):
                if node.find_parent(class_=re.compile('recommend|cross.sell|similar|listing|slider',re.I)): continue
                raw=node.get('content','') or node.get_text(' ',strip=True)
                match=re.search(r'(\d{1,5}(?:[.,]\d{2}))\s*€?',raw)
                if match:
                    price=euro_num(match.group(1)); break
            if price is not None: break
        if price is not None: break
    if price is None: raise ValueError('Kein eindeutiger Hauptproduktpreis gefunden.')
    # Restrict status to the primary product detail area.
    status_root=soup.select_one('.product--details') or soup.select_one('.product-details') or main
    status_text=status_root.get_text(' ',strip=True).lower()
    unavailable=any(s in status_text for s in ['artikel steht derzeit nicht zur verfügung','derzeit nicht lieferbar','nicht verfügbar','ausverkauft'])
    available=any(s in status_text for s in ['sofort versandfertig','sofort lieferbar'])
    if unavailable and available: availability='Unbestätigt'
    elif unavailable: availability='Nicht lieferbar'
    elif available: availability='Lieferbar'
    else: availability='Unbestätigt'
    image=''
    for sel in ['meta[property="og:image"]','meta[name="twitter:image"]']:
        n=soup.select_one(sel)
        if n and n.get('content','').startswith('https://'): image=n['content']; break
    sku=''
    match=re.search(r'\bFO\d{4,6}\b',title+' '+url,re.I)
    if match: sku=match.group(0).upper()
    return {'sku':sku,'description':title,'brickmo_photo':image,'brickmo_price':f'{price:.2f}','shipping':'5.49','total':f'{price+5.49:.2f}','availability':availability,'last_checked':datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),'error':''}

LOOTSTORE_HOSTS=('lootstore.de','www.lootstore.de')

def valid_lootstore_url(url):
    p=urlparse(str(url).strip())
    return p.scheme=='https' and p.hostname in LOOTSTORE_HOSTS and p.path.startswith('/products/')

def parse_lootstore(url):
    if not valid_lootstore_url(url):
        raise ValueError('Ungültiger Lootstore-Produktlink.')
    # Shopify public product JSON, one HTTP request per product.
    clean=url.split('?',1)[0].rstrip('/')
    r=requests.get(clean+'.js',headers={'User-Agent':'LBrixxPriceMonitor/1.0 (manual price comparison)','Accept':'application/json'},timeout=25)
    if r.status_code==429:
        raise RuntimeError('Lootstore meldet HTTP 429 (zu viele Anfragen). Bitte später erneut versuchen.')
    r.raise_for_status()
    product=r.json()
    variants=product.get('variants') or []
    if not variants:
        raise ValueError('Keine Produktvarianten gefunden.')
    # Prefer the least expensive AVAILABLE variant. If all unavailable, use the lowest priced variant.
    purchasable=[v for v in variants if v.get('available') is True]
    candidates=purchasable or variants
    variant=min(candidates,key=lambda v:float(v.get('price') or 1e15))
    price=round(float(variant['price'])/100,2)  # Shopify .js price is integer cents
    availability='Lieferbar' if purchasable else 'Nicht lieferbar'
    shipping=0.0 if price>=99.0 else 4.99
    return {'lootstore_price':f'{price:.2f}','lootstore_shipping':f'{shipping:.2f}',
            'lootstore_total':f'{price+shipping:.2f}', 'lootstore_availability':availability,
            'lootstore_checked':datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'), 'lootstore_error':''}

def short_date(value):
    raw=str(value).strip()
    if not raw: return 'nicht geprüft'
    try:
        dt=datetime.strptime(raw,'%Y-%m-%d %H:%M UTC')
        return dt.strftime('%H:%M %d.%m.%y')
    except ValueError: return raw[:22]

def checked_status(value):
    return str(value).strip().lower() in ('1','true','yes','ja')

def update_prices(df,provider,indices):
    changed=df.copy()
    total=len(indices)
    if not total: return changed,0,0
    progress=st.progress(0)
    ok=fail=0
    for pos,i in enumerate(indices):
        row=changed.loc[i]
        url=str(row['brickmo_url'] if provider=='brickmo' else row['lootstore_url']).strip()
        try:
            data=parse_product(url) if provider=='brickmo' else parse_lootstore(url)
            for key,value in data.items():
                if provider=='brickmo' and key in ('sku','description'): continue
                if provider=='brickmo' and key=='brickmo_photo' and not value: continue
                changed.at[i,key]=value
            ok+=1
        except Exception as exc:
            field='error' if provider=='brickmo' else 'lootstore_error'
            changed.at[i,field]=str(exc)
            fail+=1
            if '429' in str(exc):
                st.warning(f'{provider}: HTTP 429. Weitere Anfragen wurden gestoppt.')
                break
        progress.progress((pos+1)/total)
        if pos+1<total: time.sleep(2)
    return changed,ok,fail

if 'products' not in st.session_state:
    try:
        df,_=load(); st.session_state.products=normalize(df)
    except Exception as e:
        st.error(f'Produktliste konnte nicht geladen werden: {e}'); st.stop()

header_logo, header_title = st.columns([1, 5], vertical_alignment='center')
with header_logo:
    st.image('lbrixx_logo.png', use_container_width=True)
with header_title:
    st.markdown('### Preisvergleich V28.11.49 · PostgreSQL')
# Odoo 19 JSON-2 API: credentials are read only from Streamlit Secrets.
ODOO_URL=str(secrets('ODOO_URL','')).rstrip('/')
ODOO_DB=str(secrets('ODOO_DATABASE',''))
ODOO_KEY=str(secrets('ODOO_API_KEY',''))
def odoo_read(limit=5, offset=0):
    if not all((ODOO_URL, ODOO_DB, ODOO_KEY)):
        raise ValueError('ODOO_URL, ODOO_DATABASE und ODOO_API_KEY in Streamlit Secrets ergänzen.')
    if not ODOO_URL.startswith('https://'):
        raise ValueError('Die Odoo-Adresse muss HTTPS verwenden.')
    endpoint=ODOO_URL+'/json/2/product.product/search_read'
    headers={'Authorization':'bearer '+ODOO_KEY,'X-Odoo-Database':ODOO_DB,'Content-Type':'application/json'}
    payload={'domain':[['active','=',True]],'fields':['id','default_code','name','list_price','barcode','image_128','product_tmpl_id'],'limit':limit,'offset':offset,'order':'id asc','context':{'lang':'de_DE'}}
    response=requests.post(endpoint,headers=headers,json=payload,timeout=40)
    if not response.ok:
        raise RuntimeError(f'Odoo HTTP {response.status_code}: API-Zugriff fehlgeschlagen. Prüfe Tarif, Datenbankname und Berechtigungen.')
    result=response.json()
    if isinstance(result,dict) and result.get('error'):
        raise RuntimeError('Odoo meldet einen API-Fehler. Bitte Zugriffsrechte und Tarif prüfen.')
    if not isinstance(result,list):
        raise ValueError('Unerwartete Antwort der Odoo-API.')
    return result

def odoo_brand_values(products):
    """Read the 'Marke' template attribute via Odoo JSON-2; IDs are Odoo value IDs."""
    templates = set()
    for p in products:
        value = p.get('product_tmpl_id')
        tid = value[0] if isinstance(value, (list, tuple)) and value else value
        if isinstance(tid, int):
            templates.add(tid)
    if not templates:
        return {}
    headers = {'Authorization': 'bearer ' + ODOO_KEY,
               'X-Odoo-Database': ODOO_DB, 'Content-Type': 'application/json'}
    def read_model(model, domain, fields):
        result = []
        for start in range(0, len(domain[0][2]), 100):
            batch = domain[0][2][start:start+100]
            payload = {'domain': [[domain[0][0], 'in', batch]],
                       'fields': fields, 'limit': 10000, 'context': {'lang': 'de_DE'}}
            response = requests.post(ODOO_URL + '/json/2/' + model + '/search_read',
                                     headers=headers, json=payload, timeout=40)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                raise ValueError('Unerwartete Odoo-Antwort für ' + model)
            result.extend(data)
        return result
    lines = read_model('product.template.attribute.line',
                       [['product_tmpl_id', 'in', sorted(templates)]],
                       ['product_tmpl_id', 'attribute_id', 'value_ids'])
    brand_lines = []
    value_ids = set()
    for line in lines:
        attribute = line.get('attribute_id')
        attr_name = attribute[1] if isinstance(attribute, (list, tuple)) and len(attribute)>1 else ''
        if str(attr_name).strip().casefold() == 'marke':
            brand_lines.append(line)
            value_ids.update(int(v) for v in line.get('value_ids', []) if isinstance(v, int))
    if not value_ids:
        return {}
    values = read_model('product.attribute.value', [['id', 'in', sorted(value_ids)]], ['name'])
    names = {int(v['id']): str(v.get('name') or '') for v in values}
    result = {}
    for line in brand_lines:
        tid = line['product_tmpl_id']
        tid = tid[0] if isinstance(tid, (list, tuple)) else tid
        ids = [v for v in line.get('value_ids', []) if v in names]
        # Only one unambiguous brand per product; do not guess when multiple values exist.
        if len(ids) == 1:
            result[int(tid)] = (str(ids[0]), names[ids[0]])
    return result

def odoo_merge(existing, products):
    result=normalize(existing.copy())
    added=updated=skipped=0
    for product in products:
        oid=str(product.get('id','')).strip()
        sku=str(product.get('default_code') or '').strip()
        if not oid:
            skipped+=1; continue
        matching=result.index[result.odoo_id.eq(oid)].tolist()
        if not matching and sku:
            matching=result.index[(result.sku.str.casefold()==sku.casefold()) & (result.odoo_id.eq(''))].tolist()
        if len(matching)>1:
            skipped+=1; continue
        index=matching[0] if matching else None
        if index is not None and str(result.at[index,'deleted']).strip()=='1':
            skipped+=1; continue
        name=str(product.get('name') or '')
        price=euro_num(product.get('list_price'))
        raw_image=product.get('image_128') # Kleines Odoo-Vorschaubild (128 px)
        # Odoo image_128 is a base64-encoded thumbnail (or False).
        # Keep the old Odoo image if Odoo has no image for this product.
        odoo_photo=''
        if isinstance(raw_image,str) and raw_image.strip():
            try:
                image_bytes=base64.b64decode(raw_image,validate=True)
                if len(image_bytes) <= 120_000:
                    mime='image/png' if image_bytes.startswith(b'\x89PNG') else 'image/jpeg'
                    odoo_photo=f'data:{mime};base64,{raw_image}'
            except (ValueError,base64.binascii.Error):
                pass
        patch={'ean':str(product.get('barcode') or '').strip(),'odoo_id':oid,'description':name,'lbrixx_price':f'{price:.2f}' if price is not None else ''}
        if product.get('_brand_id'):
            patch['brand_id'] = product['_brand_id']
            patch['brand_name'] = product['_brand_name']
        if odoo_photo: patch['photo']=odoo_photo
        if sku: patch['sku']=sku
        if index is not None:
            old_photo=str(result.at[index,'photo']).strip()
            if old_photo.startswith('https://') and not str(result.at[index,'brickmo_photo']).strip():
                result.at[index,'brickmo_photo']=old_photo
                result.at[index,'photo']=''
        if index is None:
            # New products without competitor links can be enriched later.
            record={c:'' for c in COLUMNS}; record.update(patch)
            result=pd.concat([result,pd.DataFrame([record])],ignore_index=True)
            added+=1
        else:
            for key,value in patch.items(): result.at[index,key]=value
            updated+=1
    return normalize(result),added,updated,skipped

with st.expander('🔌 Odoo 19 Online – Verbindung und Produktimport',expanded=False):
    st.caption('Odoo wird nur gelesen. Produktnamen werden mit deutschem Sprachkontext (de_DE) abgefragt. Fehlt eine Übersetzung, liefert Odoo ggf. den ursprünglichen Namen.')
    st.write('Server:', ODOO_URL or 'Nicht konfiguriert')
    col_test,col_import=st.columns(2)
    with col_test:
        test_odoo=st.button('🔍 Odoo-Verbindung testen',use_container_width=True)
    with col_import:
        import_odoo=st.button('📥 Alle Artikel aus Odoo aktualisieren',use_container_width=True)
    if test_odoo:
        try:
            sample=odoo_read(limit=5)
            st.success(f'Verbindung erfolgreich: {len(sample)} Beispielartikel mit Sprache de_DE abgerufen.')
            st.dataframe(pd.DataFrame([{'Odoo-ID':p.get('id'),'SKU':p.get('default_code'),'Artikel':p.get('name'),'Preis (€)':p.get('list_price')} for p in sample]),hide_index=True,use_container_width=True)
        except Exception as exc:
            st.error(str(exc))
    if import_odoo:
        try:
            all_products=[]
            max_items=10000
            for offset in range(0,max_items,100):
                batch_limit=min(100,max_items-offset)
                batch=odoo_read(limit=batch_limit,offset=offset)
                all_products.extend(batch)
                if len(batch)<batch_limit: break
            if len(all_products)==max_items:
                st.warning('Import auf 10.000 Produkte begrenzt.')
            brands=odoo_brand_values(all_products)
            for item in all_products:
                tmpl=item.get('product_tmpl_id')
                tmpl=tmpl[0] if isinstance(tmpl,(list,tuple)) and tmpl else tmpl
                if tmpl in brands:
                    item['_brand_id'],item['_brand_name']=brands[tmpl]
            merged,added,updated,skipped=odoo_merge(st.session_state.products,all_products)
            # Only persist changed Odoo fields; never touch existing offers or prices.
            previous=st.session_state.products
            compare_fields=['odoo_id','sku','ean','description','lbrixx_price','photo','brand_id','brand_name']
            changed=[]
            for idx,row in merged.iterrows():
                if idx>=len(previous) or any(str(row[c])!=str(previous.at[idx,c]) for c in compare_fields):
                    changed.append(idx)
            if changed:
                db_store.save_odoo_rows(DB_URL,merged.loc[changed].copy())
                st.session_state.products=normalize(lumibricks_store.overlay(DB_URL,db_store.load(DB_URL)))
            st.success(f'Odoo-Synchronisierung: {added} neu, {updated} geprüft, {len(changed)} tatsächlich geändert, {skipped} übersprungen. Wettbewerberlinks und Preisverläufe unverändert.')
            st.rerun()
        except Exception as exc:
            st.error(f'Odoo-Import fehlgeschlagen: {exc}')


st.caption('Brickmo und Lootstore · Einzelartikelvergleich nach Deutschland · Gesamtpreise inklusive Versand')
st.success('🗄️ PostgreSQL verbunden – Produktdaten werden in Supabase gespeichert.')

with st.expander('➕ Artikel manuell hinzufügen:', expanded=False):
    with st.expander('Brickmo', expanded=False):
        with st.form('add_item',clear_on_submit=False):
            a,b=st.columns(2)
            with a:
                url=st.text_input('Brickmo-Produktlink *',placeholder='https://www.brickmo.com/...')
                sku=st.text_input('Hersteller-SKU (optional, wird sonst aus Link ermittelt)')
            with b:
                name=st.text_input('Beschreibung (optional, wird aus Shop übernommen)')
                ownprice=st.text_input('LBrixx-Verkaufspreis in € (optional)',placeholder='49,90')
            submitted=st.form_submit_button('Artikel hinzufügen und Brickmo prüfen',type='primary')
        if submitted:
            if not valid_url(url): st.error('Bitte eine gültige Brickmo-HTTPS-Produktadresse einfügen.')
            else:
                df=st.session_state.products.copy()
                if url.strip() in df.brickmo_url.values: st.error('Dieser Brickmo-Link ist bereits gespeichert.')
                else:
                    with st.spinner('Brickmo wird abgefragt…'):
                        try:
                            result=parse_product(url.strip())
                            key=(sku.strip() or result['sku']).upper()
                            if key and key in df.sku.values: st.error('Diese Hersteller-SKU ist bereits vorhanden. Bitte bestehenden Eintrag bearbeiten.')
                            else:
                                row={c:'' for c in COLUMNS}; row.update(result)
                                row.update({'sku':key,'description':name.strip() or result['description'],'lbrixx_price':str(euro_num(ownprice) or '') if ownprice.strip() else '', 'brickmo_url':url.strip()})
                                save(pd.concat([df,pd.DataFrame([row])],ignore_index=True)); st.success('Artikel hinzugefügt und gespeichert.'); st.rerun()
                        except Exception as e: st.error(f'Artikel konnte nicht automatisch erfasst werden: {e}')

    with st.expander('Lootstore', expanded=False):
        st.caption('Lootstore-Produktlink einem bestehenden Artikel zuordnen oder einen neuen Artikel anlegen. Suchparameter werden entfernt.')
        with st.form('add_lootstore_item'):
            loot_url=st.text_input('Lootstore-Produktlink *',placeholder='https://lootstore.de/products/...')
            loot_sku=st.text_input('Hersteller-SKU *',placeholder='z. B. F9051')
            loot_name=st.text_input('Produktbezeichnung (nur bei neuen Artikeln)')
            loot_submit=st.form_submit_button('Lootstore-Artikel hinzufügen',type='primary')
        if loot_submit:
            clean_loot=loot_url.strip().split('?',1)[0].split('#',1)[0].rstrip('/')
            key=loot_sku.strip().upper()
            if not valid_lootstore_url(clean_loot):
                st.error('Bitte einen gültigen Lootstore-HTTPS-Produktlink eintragen.')
            elif not key:
                st.error('Bitte die Hersteller-SKU zur eindeutigen Zuordnung angeben.')
            else:
                try:
                    current=st.session_state.products.copy()
                    matches=current.index[current.sku.astype(str).str.upper().eq(key)].tolist()
                    if len(matches)>1:
                        st.error('Mehrere Artikel mit dieser SKU gefunden. Bitte über die Artikelbearbeitung zuordnen.')
                    elif clean_loot in current.lootstore_url.astype(str).str.split('?',n=1).str[0].str.rstrip('/').values:
                        st.error('Dieser Lootstore-Link ist bereits einem Artikel zugeordnet.')
                    else:
                        with st.spinner('Lootstore-Preis wird abgefragt…'):
                            result=parse_lootstore(clean_loot)
                        if matches:
                            idx=matches[0]
                            current.at[idx,'lootstore_url']=clean_loot
                            for field,value in result.items():
                                if field.startswith('lootstore_') and field in current.columns and field!='lootstore_url':
                                    current.at[idx,field]=value
                        else:
                            row={c:'' for c in COLUMNS}
                            row.update({'sku':key,'description':loot_name.strip() or key,'lootstore_url':clean_loot})
                            row.update({k:v for k,v in result.items() if k.startswith('lootstore_') and k!='lootstore_url'})
                            current=pd.concat([current,pd.DataFrame([row])],ignore_index=True)
                        save(current)
                        st.success('Lootstore-Artikel gespeichert.')
                        st.rerun()
                except Exception as exc:
                    st.error(f'Lootstore-Artikel konnte nicht gespeichert werden: {exc}')

with st.expander('📦 Lootstore-Klemmbausteine indexieren (V28.11.30)', expanded=False):
    st.caption('Schonende Pantasy-Indexierung über die Lootstore-Produktsitemap: höchstens 20 Produktprüfungen je Durchlauf, mindestens 2 Sekunden Abstand und sofortiger Stopp bei HTTP 429. Fortschritt wird in PostgreSQL gespeichert. Keine automatische Zuordnung.')
    if st.button('Pantasy-Sitemap: nächste 20 Produkte prüfen', key='lootstore_collection_index'):
        try:
            with st.spinner('Lootstore-Katalog wird eingelesen…'):
                st.session_state.lootstore_index_report = lootstore_index.index_collection(DB_URL)
                st.session_state.lootstore_comparison = None
        except Exception as exc:
            st.error(f'Lootstore-Indexierung fehlgeschlagen: {exc}')
    loot_report = st.session_state.get('lootstore_index_report')
    if loot_report:
        st.write(f"{loot_report.get('checked', 0)} Produkte geprüft · {loot_report['saved']} Pantasy-Links gespeichert · {loot_report.get('remaining', 0)} Kandidaten offen")
        if not loot_report['complete']:
            st.warning('Durchlauf unterbrochen oder weitere Kandidaten vorhanden. Diagnose prüfen; bei HTTP 429 bitte später erneut versuchen.')
        with st.expander('Lootstore-Indexdiagnose'):
            for note in loot_report['notes']:
                st.caption(note)
            st.write('Beispiel-SKUs:', ', '.join(loot_report['sample_skus']) or 'Keine')
    with st.expander('➕ Einzelnen Lootstore-Produktlink indexieren', expanded=False):
        st.caption('Für Produkte, die nicht in der Kategorie erscheinen. Prüft Hersteller und Artikelnummer; erstellt keine automatische LBrixx-Zuordnung.')
        single_url = st.text_input('Lootstore-Produktlink', value='https://lootstore.de/products/pantasy-steampunk-die-nautilus-85029-klemmbausteinset', key='lootstore_single_index_url')
        if st.button('Produkt prüfen und in Lootstore-Index speichern', key='lootstore_single_index_button'):
            try:
                result = lootstore_index.index_product_url(DB_URL, single_url)
                st.success(f"Gespeichert: {result['brand']} {result['sku']} – {result['title']} (Quelle: {result['source']}).")
                st.session_state.lootstore_comparison = None
            except Exception as exc:
                st.error(f'Produkt konnte nicht indexiert werden: {exc}')
    with st.expander('🔎 Lootstore-SKU-Einzeldiagnose (Pantasy)', expanded=False):
        st.caption('Prüft Index, Shopify-Suche, HTML-Suche und optional einen direkten Produktlink. Keine automatische Zuordnung oder Datenbankänderung.')
        diagnostic_sku = st.text_input('Pantasy-Artikelnummer', value='85029', key='lootstore_diagnostic_sku')
        diagnostic_url = st.text_input('Direkter Lootstore-Produktlink (optional)', value='https://lootstore.de/products/pantasy-steampunk-die-nautilus-85029-klemmbausteinset', key='lootstore_diagnostic_url')
        if st.button('SKU prüfen', key='lootstore_sku_diagnostic_run'):
            try:
                with st.spinner('Index und Lootstore-Produktsuche prüfen…'):
                    st.session_state.lootstore_sku_diagnostic = lootstore_index.diagnose_sku(DB_URL, diagnostic_sku, product_url=diagnostic_url)
            except Exception as exc:
                st.error(f'SKU-Diagnose fehlgeschlagen: {exc}')
        result = st.session_state.get('lootstore_sku_diagnostic')
        if result:
            st.write(f"SKU {result['sku']} · {len(result['stored'])} Index-Treffer · {len(result['live'])} Live-Treffer")
            if not result['stored']:
                st.warning('Artikelnummer fehlt im gespeicherten Lootstore-Index.')
            for entry in result['stored']:
                st.write(f"Index: {entry['manufacturer_sku']} · {entry['brand_name']} · {entry['product_title']}")
                st.markdown(f"[Produkt öffnen]({entry['product_url']})")
            for entry in result['live']:
                st.write(f"Live: {', '.join(entry['skus']) or result['sku']} · {entry['brand']} · {entry['title']} · Quelle: {entry.get('source', 'Varianten-SKU')}")
                if not entry['pantasy']:
                    st.warning('Hersteller im Shopify-Produktdatensatz ist nicht Pantasy – kein automatischer Abgleich.')
                st.markdown(f"[Produkt öffnen]({entry['url']})")
            for entry in result.get('unverified', []):
                st.warning(f"Produktseite gefunden, SKU nicht bestätigt: {entry['title']} · Hersteller {entry['brand']} · Varianten-SKUs: {', '.join(entry['skus']) or 'keine'}")
                st.markdown(f"[Produkt öffnen]({entry['url']})")
            st.caption(f"{result.get('checked_handles', 0)} Produktlinks bei der Live-Diagnose geprüft.")
            if not result['live']:
                st.info('Kein eindeutiger Live-Treffer über Varianten-SKU oder Pantasy-Titel und URL.')
            for err in result['errors']:
                st.caption('Abrufhinweis: ' + err)
    if st.button('Pantasy mit LBrixx abgleichen', key='lootstore_pantasy_compare'):
        try:
            own = st.session_state.products.copy()
            own = own[(own['deleted'] != '1') & (own['lootstore_url'].astype(str).str.strip() == '')]
            own = own[own['brand_name'].map(lootstore_index.brand).eq('pantasy')]
            indexed = lootstore_index.batch_match_skus(DB_URL, [k for _, p in own.iterrows() for k in brickmo_match_keys(p)])
            candidates = []
            for _, product in own.iterrows():
                seen = set()
                for key in brickmo_match_keys(product):
                    for match in indexed.get(lootstore_index.match_key(key), []):
                        if lootstore_index.brand(match['brand_name']) != 'pantasy':
                            continue
                        if match['product_url'] in seen:
                            continue
                        seen.add(match['product_url'])
                        candidates.append({'db_id':str(product['db_id']), 'sku':str(product['sku']),
                            'description':str(product['description']), 'title':match['product_title'],
                            'url':match['product_url'], 'matched_sku':key})
            st.session_state.lootstore_comparison = candidates
            matched_ids = {c['db_id'] for c in candidates}
            st.session_state.lootstore_match_diagnostics = {
                'total': len(own), 'matched': len(matched_ids),
                'unmatched': [str(p['sku']) for _, p in own.iterrows()
                              if str(p['db_id']) not in matched_ids][:30],
            }
        except Exception as exc:
            st.error(f'Lootstore-Abgleich fehlgeschlagen: {exc}')
    diagnostics = st.session_state.get('lootstore_match_diagnostics')
    if diagnostics:
        with st.expander('Pantasy-Abgleichdiagnose'):
            st.write(f"{diagnostics['total']} offene Pantasy-Artikel · {diagnostics['matched']} Artikel mit Treffer")
            if diagnostics['unmatched']:
                st.caption('Ohne Treffer (max. 30): ' + ', '.join(diagnostics['unmatched']))
    loot_candidates = st.session_state.get('lootstore_comparison')
    if loot_candidates is not None:
        st.write(f'{len(loot_candidates)} Pantasy-Zuordnungsvorschläge – einzeln bestätigen.')
        for i, candidate in enumerate(loot_candidates):
            with st.container(border=True):
                st.markdown(f"**{html.escape(candidate['sku'])} – {html.escape(candidate['description'])}**")
                st.write('Lootstore:', candidate['title'])
                st.caption(f"Treffer über SKU {candidate['matched_sku']}; Marke Pantasy laut Lootstore-Produktdaten.")
                st.markdown(f"[Lootstore-Produkt prüfen]({candidate['url']})")
                if st.button('✓ Lootstore-Zuordnung bestätigen', key=f'loot_confirm_{candidate["db_id"]}_{i}'):
                    try:
                        clean = db_store.confirm_match(DB_URL, candidate['db_id'], 'Lootstore', candidate['url'])
                        current = st.session_state.products
                        mask = current['db_id'].astype(str) == candidate['db_id']
                        current.loc[mask, 'lootstore_url'] = clean
                        st.session_state.products = current
                        st.session_state.lootstore_comparison = [c for c in loot_candidates if c['db_id'] != candidate['db_id']]
                        st.rerun()
                    except Exception as exc:
                        st.error(f'Zuordnung fehlgeschlagen: {exc}')
        if not loot_candidates:
            st.info('Keine offenen Pantasy-Vorschläge vorhanden. SKU- und Herstellerdaten in Lootstore prüfen.')

with st.expander('📦 Brickmo-Herstellerkategorien indexieren (V28.11.21)', expanded=False):
    st.caption('Ausschließlich offizielle Herstellerkategorien. Themenwelten und freie Kategorie-URLs sind für Indexierung und Vorschläge deaktiviert. Bestehende Zuordnungen bleiben erhalten.')
    st.caption('Maximal 250 Seiten je Hersteller; Vorschläge müssen einzeln bestätigt werden.')
    # V28.10: Nur FunWhole als zusätzliche Herstellerquelle; keine anderen Themenwelten.
    funwhole_url = 'https://www.brickmo.com/weitere-marken/funwhole/'
    st.markdown('**Neu: Herstellerindex FunWhole**')
    st.caption('Liest alle erkannten Seiten der Brickmo-FunWhole-Herstellerübersicht ein. Bereits gespeicherte Zuordnungen bleiben bestehen.')
    if st.button('FunWhole vollständig indexieren', key='funwhole_full_index'):
        try:
            with st.spinner('FunWhole-Herstellerseiten werden vollständig indexiert…'):
                st.session_state.full_category_report = brickmo_index.index_category(DB_URL, funwhole_url)
                st.session_state.full_category_comparison = None
        except Exception as exc:
            st.error(f'FunWhole-Indexierung fehlgeschlagen: {exc}')
    # V28.11.2: Pantasy als zweite Herstellerquelle (zusätzlich zu FunWhole).
    pantasy_url = 'https://www.brickmo.com/weitere-marken/pantasy/'
    st.markdown('**Neu: Herstellerindex Pantasy**')
    st.caption('Indexiert die Brickmo-Pantasy-Herstellerübersicht. Bestehende Produktzuordnungen und Preise bleiben unverändert.')
    if st.button('Pantasy vollständig indexieren', key='pantasy_full_index'):
        try:
            with st.spinner('Pantasy-Herstellerseiten werden vollständig indexiert…'):
                st.session_state.full_category_report = brickmo_index.index_category(DB_URL, pantasy_url)
                st.session_state.full_category_comparison = None
        except Exception as exc:
            st.error(f'Pantasy-Indexierung fehlgeschlagen: {exc}')
    # V28.11.3: Reobrix als dritte Herstellerquelle.
    reobrix_url = 'https://www.brickmo.com/weitere-marken/reobrix/'
    st.markdown('**Herstellerindex Reobrix**')
    st.caption('Indexiert die Brickmo-Reobrix-Herstellerübersicht. Bestehende Zuordnungen und Preise bleiben erhalten.')
    if st.button('Reobrix vollständig indexieren', key='reobrix_full_index'):
        try:
            with st.spinner('Reobrix-Herstellerseiten werden vollständig indexiert…'):
                st.session_state.full_category_report = brickmo_index.index_category(DB_URL, reobrix_url)
                st.session_state.full_category_comparison = None
        except Exception as exc:
            st.error(f'Reobrix-Indexierung fehlgeschlagen: {exc}')
    # V28.11.4: Wange als vierte Herstellerquelle.
    wange_url = 'https://www.brickmo.com/wange/'
    st.markdown('**Herstellerindex Wange**')
    st.caption('Indexiert die Brickmo-Wange-Kategorie. Bestehende Zuordnungen und Preise bleiben erhalten.')
    if st.button('Wange vollständig indexieren', key='wange_full_index'):
        try:
            with st.spinner('Wange-Herstellerseiten werden vollständig indexiert…'):
                st.session_state.full_category_report = brickmo_index.index_category(DB_URL, wange_url)
                st.session_state.full_category_comparison = None
        except Exception as exc:
            st.error(f'Wange-Indexierung fehlgeschlagen: {exc}')
    # V28.11.6: Mould King als fünfte Herstellerquelle.
    mouldking_url = 'https://www.brickmo.com/cat/index/sCategory/6589'
    st.markdown('**Herstellerindex Mould King**')
    st.caption('Indexiert die Brickmo-Mould-King-Kategorie. Bestehende Zuordnungen und Preise bleiben erhalten.')
    if st.button('Mould King vollständig indexieren', key='mouldking_full_index'):
        try:
            with st.spinner('Mould-King-Herstellerseiten werden vollständig indexiert…'):
                st.session_state.full_category_report = brickmo_index.index_category(DB_URL, mouldking_url)
                st.session_state.full_category_comparison = None
        except Exception as exc:
            st.error(f'Mould-King-Indexierung fehlgeschlagen: {exc}')
    # V28.11.7: Happy Build vorab aufnehmen, auch wenn die Kategorie derzeit leer ist.
    happybuild_url = 'https://www.brickmo.com/weitere-marken/happy-build/'
    st.markdown('**Herstellerindex Happy Build**')
    st.caption('Die Kategorie ist derzeit möglicherweise leer. Bei 0 Artikeln bleiben vorhandene Daten unverändert; die Quelle kann später erneut indexiert werden.')
    if st.button('Happy Build vollständig indexieren', key='happybuild_full_index'):
        try:
            with st.spinner('Happy-Build-Herstellerseiten werden indexiert…'):
                st.session_state.full_category_report = brickmo_index.index_category(DB_URL, happybuild_url)
                st.session_state.full_category_comparison = None
        except Exception as exc:
            st.error(f'Happy-Build-Indexierung fehlgeschlagen: {exc}')
    # V28.11.9: Mattel Brick Shop als siebte Herstellerquelle.
    # Immer mit der ersten Kategorieseite starten, damit alle Seiten erfasst werden.
    mattel_url = 'https://www.brickmo.com/weitere-marken/mattel-brick-shop/'
    st.markdown('**Herstellerindex Mattel Brick Shop**')
    st.caption('Indexiert alle erkannten Seiten der Mattel-Brick-Shop-Kategorie (nicht nur Seite 2). Bestehende Zuordnungen und Preise bleiben erhalten.')
    if st.button('Mattel Brick Shop vollständig indexieren', key='mattel_brick_shop_full_index'):
        try:
            with st.spinner('Mattel-Brick-Shop-Herstellerseiten werden indexiert…'):
                st.session_state.full_category_report = brickmo_index.index_category(DB_URL, mattel_url)
                st.session_state.full_category_comparison = None
        except Exception as exc:
            st.error(f'Mattel-Brick-Shop-Indexierung fehlgeschlagen: {exc}')
    # V28.11.9: CaDA als achte Herstellerquelle.
    cada_url = 'https://www.brickmo.com/cada/'
    st.markdown('**Herstellerindex CaDA**')
    st.caption('Indexiert alle erkannten Seiten der Brickmo-CaDA-Kategorie. Bestehende Zuordnungen und Preise bleiben erhalten.')
    if st.button('CaDA vollständig indexieren', key='cada_full_index'):
        try:
            with st.spinner('CaDA-Herstellerseiten werden indexiert…'):
                st.session_state.full_category_report = brickmo_index.index_category(DB_URL, cada_url)
                st.session_state.full_category_comparison = None
        except Exception as exc:
            st.error(f'CaDA-Indexierung fehlgeschlagen: {exc}')
    full_report = st.session_state.get('full_category_report')
    if full_report:
        st.write(f"{full_report['pages_checked']} von {full_report['pages_detected']} erkannten Seiten geprüft · {full_report['links_found']} eindeutige Produktlinks · {full_report['sku_links']} SKU-Links gespeichert")
        if not full_report['complete']:
            st.warning('Nicht alle Seiten konnten verarbeitet werden. Bitte die Diagnose prüfen.')
        elif full_report['saved']:
            st.success('Katalogindex aktualisiert. Bestehende LBrixx-Zuordnungen bleiben unverändert.')
        else:
            st.warning('Keine eindeutigen SKU-Links erkannt. Es wurden keine Zuordnungen vorgenommen.')
        with st.expander('Index-Diagnose', expanded=False):
            for note in full_report['notes']: st.caption(note)
            st.write('Beispiel-SKUs:', ', '.join(full_report['sample_skus']) or 'Keine')
            st.caption('Falls Mattel 0 SKU-Links meldet: Produktdetail-Anreicherung für die Mattel-Kategorie starten; die Produktnummern stehen möglicherweise nur auf den Detailseiten.')
        with st.expander('SKU-Diagnose: Warum fehlt ein Vorschlag?', expanded=False):
            diagnostic_sku = st.text_input('Odoo-Artikelnummer (z. B. 11015)', value='11015', key='brickmo_diagnostic_sku').strip().upper()
            if st.button('Artikelnummer prüfen', key='brickmo_diagnostic_button'):
                try:
                    own = st.session_state.products.copy()
                    own = own[own['sku'].astype(str).str.upper().eq(diagnostic_sku)]
                    if own.empty:
                        st.warning('Keine Odoo-Artikelzeile mit dieser SKU gefunden.')
                    for _, own_row in own.iterrows():
                        keys = brickmo_match_keys(own_row)
                        st.write('Odoo-Marke:', str(own_row.get('brand_name', '')) or '(leer)')
                        st.write('Suchschlüssel:', ', '.join(keys))
                        found = brickmo_index.batch_match_skus(DB_URL, keys)
                        rows = [m for key in keys for m in found.get(key, [])]
                        if not rows:
                            st.warning('Kein SKU-Treffer im gespeicherten Brickmo-Index. Herstellerkategorie neu indexieren oder SKU-Erkennung prüfen.')
                        for m in rows[:25]:
                            cb = brickmo_index.match_catalog_brand(m)
                            ob = str(own_row.get('brand_name', '') or '')
                            verdict = ('Marke stimmt überein' if brickmo_index.brand_key(cb) == brickmo_index.brand_key(ob) and cb else 'Marke fehlt oder stimmt nicht überein')
                            st.write({'SKU':m.get('manufacturer_sku'), 'Brickmo-Marke':cb or '(nicht gesichert)', 'Ergebnis':verdict, 'URL':m.get('product_url'), 'Quelle':m.get('source_sitemap')})
                except Exception as exc:
                    st.error(f'Diagnose fehlgeschlagen: {exc}')
        if st.button('Mit LBrixx-Artikeln abgleichen', key='full_category_compare'):
            try:
                products_to_match = st.session_state.products.copy()
                products_to_match = products_to_match[(products_to_match['deleted']!='1') & (products_to_match['brickmo_url'].astype(str).str.strip()=='')]
                indexed = brickmo_index.batch_match_skus(DB_URL, [key for _, p in products_to_match.iterrows() for key in brickmo_match_keys(p)])
                comparison = []
                rejected_brand = 0
                unknown_brand = 0
                for _, product in products_to_match.iterrows():
                    own_brand = brickmo_index.brand_key(product.get('brand_name', ''))
                    seen_urls = set()
                    for key in brickmo_match_keys(product):
                        for match in indexed.get(key, []):
                            catalog_brand = brickmo_index.brand_key(brickmo_index.match_catalog_brand(match))
                            if not own_brand or not catalog_brand:
                                unknown_brand += 1
                                continue
                            if own_brand != catalog_brand:
                                rejected_brand += 1
                                continue
                            if match['product_url'] in seen_urls:
                                continue
                            seen_urls.add(match['product_url'])
                            comparison.append({'db_id':str(product['db_id']), 'SKU':str(product['sku']),
                                'LBrixx-Artikel':str(product['description']),
                                'Brickmo-Produkt':match['product_title'], 'Brickmo-Link':match['product_url'],
                                'Quelle':match['source_sitemap'], 'Treffer-SKU':key})
                st.session_state.full_category_diagnostics = (rejected_brand, unknown_brand)
                st.session_state.full_category_comparison = comparison
            except Exception as exc:
                st.error(f'Abgleich fehlgeschlagen: {exc}')
        comparison = st.session_state.get('full_category_comparison')
        if comparison is not None:
            st.write(f'{len(comparison)} mögliche Zuordnungsvorschläge – nur nach Einzelbestätigung gespeichert.')
            rejected_brand, unknown_brand = st.session_state.get('full_category_diagnostics', (0, 0))
            st.caption(f'Markenfilter: {rejected_brand} markenfremde Treffer und {unknown_brand} Treffer ohne gesicherte Herstellerquelle ausgeschlossen. Ältere Themenwelt-Einträge werden nicht mehr als Vorschläge berücksichtigt. Herstellerkategorien bei Bedarf neu indexieren.')
            for i, candidate in enumerate(comparison):
                with st.container(border=True):
                    st.markdown(f"**{html.escape(candidate['SKU'])} – {html.escape(candidate['LBrixx-Artikel'])}**")
                    st.caption(f"Abgleich über Hersteller-SKU: {html.escape(candidate.get('Treffer-SKU', candidate['SKU']))}")
                    st.markdown(
                        '<div style="font-size:17px;font-weight:600;color:#111111;line-height:1.45;">'
                        f"Brickmo: {html.escape(str(candidate['Brickmo-Produkt']))}"
                        '</div>',
                        unsafe_allow_html=True,
                    )
                    st.caption(f"Quelle: {candidate['Quelle']} · Bitte Hersteller und SKU vor Bestätigung vergleichen.")
                    st.markdown(f"[Brickmo-Produkt prüfen]({candidate['Brickmo-Link']})")
                    if st.button('✓ Zuordnung bestätigen', key=f'category_confirm_{candidate["db_id"]}_{i}'):
                        try:
                            clean = db_store.confirm_match(DB_URL, candidate['db_id'], 'Brickmo', candidate['Brickmo-Link'])
                            current = st.session_state.products
                            mask = current['db_id'].astype(str) == candidate['db_id']
                            current.loc[mask, 'brickmo_url'] = clean
                            st.session_state.products = current
                            st.session_state.full_category_comparison = [c for c in comparison if c['db_id'] != candidate['db_id']]
                            st.rerun()
                        except Exception as exc:
                            st.error(f'Zuordnung fehlgeschlagen: {exc}')
            if not comparison:
                st.info('Keine offenen Vorschläge mehr vorhanden.')
    st.divider()
    st.markdown('**Optionale SKU-Ergänzung aus Produktdetails**')
    st.caption('Liest begrenzt Produktseiten ohne SKU nach und erkennt auch numerische Artikelnummern. '
               'Bereits zugeordnete LBrixx-Artikel werden nicht verändert. Bitte nur gelegentlich starten.')
    detail_limit = st.selectbox('Maximale Produktseiten je Durchlauf', [10, 25, 50], key='detail_enrich_limit')
    if st.button('Fehlende Artikelnummern nachladen', key='category_enrich_run'):
        try:
            with st.spinner('Brickmo-Produktdetails werden vorsichtig geprüft…'):
                st.session_state.category_enrich_report = brickmo_index.enrich_category_skus(DB_URL, full_category_url, limit=detail_limit)
        except Exception as exc:
            st.error(f'Detailprüfung fehlgeschlagen: {exc}')
    enrich_report = st.session_state.get('category_enrich_report')
    if enrich_report:
        st.write(f"{enrich_report['checked']} Produktseiten geprüft · {enrich_report['found']} Artikelnummern erkannt · "
                 f"{enrich_report['saved']} im Index gespeichert")
        with st.expander('Detailprüfung – Diagnose'):
            for note in enrich_report['notes']:
                st.caption(note)

st.subheader('Artikelübersicht')
df=st.session_state.products.copy()

# The Edit link in the first table column selects a row by its current position.
# All edits are committed through save(), including GitHub persistence.
toggle_param = st.query_params.get('toggle_hidden', '')
if toggle_param != '':
    try:
        toggle_index=int(toggle_param)
        if toggle_index < 0 or toggle_index >= len(df):
            raise ValueError('Artikel nicht gefunden.')
        # Confirmation avoids unintended writes from opening a URL.
        target=df.iloc[toggle_index]
        current_hidden=str(target.get('hidden','')).strip().lower() in ('1','true','yes','ja')
        st.warning(f"Artikel {target['sku'] or target['description']} {'wieder einblenden' if current_hidden else 'ausblenden'}?")
        a,b=st.columns(2)
        with a:
            if st.button('Ja, Änderung speichern',type='primary'):
                try:
                    set_product_flag(target,'hidden',not current_hidden)
                    st.query_params.clear()
                    st.rerun()
                except Exception as exc:
                    st.error(f'Speichern fehlgeschlagen: {exc}')
        with b:
            if st.button('Abbrechen',key='cancel_hide'):
                st.query_params.clear()
                st.rerun()
    except (ValueError,TypeError):
        st.error('Ungültiger Artikel-Link.')
        if st.button('Zurück zur Übersicht'):
            st.query_params.clear()
            st.rerun()

delete_param=st.query_params.get('delete','')
if delete_param != '':
    try:
        delete_index=int(delete_param)
        if delete_index<0 or delete_index>=len(df): raise ValueError('Artikel nicht gefunden')
        item=df.iloc[delete_index]
        is_deleted=checked_status(item.get('deleted',''))
        st.warning(f"{'Wiederherstellen' if is_deleted else 'Aus dem Price-Crawler löschen'}: {item['sku'] or item['description']}?")
        st.caption('Gelöschte Odoo-Artikel werden bei einem erneuten Odoo-Import nicht wieder angelegt. In Odoo selbst wird nichts gelöscht.')
        a,b=st.columns(2)
        with a:
            if st.button('Ja, bestätigen',type='primary',key='confirm_delete'):
                try:
                    set_product_flag(item,'deleted',not is_deleted)
                    st.query_params.clear(); st.rerun()
                except Exception as exc: st.error(f'Löschen fehlgeschlagen: {exc}')
        with b:
            if st.button('Abbrechen',key='cancel_delete'):
                st.query_params.clear(); st.rerun()
    except (ValueError,TypeError):
        st.error('Ungültiger Löschlink.')

edit_param = st.query_params.get('edit', '')
if edit_param != '':
    try:
        edit_index = int(edit_param)
        if edit_index < 0 or edit_index >= len(df):
            raise ValueError('Artikel nicht gefunden.')
    except (ValueError, TypeError):
        st.warning('Der Bearbeitungslink ist ungültig.')
        if st.button('Zur Übersicht zurück'):
            st.query_params.clear()
            st.rerun()
    else:
        current = df.iloc[edit_index]
        st.markdown(f"#### ✏️ Artikel bearbeiten: {current['sku'] or current['description']}")
        with st.form(f'edit_product_{edit_index}'):
            e1, e2 = st.columns(2)
            with e1:
                new_sku = st.text_input('Hersteller-SKU', value=str(current['sku']))
                new_ean = st.text_input('EAN / Barcode', value=str(current['ean']))
                new_url = st.text_input('Brickmo-Produktlink', value=str(current['brickmo_url']))
                new_loot_url = st.text_input('Lootstore-Produktlink', value=str(current['lootstore_url']))
                new_lumi_url = st.text_input('Lumibricks-Produktlink (leer lassen zum Entfernen)', value=str(current['lumibricks_url']))
            with e2:
                new_desc = st.text_input('Beschreibung', value=str(current['description']))
                new_price = st.text_input('LBrixx-Verkaufspreis (€)', value=fmt(current['lbrixx_price']).replace(' €','') if euro_num(current['lbrixx_price']) is not None else '')
            new_hidden = st.checkbox('Artikel dauerhaft aus der Übersicht ausblenden', value=str(current.get('hidden','')).strip().lower() in ('1','true','yes','ja'))
            confirm = st.form_submit_button('💾 Änderungen speichern', type='primary')
        if st.button('Abbrechen'):
            st.query_params.clear()
            st.rerun()
        if confirm:
            cleaned_sku = new_sku.strip().upper()
            cleaned_url = new_url.strip().split('?',1)[0].split('#',1)[0].rstrip('/')
            cleaned_loot_url = new_loot_url.strip().split('?',1)[0].split('#',1)[0].rstrip('/')
            cleaned_lumi_url = new_lumi_url.strip().split('?',1)[0].split('#',1)[0].rstrip('/')
            new_price_value = euro_num(new_price)
            other = df.drop(df.index[edit_index])
            if not cleaned_sku:
                st.error('Bitte eine Hersteller-SKU eintragen.')
            elif cleaned_url and not valid_url(cleaned_url):
                st.error('Bitte eine gültige Brickmo-HTTPS-Produktadresse eintragen.')
            elif cleaned_loot_url and not valid_lootstore_url(cleaned_loot_url):
                st.error('Bitte eine gültige Lootstore-HTTPS-Produktadresse eintragen.')
            elif cleaned_lumi_url and not lumibricks_store.is_valid_product_url(cleaned_lumi_url):
                st.error('Bitte eine gültige Lumibricks-HTTPS-Produktadresse eintragen.')
            elif new_price.strip() and (new_price_value is None or new_price_value < 0):
                st.error('Der LBrixx-Preis muss eine gültige, nicht negative Zahl sein.')
            elif cleaned_sku in other.sku.str.upper().values:
                st.error('Diese Hersteller-SKU ist bereits vorhanden.')
            elif cleaned_loot_url and cleaned_loot_url in other.lootstore_url.values:
                st.error('Dieser Lootstore-Link ist bereits vorhanden.')
            elif cleaned_lumi_url and cleaned_lumi_url in other.lumibricks_url.values:
                st.error('Dieser Lumibricks-Link ist bereits vorhanden.')
            elif cleaned_url and cleaned_url in other.brickmo_url.values:
                st.error('Dieser Brickmo-Link ist bereits vorhanden.')
            else:
                updated = df.copy()
                updated.at[edit_index, 'hidden'] = '1' if new_hidden else ''
                updated.at[edit_index, 'ean'] = new_ean.strip()
                updated.at[edit_index, 'sku'] = cleaned_sku
                updated.at[edit_index, 'description'] = new_desc.strip()
                updated.at[edit_index, 'lbrixx_price'] = f'{new_price_value:.2f}' if new_price.strip() else ''
                updated.at[edit_index, 'brickmo_url'] = cleaned_url
                updated.at[edit_index, 'lootstore_url'] = cleaned_loot_url
                if cleaned_loot_url != str(current['lootstore_url']):
                    for field in ('lootstore_price','lootstore_shipping','lootstore_total','lootstore_availability','lootstore_checked','lootstore_error'):
                        updated.at[edit_index,field]=''
                # A changed URL invalidates the old shop price and availability until rechecked.
                if cleaned_url != str(current['brickmo_url']):
                    for field in ('brickmo_price', 'shipping', 'total', 'availability', 'last_checked', 'error', 'brickmo_photo'):
                        updated.at[edit_index, field] = ''
                lumi_changed = cleaned_lumi_url != str(current['lumibricks_url']).strip()
                try:
                    if lumi_changed:
                        lumibricks_store.set_link(DB_URL, str(current['db_id']), cleaned_lumi_url)
                    save_changed_rows(updated,[edit_index])
                    if new_price.strip() and new_price_value != euro_num(current['lbrixx_price']):
                        db_store.record_own_price(DB_URL, str(current['db_id']), new_price_value)
                    st.query_params.clear()
                    st.success('Artikel dauerhaft gespeichert.')
                    st.rerun()
                except Exception as exc:
                    st.error(f'Änderungen konnten nicht gespeichert werden: {exc}')
        st.markdown('##### 📈 Preisverlauf dieses Artikels')
        own_shipping=st.toggle('Versandkosten einbeziehen',value=False,key=f'edit_shipping_{edit_index}')
        if str(current.get('db_id','')).strip().isdigit():
            try:
                own_history=db_store.history(DB_URL,str(current['db_id']))
                if own_history.empty:
                    st.info('Noch keine gespeicherten Preisverläufe für diesen Artikel.')
                else:
                    own_history['Wert']=pd.to_numeric(own_history['price'],errors='coerce')
                    if own_shipping:
                        own_history['Wert']+=pd.to_numeric(own_history['shipping_cost'],errors='coerce').fillna(0)
                    own_chart=own_history.pivot_table(index='recorded_at',columns='merchant',values='Wert',aggfunc='last')
                    st.line_chart(own_chart)
            except Exception as exc:
                st.warning(f'Preisverlauf konnte nicht geladen werden: {exc}')

with st.expander('💡 Lumibricks – Artikel zuordnen und Preise prüfen', expanded=False):
    st.caption('Passende Artikelnummern werden automatisch einander gegenübergestellt. Du bestätigst direkt in der Trefferzeile; eine vorherige Artikelauswahl ist nicht nötig.')
    lumi_products=df.loc[df['brand_name'].astype(str).str.contains(r'lumibricks|funwhole',case=False,na=False) & ~df['deleted'].map(checked_status)].copy()
    labels={f"{r.sku} · {str(r.description)[:65]}":i for i,r in lumi_products.iterrows()}
    if not labels:
        st.info('Keine Lumibricks-/FunWhole-Artikel im Odoo-Sortiment gefunden.')
    else:
        if st.button('🔎 Lumibricks-SKU-Vorschläge suchen'):
            with st.spinner('Öffentlichen Lumibricks-Katalog lesen…'):
                catalog,diagnostics,count=lumibricks_store.catalog()
            suggestions=[]
            missing=[]
            conflicts=[]
            sku_diagnostic_candidates=[]
            for _,product in lumi_products.iterrows():
                if str(product.lumibricks_url).strip():
                    continue
                # Always inspect BOTH the primary Odoo SKU and every explicit
                # manufacturer reference. A hit must not short-circuit another SKU.
                # Odoo often stores an alternate code in the product name,
                # the SKU itself (e.g. "L9113 (18007)"), or lbrixx_sku.
                # Inspect all explicit reference fields, not only description.
                primary=str(product.get('sku', '') or '').strip()
                # `description` is the full Odoo product name imported from
                # product.product.name. It may begin with a numeric manufacturer
                # SKU: `17007 (L9118) Cyber Couture Galleria`.
                reference_fields=[str(product.get(field, '') or '') for field in
                                  ('sku', 'description', 'lbrixx_sku')]
                candidates=[]; seen_keys=set()
                def add_candidate(value):
                    key=lumibricks_store.sku_key(value)
                    if key and key not in seen_keys and re.fullmatch(r'[A-Z]{0,6}\d{3,8}',key):
                        seen_keys.add(key)
                        candidates.append(key)
                # Always search the primary number, independently of aliases.
                add_candidate(primary.split('(',1)[0].strip())
                for field_text in reference_fields:
                    # Parenthesized legacy numbers: (F9031), (17007).
                    for raw in re.findall(r'\(\s*([A-Za-z]{0,6}[- ]?\d{3,8})\s*\)',field_text):
                        add_candidate(raw.replace(' ','').replace('-',''))
                    # Explicitly labelled manufacturer numbers.
                    for raw in re.findall(r'(?i)\b(?:sku|artikelnummer|artikelnr\.?|ref(?:erenz)?\.?)\s*[:#-]?\s*([A-Za-z]{0,6}\d{3,8})\b',field_text):
                        add_candidate(raw)
                # The manufacturer SKU can be the FIRST token of the Odoo
                # product name, including numeric codes. Require a separator
                # and an additional name token, to avoid extracting piece counts.
                full_name=str(product.get('description','') or '').strip()
                leading=re.match(r'^([A-Za-z]{0,6}[- ]?\d{3,8})(?=\s*\(|\s+|\s*[-–—:])',full_name)
                if leading:
                    add_candidate(leading.group(1).replace(' ','').replace('-',''))
                # Odoo product names can put the legacy manufacturer's SKU
                # immediately AFTER the current SKU, without parentheses:
                # '14003 F9036 Western Freight Train'. Restrict extraction
                # to the second token and require an actual product title after
                # it, rather than scanning arbitrary numbers (piece counts).
                initial_codes=re.match(
                    r'^\s*([A-Za-z]{0,6}\d{3,8})\s+([A-Za-z]{1,6}\d{3,8})\s+(?=[A-Za-z])',
                    full_name,
                )
                if initial_codes:
                    add_candidate(initial_codes.group(1))
                    add_candidate(initial_codes.group(2))
                # lbrixx_sku is a dedicated SKU field and may be numeric.
                add_candidate(product.get('lbrixx_sku',''))
                # A SKU field containing "L9113 (18007)" also yields both.
                # Keep a diagnostic record of all candidate keys.
                sku_diagnostic_candidates.append(
                    f"{primary}: Odoo-Produktname im Crawler: {str(product.get('description','') or '')[:160]!r} "
                    f"| Suchschlüssel {', '.join(candidates) or '(keine)'}"
                )
                if candidates:
                    missing.append((product,candidates))
            # A single click checks all unlinked products. Keep progress and results
            # in the Streamlit session so rate-limit interruptions can be resumed.
            checked=0; targeted_hits=0; sku_diagnostics=sku_diagnostic_candidates
            search_cache=st.session_state.setdefault('lumi_sku_search_cache',{})
            import requests as _requests
            search_session=_requests.Session()
            stopped_reason=''
            if missing:
                progress=st.progress(0,text=f'Gezielte SKU-Nachsuche: 0/{len(missing)} Artikel')
                try:
                    last_request=0.0
                    for i,(product,codes) in enumerate(missing):
                        matches=[]
                        for code in codes:
                            key=lumibricks_store.sku_key(code)
                            # Catalog and targeted search are independent; test
                            # every distinct SKU even if another has a hit.
                            if key in catalog:
                                matches.append((code,catalog[key]))
                                sku_diagnostics.append(f'{product.sku} / {code}: Katalogtreffer')
                            if key not in search_cache:
                                delay=2.0-(time.monotonic()-last_request)
                                if delay>0: time.sleep(delay)
                                last_request=time.monotonic()
                                hit,reason=lumibricks_store.search_exact_sku(code,search_session)
                                search_cache[key]=(hit,reason)
                            hit,reason=search_cache[key]
                            sku_diagnostics.append(f'{product.sku} / {code}: {reason}')
                            if hit:
                                matches.append((code,hit))
                        checked+=1
                        unique={hit['url']:hit for _,hit in matches}
                        if len(unique)==1:
                            hit=next(iter(unique.values()))
                            suggestions.append((str(product.db_id),str(product.sku),hit['url'],hit['title']))
                            targeted_hits+=1
                        elif len(unique)>1:
                            conflicts.append((str(product.db_id),str(product.sku),matches))
                        progress.progress((i+1)/len(missing),text=f'Gezielte SKU-Nachsuche: {i+1}/{len(missing)} Artikel · {targeted_hits} Treffer')
                        # Save partial results after each completed product.
                        st.session_state['lumi_suggestions']=suggestions.copy()
                except Exception as exc:
                    stopped_reason=str(exc)
                    diagnostics.append(f'Gezielte Nachsuche unterbrochen nach {checked} Artikeln: {exc}. Beim nächsten Klick wird mit zwischengespeicherten SKU-Ergebnissen fortgesetzt.')
                finally:
                    search_session.close()
            diagnostics.append(f'Gezielte SKU-Nachsuche: {checked}/{len(missing)} noch nicht zugeordnete Artikel geprüft, {targeted_hits} zusätzliche Treffer')
            if sku_diagnostics:
                st.session_state['lumi_sku_diagnostics']=sku_diagnostics
            if st.session_state.get('lumi_sku_diagnostics'):
                with st.expander('🔬 Diagnose der einzelnen SKU-Prüfungen', expanded=False):
                    for line in st.session_state['lumi_sku_diagnostics']: st.text(line)
            st.session_state['lumi_suggestions']=suggestions
            st.session_state['lumi_conflicts']=conflicts
            st.info(f'{count} Shopprodukte geprüft · {len(suggestions)} neue SKU-Vorschläge')
            for message in diagnostics: st.warning(message)
        suggestions=st.session_state.get('lumi_suggestions',[])
        if suggestions:
            st.caption('Direkter SKU-Abgleich: LBrixx-Artikel → passender Lumibricks-Artikel. Jeden Treffer direkt bestätigen:')
            for pid,sku,url,title in suggestions[:100]:
                a,b=st.columns([5,1])
                with a: st.markdown(f'**LBrixx {sku}** → **Lumibricks {sku}** · {title[:75]} · [Produkt ansehen]({url})')
                with b:
                    if st.button('✓ Zuordnen',key=f'lumi_confirm_{pid}'):
                        try:
                            lumibricks_store.confirm(DB_URL,pid,url)
                            st.session_state['lumi_suggestions']=[s for s in suggestions if s[0]!=pid]
                            st.session_state.products=normalize(lumibricks_store.overlay(DB_URL,db_store.load(DB_URL)))
                            st.rerun()
                        except Exception as exc: st.error(str(exc))
        for pid,sku,match_list in st.session_state.get('lumi_conflicts',[]):
            st.warning(f'⚠️ LBrixx {sku}: Unterschiedliche Lumibricks-Produkte zu den geprüften Artikelnummern. Bitte manuell entscheiden:')
            unique_options={}
            for code,hit in match_list:
                unique_options.setdefault(hit['url'],(code,hit))
            for url,(code,hit) in unique_options.items():
                a,b=st.columns([5,1])
                with a:
                    st.markdown(f'**{code}** → {hit["title"][:75]} · [Produkt ansehen]({url})')
                with b:
                    if st.button('✓ Zuordnen',key=f'lumi_conflict_{pid}_{lumibricks_store.sku_key(code)}'):
                        try:
                            lumibricks_store.confirm(DB_URL,pid,url)
                            st.session_state['lumi_conflicts']=[c for c in st.session_state.get('lumi_conflicts',[]) if c[0]!=pid]
                            st.session_state.products=normalize(lumibricks_store.overlay(DB_URL,db_store.load(DB_URL)))
                            st.rerun()
                        except Exception as exc: st.error(str(exc))
        with st.expander("Manuelle Zuordnung / Einzelpreisprüfung", expanded=False):
            chosen_lumi=st.selectbox('LBrixx-Artikel',list(labels),key='lumi_article')
            lumi_idx=labels[chosen_lumi]
            lumi_row=df.loc[lumi_idx]
            lumi_url=st.text_input('Lumibricks-Produktlink',value=str(lumi_row.lumibricks_url),key=f'lumi_url_{lumi_idx}')
            if st.button('🔗 Lumibricks-Link bestätigen'):
                try:
                    lumibricks_store.confirm(DB_URL,lumi_row.db_id,lumi_url)
                    st.session_state.products=normalize(lumibricks_store.overlay(DB_URL,db_store.load(DB_URL)))
                    st.success('Link gespeichert.');st.rerun()
                except Exception as exc: st.error(str(exc))
            if st.button('💶 EUR-Preis des gewählten Artikels prüfen'):
                try:
                    with st.spinner('Lumibricks-EUR-Preis prüfen…'):
                        price=lumibricks_store.record(DB_URL,lumi_row.db_id,str(lumi_row.lumibricks_url))
                    st.session_state.products=normalize(lumibricks_store.overlay(DB_URL,db_store.load(DB_URL)))
                    st.success(f'EUR-Preis gespeichert: {fmt(price)} · Versand 0,00 €');st.rerun()
                except Exception as exc: st.error(f'Preis nicht übernommen: {exc}')
st.caption('Lumibricks-Preise werden nur bei eindeutigem EUR-Nachweis gespeichert. Bei abweichender Währung bleibt der letzte bestätigte Preis erhalten.')

# Hidden state is persisted in the existing GitHub CSV and retained during Odoo merges.
# Use the original dataframe index so sorting/filtering never changes edit targets.
# Exklusive Ansichten: ausgeblendete und geloeschte Artikel erscheinen NICHT
# gemeinsam mit aktiven Artikeln. Geloescht hat Vorrang vor ausgeblendet.
deleted_mask=df['deleted'].map(checked_status)
hidden_mask=df['hidden'].map(checked_status)
active_mask=~hidden_mask & ~deleted_mask
hidden_only_mask=hidden_mask & ~deleted_mask
view_options=['Aktive Artikel', '👁️ Ausgeblendete Artikel', '🗑️ Gelöschte Artikel']
view_col, brand_col = st.columns([2, 1], gap='medium')
with view_col:
    requested_view = st.query_params.get('view')
    if requested_view in view_options and st.session_state.get('_article_view_query') != requested_view:
        st.session_state['article_status_view'] = requested_view
        st.session_state['_article_view_query'] = requested_view
    article_view=st.radio(
        'Artikelstatus', view_options, horizontal=True,
        key='article_status_view',
        captions=[
            f'{int(active_mask.sum())} Artikel',
            f'{int(hidden_only_mask.sum())} Artikel',
            f'{int(deleted_mask.sum())} Artikel',
        ],
    )
if article_view == view_options[0]:
    display_mask=active_mask.copy()
elif article_view == view_options[1]:
    display_mask=hidden_only_mask.copy()
else:
    display_mask=deleted_mask.copy()
# Markenfilter beeinflusst nur die Darstellung, niemals die Datenbank.
brand_values=sorted(
    {str(v).strip() for v in df['brand_name'].fillna('') if str(v).strip()},
    key=str.casefold,
)
with brand_col:
    requested_brand = st.query_params.get('brand')
    available_brands = ['Alle Marken', 'Ohne Marke'] + brand_values
    if requested_brand in available_brands and st.session_state.get('_article_brand_query') != requested_brand:
        st.session_state['article_brand_filter'] = requested_brand
        st.session_state['_article_brand_query'] = requested_brand
    brand_selection=st.selectbox(
        'Markenfilter', ['Alle Marken', 'Ohne Marke'] + brand_values,
        key='article_brand_filter',
    )
if brand_selection == 'Ohne Marke':
    display_mask &= df['brand_name'].fillna('').astype(str).str.strip().eq('')
elif brand_selection != 'Alle Marken':
    display_mask &= df['brand_name'].fillna('').astype(str).str.strip().eq(brand_selection)
df_visible=df.loc[display_mask].copy()
st.caption(f'{len(df_visible)} Artikel in der aktuellen Ansicht · {int(active_mask.sum())} aktiv · {int(hidden_only_mask.sum())} ausgeblendet · {int(deleted_mask.sum())} gelöscht')

if df.empty: st.info('Noch keine Artikel vorhanden.')
else:
    sort_choices = {'EAN': 'ean',
        'Odoo-ID': 'odoo_id',
        'SKU': 'sku',
        'Marke': 'brand_name',
        'Beschreibung': 'description',
        'LBrixx-Preis': 'lbrixx_price',
        'Brickmo-Preis': 'brickmo_price',
        'Lootstore-Preis': 'lootstore_price',
        'Lumibricks-Preis': 'lumibricks_total',
        'Lootstore-Gesamtpreis': 'lootstore_total',
        'Preisdifferenz (LBrixx − Lootstore)': 'loot_difference',
        'Versand': 'shipping',
        'Brickmo-Gesamtpreis': 'total',
        'Preisdifferenz (LBrixx − Brickmo)': 'difference',
        'Verfügbarkeit': 'availability',
        'Letzte Preisprüfung': 'last_checked',
    }
    # Click a column heading to toggle sort direction.
    sort_field=st.query_params.get('sort','sku')
    if sort_field not in sort_choices.values(): sort_field='sku'
    sort_direction=st.query_params.get('dir','asc')
    if sort_direction not in ('asc','desc'): sort_direction='asc'
    st.caption('Kompaktansicht · Überschrift anklicken zum Sortieren · Aktionen: ✏️ Bearbeiten, 🚫 Ausblenden, 🗑️ Löschen. SKU und Artikelname bleiben beim Scrollen sichtbar.')
    df_display = df_visible.copy()
    numeric_fields = {'odoo_id', 'lbrixx_price', 'brickmo_price', 'shipping', 'total', 'lootstore_price', 'lootstore_total', 'difference'}
    if sort_field == 'loot_difference':
        df_display['_sort'] = df_display.apply(
            lambda r: (euro_num(r.lbrixx_price) - euro_num(r.lootstore_total))
            if euro_num(r.lbrixx_price) is not None and euro_num(r.lootstore_total) is not None
            else float('nan'), axis=1)
    elif sort_field == 'difference':
        df_display['_sort'] = df_display.apply(
            lambda r: (euro_num(r.lbrixx_price) - euro_num(r.total))
            if euro_num(r.lbrixx_price) is not None and euro_num(r.total) is not None
            else float('nan'), axis=1)
    elif sort_field in numeric_fields:
        df_display['_sort'] = df_display[sort_field].map(euro_num)
    else:
        df_display['_sort'] = df_display[sort_field].fillna('').astype(str).str.casefold()
        df_display.loc[df_display[sort_field].fillna('').astype(str).str.strip().eq(''), '_sort'] = None
    df_display = df_display.sort_values('_sort', ascending=(sort_direction == 'asc'),
                                        na_position='last', kind='stable')
    if df_display.empty:
        st.info('Keine Artikel in dieser Ansicht. Bitte Artikelstatus oder Markenfilter ändern.')
    # Render clickable, availability-colored Brickmo price cells as safe HTML.
    # st.dataframe does not reliably combine per-cell background styles with LinkColumn.
    def cell(v):
        return html.escape(str(v if v is not None else ''), quote=True)

    def price_cell(row,provider):
        if provider=='brickmo':
            status=str(row.availability).strip().lower()
            price=fmt(row.total)
            shipping=fmt(row.shipping)
            url=str(row.brickmo_url).strip()
            timestamp=row.last_checked
            valid=valid_url(url)
        elif provider=='lumibricks':
            status=str(row.lumibricks_availability).strip().lower()
            price=fmt(row.lumibricks_total)
            shipping=fmt(row.lumibricks_shipping)
            url=str(row.lumibricks_url).strip()
            timestamp=row.lumibricks_checked
            valid=lumibricks_store.valid_url(url)
        else:
            status=str(row.lootstore_availability).strip().lower()
            price=fmt(row.lootstore_total)
            shipping=fmt(row.lootstore_shipping)
            url=str(row.lootstore_url).strip()
            timestamp=row.lootstore_checked
            valid=valid_lootstore_url(url)
        css_class='available' if status=='lieferbar' else 'unavailable' if status=='nicht lieferbar' else 'unknown'
        label=cell(price)
        if not valid:
            # Missing product association is distinct from an unchecked/failed price query.
            return ('<td class="shop-price missing-association" title="Kein Produktlink zugeordnet">'
                    '<span class="missing-mark" aria-label="Keine Produktzuordnung">❗</span>'
                    '<div class="missing-label">Nicht zugeordnet</div></td>')
        if valid: label=f'<a href="{cell(url)}" target="_blank" rel="noopener noreferrer">{label} ↗</a>'
        err=str(row.error if provider=='brickmo' else row.lumibricks_error if provider=='lumibricks' else row.lootstore_error).strip()
        stamp=cell(short_date(timestamp))
        label=('❗ ' if err else '✓ ' if timestamp else '● ')+label
        shipping_label = f'<div class="shipping-note">inkl. {cell(shipping)} Versand</div>' if shipping != '–' else ''
        return f'<td class="shop-price {css_class}">{label}{shipping_label}<div class="timestamp">{stamp}</div></td>'

    rows=[]
    for row_idx, r in df_display.iterrows():
        # Odoo is the primary image source; Brickmo is only a fallback.
        photo=str(r.photo).strip()
        fallback=str(r.brickmo_photo).strip()
        chosen=photo if photo.startswith(('data:image/jpeg;base64,','data:image/png;base64,','https://')) else fallback
        img=f'<img src="{cell(chosen)}" alt="Produktfoto" loading="lazy">' if chosen.startswith(('data:image/jpeg;base64,','data:image/png;base64,','https://')) else '–'
        rows.append('<tr>' +
            f'<td class="edit"><div class="action-group"><a title="Bearbeiten" aria-label="Bearbeiten" href="?edit={row_idx}" target="_self">✏️</a> <a title="{"Einblenden" if checked_status(r.hidden) else "Ausblenden"}" aria-label="Sichtbarkeit ändern" href="?toggle_hidden={row_idx}" target="_self">{"👁️" if checked_status(r.hidden) else "🚫"}</a> <a title="{"Wiederherstellen" if checked_status(r.deleted) else "Löschen"}" aria-label="Löschen oder wiederherstellen" href="?delete={row_idx}" target="_self">{"↩️" if checked_status(r.deleted) else "🗑️"}</a></div></td>' +
            f'<td class="photo">{img}</td>' +
            f'<td>{cell(r.sku)}</td>' +
            f'<td>{cell(r.brand_name)}</td>' +
            f'<td>{cell(r.ean)}</td>' +
            f'<td>{cell(r.lbrixx_sku)}</td>' +
            f'<td class="description">{cell(r.description)}</td>' +
            f'<td>{cell(fmt(r.lbrixx_price))}</td>' +
            price_cell(r,'brickmo') +
            price_cell(r,'lootstore') +
            price_cell(r,'lumibricks') +
            '</tr>')
    table_css = """
    <style>
    .lbrixx-scroll {overflow:auto; max-height:76vh; width:100%; border:1px solid rgba(128,128,128,.28); border-radius:8px; isolation:isolate;}
    .lbrixx-table {width:max-content; min-width:100%; border-collapse:separate; border-spacing:0; font-size:12px; line-height:1.3; color:#F5F7FB !important;}
    .lbrixx-table th {position:sticky; top:0; z-index:5; text-align:left; font-weight:700; padding:9px 8px; background:#365779; color:#fff; white-space:nowrap; border-bottom:3px solid #e6a35c; box-shadow:inset 0 1px 0 rgba(255,255,255,.12);}
    .lbrixx-table td {padding:5px 8px; border-bottom:1px solid rgba(128,128,128,.2); vertical-align:middle; background:#141821; color:#F5F7FB !important;}
    .lbrixx-table td:not(.shop-price) a {color:#F5F7FB !important;}
    .lbrixx-table tr:nth-child(even) td:not(.shop-price) {background:#1c222d;}
    .lbrixx-table tr:hover td:not(.shop-price) {background:#313c4d;}
    .lbrixx-table .edit {min-width:102px; width:102px; position:sticky; left:0; z-index:3;}
    .lbrixx-table th:nth-child(1) {position:sticky;left:0;z-index:9;min-width:102px;width:102px;}
    .lbrixx-table th:nth-child(2) {position:sticky;left:102px;z-index:8;min-width:54px;width:54px;}
    .lbrixx-table td:nth-child(2) {position:sticky;left:102px;z-index:3;min-width:54px;width:54px;}
    .lbrixx-table th:nth-child(3) {position:sticky;left:156px;z-index:8;min-width:90px;width:90px;}
    .lbrixx-table td:nth-child(3) {position:sticky;left:156px;z-index:3;min-width:90px;width:90px;font-weight:650;}
    .lbrixx-table th:nth-child(7) {position:sticky;left:246px;z-index:8;min-width:240px;width:240px;}
    .lbrixx-table td:nth-child(7) {position:sticky;left:246px;z-index:3;min-width:240px;width:240px;max-width:240px;white-space:normal;}
    .lbrixx-table .action-group {display:flex;align-items:center;gap:3px;}
    .lbrixx-table .edit a {display:inline-flex;align-items:center;justify-content:center; width:28px;height:27px; border:1px solid rgba(128,128,128,.5); border-radius:5px; text-decoration:none; color:inherit; font-size:14px;}
    .lbrixx-table .edit a:hover {background:rgba(128,128,128,.25);}
    .lbrixx-table .photo img {width:43px;height:43px;object-fit:contain;border-radius:4px;background:white;}
    .lbrixx-table .shop-price {font-weight:700;white-space:nowrap;min-width:105px;}
    .lbrixx-table .shop-price.missing-association {background:#fff1d8;color:#8c3300 !important;text-align:center;min-width:105px;}
    .lbrixx-table .missing-mark {display:block;font-size:30px;line-height:1.15;font-weight:900;}
    .lbrixx-table .missing-label {font-size:10px;font-weight:750;white-space:nowrap;}
    .lbrixx-table .shop-price.available {background:#B7E1A1;color:#173B20 !important;}
    .lbrixx-table .shop-price.unknown {background:#006CB7;color:#fff !important;}
    .lbrixx-table .shop-price.unavailable {background:#D01012;color:#fff !important;}
    .lbrixx-table .shop-price a {color:inherit;text-decoration:underline;text-underline-offset:3px;}
    .lbrixx-table .shop-price .shipping-note {font-size:10px;font-weight:500;margin-top:3px;opacity:.95;}
    .lbrixx-table .shop-price .timestamp {font-size:10px;font-weight:400;margin-top:2px;opacity:.9;}
    .lbrixx-table th a:hover {text-decoration:underline!important;}
    </style>
    """
    headings=['Aktionen','Foto','SKU','Marke','EAN','LBrixx-Nr.','Beschreibung','LBrixx-Preis','Brickmo-Preis inkl. Versand ↗','Lootstore-Preis inkl. Versand ↗','Lumibricks-Preis inkl. Versand ↗']
    sort_fields=['','', 'sku','brand_name','ean','lbrixx_sku','description','lbrixx_price','total','lootstore_total','lumibricks_total']
    def heading(h,k):
        if not k: return f'<th>{cell(h)}</th>'
        next_dir='desc' if sort_field==k and sort_direction=='asc' else 'asc'
        arrow=(' ▲' if sort_direction=='asc' else ' ▼') if sort_field==k else ''
        query = urlencode({'sort': k, 'dir': next_dir, 'view': article_view, 'brand': brand_selection})
        return f'<th><a href="?{html.escape(query, quote=True)}" target="_self" style="color:inherit;text-decoration:none">{cell(h)}{arrow}</a></th>'
    table_html=table_css+'<div class="lbrixx-scroll"><table class="lbrixx-table"><thead><tr>'+''.join(heading(h,k) for h,k in zip(headings,sort_fields))+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>'
    # Refresh controls directly above the three competitor price columns.
    # Streamlit widgets cannot be embedded in the sortable HTML table itself.
    st.markdown('#### Preise aktualisieren')
    st.caption('Nur Artikel mit hinterlegtem Produktlink werden abgefragt. Mindestens 2 Sekunden zwischen Shop-Anfragen; HTTP 429 stoppt die jeweilige Serie. Zeitangaben UTC.')
    left, a, b, c = st.columns([5, 1.25, 1.25, 1.25], gap='small')
    with left:
        st.download_button('⬇️ CSV exportieren',data=df.to_csv(index=False).encode('utf-8-sig'),file_name='lbrixx_preisvergleich.csv',mime='text/csv')
    with a: brickmo_update=st.button('🔄 Brickmo',use_container_width=True)
    with b: loot_update=st.button('🔄 Lootstore',use_container_width=True)
    with c: lumi_update=st.button('🔄 Lumibricks',use_container_width=True)
    st.html(table_html)
    st.caption('Preisfelder: Hellgrün = lieferbar · Blau = unbestätigt · Rot = nicht lieferbar · ❗ auf hellem Grund = kein Shop-Produktlink zugeordnet. Zeitstempel in UTC.')
    active=df.loc[~deleted_mask & ~hidden_mask]
    selectable={f"{r.sku or r.odoo_id} · {str(r.description)[:65]}":int(i) for i,r in active.iterrows()}
    chosen=st.selectbox('Einzelnen Artikel aktualisieren',options=['Bitte Artikel wählen']+list(selectable.keys()))
    provider=st.selectbox('Anbieter für Einzelaktualisierung',options=['Brickmo','Lootstore','Lumibricks','Brickmo und Lootstore','Alle drei'])
    one_update=st.button('🔄 Gewählten Artikel aktualisieren')
    if lumi_update:
        lumi_active=df.loc[~deleted_mask & ~hidden_mask]
        lumi_rows=[r for _,r in lumi_active.iterrows() if str(r.lumibricks_url).strip() and str(r.db_id).strip()]
        if not lumi_rows:
            st.info('Lumibricks: Keine aktiven Artikel mit hinterlegtem Produktlink.')
        else:
            st.write(f'Lumibricks: {len(lumi_rows)} Produktseiten werden geprüft.')
            lumi_progress=st.progress(0)
            lumi_ok=lumi_failed=0
            for pos,r in enumerate(lumi_rows):
                try:
                    lumibricks_store.record(DB_URL,r.db_id,str(r.lumibricks_url))
                    lumi_ok+=1
                except Exception as exc:
                    lumi_failed+=1
                    st.warning(f'Lumibricks {r.sku}: {exc}')
                    if '429' in str(exc):
                        st.warning('Lumibricks: HTTP 429 – Serie abgebrochen.')
                        lumi_progress.progress((pos+1)/len(lumi_rows))
                        break
                lumi_progress.progress((pos+1)/len(lumi_rows))
                if pos+1<len(lumi_rows): time.sleep(2)
            st.success(f'Lumibricks: {lumi_ok} erfolgreich, {lumi_failed} fehlgeschlagen. Preise direkt in PostgreSQL gespeichert.')
            try:
                st.session_state.products=normalize(lumibricks_store.overlay(DB_URL,db_store.load(DB_URL)))
            except Exception as exc:
                st.warning(f'Preise gespeichert, aber Ansicht konnte nicht neu geladen werden: {exc}')
            st.rerun()
    if brickmo_update or loot_update or one_update:
        new=df.copy()
        shops=['brickmo'] if brickmo_update else ['lootstore'] if loot_update else ['brickmo','lootstore','lumibricks'] if provider=='Alle drei' else ['brickmo','lootstore'] if provider=='Brickmo und Lootstore' else [provider.lower()]
        if one_update and chosen=='Bitte Artikel wählen':
            st.warning('Bitte zunächst einen Artikel auswählen.')
        else:
            changed_indices=[]
            for shop in shops:
                url_field={'brickmo':'brickmo_url','lootstore':'lootstore_url','lumibricks':'lumibricks_url'}[shop]
                subset=[selectable[chosen]] if one_update else list(active.index)
                indices=[i for i in subset if str(new.at[i,url_field]).strip()]
                if not indices:
                    st.info(f'{shop}: Keine hinterlegten Links für die Auswahl.')
                    continue
                st.write(f'{shop.capitalize()}: {len(indices)} Produktseiten werden geprüft.')
                if shop=='lumibricks':
                    ok=failed=0
                    for i in indices:
                        try:
                            lumibricks_store.record(DB_URL,new.at[i,'db_id'],str(new.at[i,'lumibricks_url']))
                            ok+=1
                        except Exception as exc:
                            failed+=1
                            st.warning(f'Lumibricks {new.at[i,"sku"]}: {exc}')
                    # Lumibricks persists its own current prices and history in PostgreSQL.
                else:
                    new,ok,failed=update_prices(new,shop,indices)
                    changed_indices.extend(indices)
                st.write(f'{shop.capitalize()}: {ok} erfolgreich, {failed} fehlgeschlagen.')
            try:
                if changed_indices: save_changed_rows(new,changed_indices)
                st.session_state.products=normalize(lumibricks_store.overlay(DB_URL,db_store.load(DB_URL)))
                st.success('Preisprüfung gespeichert.'); st.rerun()
            except Exception as exc: st.error(f'Speichern fehlgeschlagen: {exc}')

    st.caption('Zum Bearbeiten, Ausblenden oder Löschen bitte die Aktionen direkt am Anfang der jeweiligen Tabellenzeile verwenden.')

with st.expander('📥 Artikel aus CSV importieren'):
    file=st.file_uploader('CSV mit mindestens brickmo_url, optional sku, lbrixx_sku, description, lbrixx_price',type='csv')
    if file and st.button('CSV importieren'):
        try:
            incoming=pd.read_csv(file,dtype=str,keep_default_na=False,encoding='utf-8-sig')
            if 'brickmo_url' not in incoming.columns: raise ValueError('Spalte brickmo_url fehlt.')
            if not all(not u.strip() or valid_url(u) for u in incoming.brickmo_url): raise ValueError('Mindestens eine URL ist ungültig.')
            incoming=normalize(incoming)
            combined=pd.concat([st.session_state.products,incoming],ignore_index=True)
            # Keep every distinct Odoo article; deduplicate only non-empty matching Odoo IDs.
            ids=combined.odoo_id.astype(str).str.strip()
            combined=combined.loc[~(ids.ne('') & ids.duplicated(keep='first'))]
            save(combined); st.success('CSV importiert. Jetzt „Alle Preise aktualisieren“ klicken.'); st.rerun()
        except Exception as e: st.error(str(e))

with st.expander('📈 Preisverlauf je Artikel'):
    available=st.session_state.products
    choices={f"{r.sku or r.odoo_id} · {str(r.description)[:65]}":str(r.db_id) for _,r in available.iterrows() if str(r.db_id).strip()}
    if choices:
        selected=st.selectbox('Produkt für Preisverlauf',list(choices),key='history_product')
        include_shipping=st.toggle('Versandkosten einbeziehen',value=False)
        try:
            history=db_store.history(DB_URL,choices[selected])
            if history.empty:
                st.info('Noch keine historischen Preisabfragen vorhanden. Der Verlauf beginnt mit der nächsten erfolgreichen Abfrage.')
            else:
                history['Wert']=history['price'].astype(float)
                if include_shipping: history['Wert']+=history['shipping_cost'].fillna(0).astype(float)
                chart=history.pivot_table(index='recorded_at',columns='merchant',values='Wert',aggfunc='last')
                st.line_chart(chart)
                st.dataframe(history.sort_values('recorded_at',ascending=False),hide_index=True,use_container_width=True)
        except Exception as exc: st.error(f'Preisverlauf nicht verfügbar: {exc}')
    else: st.info('Zuerst Produkte importieren.')

with st.expander('🗄️ PostgreSQL-Verbindung'):
    st.write('Datenspeicherung: Supabase PostgreSQL')
    st.caption('Die Verbindung erfolgt serverseitig über SUPABASE_DB_URL. Keine Zugangsdaten in GitHub hinterlegen.')
