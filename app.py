import os, re, io, csv, json, base64, hashlib, html, time
from datetime import datetime, timezone
from urllib.parse import urlparse
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
import db_store

st.set_page_config(page_title='LBrixx Preisvergleich', layout='wide', page_icon='🧱')
DATA_FILE = 'lbrixx_products.csv'
COLUMNS = ['db_id','ean','odoo_id','hidden','deleted','sku','lbrixx_sku','description','lbrixx_price','brickmo_url','photo','brickmo_photo','brickmo_price','shipping','total','availability','last_checked','error','lootstore_url','lootstore_price','lootstore_shipping','lootstore_total','lootstore_availability','lootstore_checked','lootstore_error']
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
    return db_store.load(DB_URL),None

def normalize(df):
    for c in COLUMNS:
        if c not in df.columns: df[c]=''
    return df[COLUMNS].fillna('').astype(str).reset_index(drop=True)

def save(df):
    stored=db_store.save(DB_URL,normalize(df))
    st.session_state.products=normalize(stored)

def euro_num(x):
    if x is None or str(x).strip()=='': return None
    s=str(x).strip().replace('€','').replace(' ','').replace('\xa0','')
    if ',' in s: s=s.replace('.','').replace(',','.')
    try: return round(float(s),2)
    except ValueError: return None

def fmt(x):
    n=euro_num(x)
    return f'{n:,.2f} €'.replace(',','X').replace('.',',').replace('X','.') if n is not None else '–'

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

st.title('🧱 LBrixx – Preisvergleich V28 · PostgreSQL')
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
    payload={'domain':[['active','=',True]],'fields':['id','default_code','name','list_price','barcode','image_128'],'limit':limit,'offset':offset,'order':'id asc','context':{'lang':'de_DE'}}
    response=requests.post(endpoint,headers=headers,json=payload,timeout=40)
    if not response.ok:
        raise RuntimeError(f'Odoo HTTP {response.status_code}: API-Zugriff fehlgeschlagen. Prüfe Tarif, Datenbankname und Berechtigungen.')
    result=response.json()
    if isinstance(result,dict) and result.get('error'):
        raise RuntimeError('Odoo meldet einen API-Fehler. Bitte Zugriffsrechte und Tarif prüfen.')
    if not isinstance(result,list):
        raise ValueError('Unerwartete Antwort der Odoo-API.')
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
    import_scope=st.radio('Importumfang', ['5 Testartikel', '100 Artikel', 'Alle Artikel'], horizontal=True, index=0)
    col_test,col_import=st.columns(2)
    with col_test:
        test_odoo=st.button('🔍 Odoo-Verbindung testen',use_container_width=True)
    with col_import:
        import_odoo=st.button('📥 Odoo-Produkte importieren / aktualisieren',use_container_width=True)
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
            max_items={'5 Testartikel':5,'100 Artikel':100,'Alle Artikel':10000}[import_scope]
            for offset in range(0,max_items,100):
                batch_limit=min(100,max_items-offset)
                batch=odoo_read(limit=batch_limit,offset=offset)
                all_products.extend(batch)
                if len(batch)<batch_limit: break
            if max_items==10000 and len(all_products)==10000:
                st.warning('Import auf 10.000 Produkte begrenzt.')
            merged,added,updated,skipped=odoo_merge(st.session_state.products,all_products)
            save(merged)
            st.success(f'Odoo-Synchronisierung gespeichert: {added} neu, {updated} aktualisiert, {skipped} übersprungen. Wettbewerberlinks bleiben erhalten.')
            st.rerun()
        except Exception as exc:
            st.error(f'Odoo-Import fehlgeschlagen: {exc}')


st.caption('Brickmo und Lootstore · Einzelartikelvergleich nach Deutschland · Versandkosten je Händler')
st.success('🗄️ PostgreSQL verbunden – Produktdaten werden in Supabase gespeichert.')

with st.expander('➕ Brickmo-Artikel hinzufügen',expanded=False):
    with st.form('add_item',clear_on_submit=False):
        a,b=st.columns(2)
        with a:
            url=st.text_input('Brickmo-Produktlink *',placeholder='https://www.brickmo.com/...')
            sku=st.text_input('Hersteller-SKU (optional, wird sonst aus Link ermittelt)')
            ownsku=st.text_input('LBrixx-Artikelnummer (optional)')
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
                            row.update({'sku':key,'lbrixx_sku':ownsku.strip(),'description':name.strip() or result['description'],'lbrixx_price':str(euro_num(ownprice) or '') if ownprice.strip() else '', 'brickmo_url':url.strip()})
                            save(pd.concat([df,pd.DataFrame([row])],ignore_index=True)); st.success('Artikel hinzugefügt und gespeichert.'); st.rerun()
                    except Exception as e: st.error(f'Artikel konnte nicht automatisch erfasst werden: {e}')

with st.expander('🔗 Lootstore-Beispiellink F9043 zuordnen'):
    st.caption('Verknüpft den bereitgestellten Lootstore-Link mit dem vorhandenen Artikel F9043/FO9043. Andere Links bleiben unverändert.')
    if st.button('Lootstore-Link für F9043 speichern'):
        current=st.session_state.products.copy()
        matches=current.index[current.sku.str.upper().isin(['F9043','FO9043'])].tolist()
        if len(matches)!=1:
            st.error('Der Artikel F9043/FO9043 wurde nicht eindeutig gefunden. Bitte Link über Edit eintragen.')
        else:
            i=matches[0]
            current.at[i,'lootstore_url']='https://lootstore.de/products/funwhole-lumibricks-f9043-cyberpunk-police-patrol-car'
            for f in ('lootstore_price','lootstore_shipping','lootstore_total','lootstore_availability','lootstore_checked','lootstore_error'):
                current.at[i,f]=''
            try:
                save(current); st.success('Lootstore-Link gespeichert. Nun den Artikel aktualisieren.'); st.rerun()
            except Exception as exc: st.error(str(exc))

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
                changed=df.copy()
                changed.at[toggle_index,'hidden']='' if current_hidden else '1'
                try:
                    save(changed)
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
                changed=df.copy()
                changed.at[delete_index,'deleted']='' if is_deleted else '1'
                try:
                    save(changed)
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
                new_ownsku = st.text_input('LBrixx-Artikelnummer', value=str(current['lbrixx_sku']))
                new_ean = st.text_input('EAN / Barcode', value=str(current['ean']))
                new_url = st.text_input('Brickmo-Produktlink', value=str(current['brickmo_url']))
                new_loot_url = st.text_input('Lootstore-Produktlink', value=str(current['lootstore_url']))
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
            cleaned_url = new_url.strip()
            new_price_value = euro_num(new_price)
            other = df.drop(df.index[edit_index])
            if not cleaned_sku:
                st.error('Bitte eine Hersteller-SKU eintragen.')
            elif cleaned_url and not valid_url(cleaned_url):
                st.error('Bitte eine gültige Brickmo-HTTPS-Produktadresse eintragen.')
            elif new_loot_url.strip() and not valid_lootstore_url(new_loot_url):
                st.error('Bitte eine gültige Lootstore-HTTPS-Produktadresse eintragen.')
            elif new_price.strip() and (new_price_value is None or new_price_value < 0):
                st.error('Der LBrixx-Preis muss eine gültige, nicht negative Zahl sein.')
            elif cleaned_sku in other.sku.str.upper().values:
                st.error('Diese Hersteller-SKU ist bereits vorhanden.')
            elif new_loot_url.strip() and new_loot_url.strip() in other.lootstore_url.values:
                st.error('Dieser Lootstore-Link ist bereits vorhanden.')
            elif cleaned_url and cleaned_url in other.brickmo_url.values:
                st.error('Dieser Brickmo-Link ist bereits vorhanden.')
            else:
                updated = df.copy()
                updated.at[edit_index, 'hidden'] = '1' if new_hidden else ''
                updated.at[edit_index, 'ean'] = new_ean.strip()
                updated.at[edit_index, 'sku'] = cleaned_sku
                updated.at[edit_index, 'lbrixx_sku'] = new_ownsku.strip()
                updated.at[edit_index, 'description'] = new_desc.strip()
                updated.at[edit_index, 'lbrixx_price'] = f'{new_price_value:.2f}' if new_price.strip() else ''
                updated.at[edit_index, 'brickmo_url'] = cleaned_url
                updated.at[edit_index, 'lootstore_url'] = new_loot_url.strip()
                if new_loot_url.strip() != str(current['lootstore_url']):
                    for field in ('lootstore_price','lootstore_shipping','lootstore_total','lootstore_availability','lootstore_checked','lootstore_error'):
                        updated.at[edit_index,field]=''
                # A changed URL invalidates the old shop price and availability until rechecked.
                if cleaned_url != str(current['brickmo_url']):
                    for field in ('brickmo_price', 'shipping', 'total', 'availability', 'last_checked', 'error', 'brickmo_photo'):
                        updated.at[edit_index, field] = ''
                try:
                    save(updated)
                    st.query_params.clear()
                    st.success('Artikel dauerhaft gespeichert.')
                    st.rerun()
                except Exception as exc:
                    st.error(f'Änderungen konnten nicht gespeichert werden: {exc}')
# Hidden state is persisted in the existing GitHub CSV and retained during Odoo merges.
# Use the original dataframe index so sorting/filtering never changes edit targets.
show_hidden=st.checkbox('👁️ Ausgeblendete Artikel anzeigen',value=False,key='show_hidden')
show_deleted=st.checkbox('🗑️ Gelöschte Artikel zur Wiederherstellung anzeigen',value=False,key='show_deleted')
deleted_mask=df['deleted'].map(checked_status)
hidden_mask=df['hidden'].astype(str).str.strip().str.lower().isin(['1','true','yes','ja'])
st.caption(f'{int((~hidden_mask & ~deleted_mask).sum())} sichtbare Artikel · {int((hidden_mask & ~deleted_mask).sum())} ausgeblendet · {int(deleted_mask.sum())} gelöscht')
display_mask=pd.Series(True,index=df.index)
if not show_hidden: display_mask &= ~hidden_mask
if not show_deleted: display_mask &= ~deleted_mask
df_visible=df.loc[display_mask].copy()

if df.empty: st.info('Noch keine Artikel vorhanden.')
else:
    sort_choices = {'EAN': 'ean',
        'Odoo-ID': 'odoo_id',
        'SKU': 'sku',
        'LBrixx-Artikelnummer': 'lbrixx_sku',
        'Beschreibung': 'description',
        'LBrixx-Preis': 'lbrixx_price',
        'Brickmo-Preis': 'brickmo_price',
        'Lootstore-Preis': 'lootstore_price',
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
    st.caption('Spaltenüberschrift anklicken: aufsteigend / absteigend sortieren.')
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
        st.info('Keine sichtbaren Artikel. Aktiviere „Ausgeblendete Artikel anzeigen“, um Artikel wieder einzublenden.')
    # Render clickable, availability-colored Brickmo price cells as safe HTML.
    # st.dataframe does not reliably combine per-cell background styles with LinkColumn.
    def cell(v):
        return html.escape(str(v if v is not None else ''), quote=True)

    def price_cell(row,provider):
        if provider=='brickmo':
            status=str(row.availability).strip().lower()
            price=fmt(row.brickmo_price)
            url=str(row.brickmo_url).strip()
            timestamp=row.last_checked
            valid=valid_url(url)
        else:
            status=str(row.lootstore_availability).strip().lower()
            price=fmt(row.lootstore_price)
            url=str(row.lootstore_url).strip()
            timestamp=row.lootstore_checked
            valid=valid_lootstore_url(url)
        css_class='available' if status=='lieferbar' else 'unavailable' if status=='nicht lieferbar' else 'unknown'
        label=cell(price)
        if valid: label=f'<a href="{cell(url)}" target="_blank" rel="noopener noreferrer">{label} ↗</a>'
        err=str(row.error if provider=='brickmo' else row.lootstore_error).strip()
        stamp=cell(short_date(timestamp))
        label=('❗ ' if err else '✓ ' if timestamp else '● ')+label
        return f'<td class="shop-price {css_class}">{label}<div class="timestamp">{stamp}</div></td>'

    rows=[]
    for row_idx, r in df_display.iterrows():
        # Odoo is the primary image source; Brickmo is only a fallback.
        photo=str(r.photo).strip()
        fallback=str(r.brickmo_photo).strip()
        chosen=photo if photo.startswith(('data:image/jpeg;base64,','data:image/png;base64,','https://')) else fallback
        img=f'<img src="{cell(chosen)}" alt="Produktfoto" loading="lazy">' if chosen.startswith(('data:image/jpeg;base64,','data:image/png;base64,','https://')) else '–'
        a=euro_num(r.lbrixx_price); b=euro_num(r.total)
        diff=fmt(round(a-b,2)) if a is not None and b is not None else '–'
        c=euro_num(r.lootstore_total)
        loot_diff=fmt(round(a-c,2)) if a is not None and c is not None else '–'
        rows.append('<tr>' +
            f'<td class="edit"><a href="?edit={row_idx}" target="_self">✏️ Edit</a> <a href="?toggle_hidden={row_idx}" target="_self">{"👁️ Einblenden" if checked_status(r.hidden) else "🚫 Ausblenden"}</a> <a href="?delete={row_idx}" target="_self">{"↩️ Wiederherstellen" if checked_status(r.deleted) else "🗑️ Löschen"}</a></td>' +
            f'<td class="photo">{img}</td>' +
            f'<td>{cell(r.sku)}</td>' +
            f'<td>{cell(r.ean)}</td>' +
            f'<td>{cell(r.lbrixx_sku)}</td>' +
            f'<td class="description">{cell(r.description)}</td>' +
            f'<td>{cell(fmt(r.lbrixx_price))}</td>' +
            price_cell(r,'brickmo') +
            f'<td>{cell(fmt(r.shipping))}</td>' +
            f'<td>{cell(fmt(r.total))}</td>' +
            price_cell(r,'lootstore') +
            f'<td>{cell(fmt(r.lootstore_shipping))}</td>' +
            f'<td>{cell(fmt(r.lootstore_total))}</td>' +
            f'<td>{cell(diff)}</td>' +
            f'<td>{cell(loot_diff)}</td>' +
            '</tr>')
    table_css = """
    <style>
    .lbrixx-scroll {overflow:auto; max-height:70vh; width:100%; border:1px solid rgba(128,128,128,.25); border-radius:10px;}
    .lbrixx-table {width:100%; border-collapse:collapse; font-size:14px; min-width:1450px;}
    .lbrixx-table th {position:sticky;top:0;z-index:2;text-align:left; font-weight:650; padding:12px 10px; background:rgba(128,128,128,.13); white-space:nowrap;}
    .lbrixx-table td {padding:10px; border-top:1px solid rgba(128,128,128,.2); vertical-align:middle;}
    .lbrixx-table tr:hover td:not(.shop-price) {background:rgba(128,128,128,.06);}
    .lbrixx-table .edit a {display:inline-block; padding:7px 9px; border:1px solid rgba(128,128,128,.45); border-radius:7px; white-space:nowrap; text-decoration:none; color:inherit; font-weight:600;}\n    .lbrixx-table .edit a:hover {background:rgba(128,128,128,.16);}\n    .lbrixx-table .photo img {width:65px; height:65px; object-fit:contain; border-radius:5px;}
    .lbrixx-table .description {min-width:220px;}
    .lbrixx-table .checked {font-size:12px; white-space:nowrap;}
    .lbrixx-table .shop-price {font-weight:700; white-space:nowrap; min-width:115px;}
    .lbrixx-table .shop-price.available {background:#B7E1A1; color:#173B20;}
    .lbrixx-table .shop-price.unknown {background:#006CB7; color:#FFFFFF;}
    .lbrixx-table .shop-price.unavailable {background:#D01012; color:#FFFFFF;}
    .lbrixx-table .shop-price a {color:inherit; text-decoration:underline; text-underline-offset:3px;}
    .lbrixx-table .shop-price .timestamp {font-size:11px; font-weight:400; margin-top:5px; opacity:.9;}
    </style>
    """
    headings=['Aktionen','Foto','SKU','EAN','LBrixx-Nr.','Beschreibung','LBrixx-Preis','Brickmo-Preis ↗','Versand','Gesamtpreis','Lootstore-Preis ↗','Versand','Gesamtpreis','Differenz LBrixx − Brickmo','Differenz LBrixx − Lootstore']
    sort_fields=['','', 'sku','ean','lbrixx_sku','description','lbrixx_price','brickmo_price','shipping','total','lootstore_price','lootstore_shipping','lootstore_total','difference','loot_difference']
    def heading(h,k):
        if not k: return f'<th>{cell(h)}</th>'
        next_dir='desc' if sort_field==k and sort_direction=='asc' else 'asc'
        arrow=(' ▲' if sort_direction=='asc' else ' ▼') if sort_field==k else ''
        return f'<th><a href="?sort={k}&dir={next_dir}" target="_self" style="color:inherit;text-decoration:none">{cell(h)}{arrow}</a></th>'
    table_html=table_css+'<div class="lbrixx-scroll"><table class="lbrixx-table"><thead><tr>'+''.join(heading(h,k) for h,k in zip(headings,sort_fields))+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>'
    st.html(table_html)
    st.caption('Preisfelder: LEGO-Hellgrün = lieferbar · LEGO-Blau = unbestätigt · LEGO-Rot = nicht lieferbar. Zeitstempel in UTC. Preis anklicken öffnet den Shop.')
    st.markdown('#### Preise aktualisieren')
    st.caption('Nur Artikel mit hinterlegtem Produktlink werden abgefragt. Zwischen Shop-Anfragen liegen mindestens 2 Sekunden. HTTP 429 stoppt die jeweilige Serie. Zeitangaben sind UTC.')
    a,b,c=st.columns([1,1,1])
    with a: brickmo_update=st.button('🔄 Brickmo komplett',use_container_width=True)
    with b: loot_update=st.button('🔄 Lootstore komplett',use_container_width=True)
    with c: st.download_button('⬇️ CSV exportieren',data=df.to_csv(index=False).encode('utf-8-sig'),file_name='lbrixx_preisvergleich.csv',mime='text/csv',use_container_width=True)
    active=df.loc[~deleted_mask & ~hidden_mask]
    selectable={f"{r.sku or r.odoo_id} · {str(r.description)[:65]}":int(i) for i,r in active.iterrows()}
    chosen=st.selectbox('Einzelnen Artikel aktualisieren',options=['Bitte Artikel wählen']+list(selectable.keys()))
    provider=st.selectbox('Anbieter für Einzelaktualisierung',options=['Brickmo','Lootstore','Beide'])
    one_update=st.button('🔄 Gewählten Artikel aktualisieren')
    if brickmo_update or loot_update or one_update:
        new=df.copy()
        shops=['brickmo'] if brickmo_update else ['lootstore'] if loot_update else ['brickmo','lootstore'] if provider=='Beide' else [provider.lower()]
        if one_update and chosen=='Bitte Artikel wählen':
            st.warning('Bitte zunächst einen Artikel auswählen.')
        else:
            for shop in shops:
                url_field='brickmo_url' if shop=='brickmo' else 'lootstore_url'
                subset=[selectable[chosen]] if one_update else list(active.index)
                indices=[i for i in subset if str(new.at[i,url_field]).strip()]
                if not indices:
                    st.info(f'{shop}: Keine hinterlegten Links für die Auswahl.')
                    continue
                st.write(f'{shop.capitalize()}: {len(indices)} Produktseiten werden geprüft.')
                new,ok,failed=update_prices(new,shop,indices)
                st.write(f'{shop.capitalize()}: {ok} erfolgreich, {failed} fehlgeschlagen.')
            try:
                save(new); st.success('Preisprüfung gespeichert.'); st.rerun()
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
