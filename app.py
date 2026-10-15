import os, re, io, csv, json, base64, hashlib, html
from datetime import datetime, timezone
from urllib.parse import urlparse
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title='LBrixx Preisvergleich', layout='wide', page_icon='🧱')
DATA_FILE = 'lbrixx_products.csv'
COLUMNS = ['sku','lbrixx_sku','description','lbrixx_price','brickmo_url','photo','brickmo_price','shipping','total','availability','last_checked','error']
SEEDS = [
 {'sku':'FO9060','lbrixx_sku':'17003','description':'FunWhole Cyberpunk Future Bus','lbrixx_price':'','brickmo_url':'https://www.brickmo.com/themenwelt/fahrzeuge/82179/funwhole-cyberpunk-fo9060-future-bus-fo9060?number=FO9060'},
 {'sku':'FO9043','lbrixx_sku':'','description':'FunWhole Cyberpunk Polizeiauto','lbrixx_price':'','brickmo_url':'https://www.brickmo.com/themenwelt/fahrzeuge/82178/funwhole-cyberpunk-fo9043-polizeiauto-fo9043'},
]

def secrets(name, default=''):
    try: return st.secrets.get(name, default)
    except Exception: return default

GH_TOKEN=secrets('GITHUB_TOKEN'); GH_REPO=secrets('GITHUB_REPO'); GH_PATH=secrets('GITHUB_DATA_PATH','data/lbrixx_products.csv')
GH_ENABLED=bool(GH_TOKEN and GH_REPO)

def gh_request(method, payload=None):
    url=f'https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}'
    headers={'Authorization':f'Bearer {GH_TOKEN}','Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}
    r=requests.request(method,url,headers=headers,json=payload,timeout=20)
    if r.status_code not in (200,201,404): r.raise_for_status()
    return r

def load():
    if GH_ENABLED:
        r=gh_request('GET')
        if r.status_code==200:
            data=r.json(); content=base64.b64decode(data['content']).decode('utf-8-sig')
            return pd.read_csv(io.StringIO(content),dtype=str,keep_default_na=False),data['sha']
        return pd.DataFrame(SEEDS),None
    if os.path.exists(DATA_FILE):
        return pd.read_csv(DATA_FILE,dtype=str,keep_default_na=False),None
    return pd.DataFrame(SEEDS),None

def normalize(df):
    for c in COLUMNS:
        if c not in df.columns: df[c]=''
    return df[COLUMNS].fillna('').astype(str).reset_index(drop=True)

def save(df):
    df=normalize(df)
    content=df.to_csv(index=False)
    if GH_ENABLED:
        r=gh_request('GET'); sha=r.json().get('sha') if r.status_code==200 else None
        payload={'message':'Update LBrixx price crawler products','content':base64.b64encode(content.encode('utf-8')).decode('ascii')}
        if sha: payload['sha']=sha
        res=gh_request('PUT',payload)
        if res.status_code not in (200,201): raise RuntimeError(f'GitHub save failed: {res.status_code}')
    else:
        with open(DATA_FILE,'w',encoding='utf-8',newline='') as f: f.write(content)
    st.session_state.products=df

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
    return {'sku':sku,'description':title,'photo':image,'brickmo_price':f'{price:.2f}','shipping':'5.49','total':f'{price+5.49:.2f}','availability':availability,'last_checked':datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),'error':''}

if 'products' not in st.session_state:
    try:
        df,_=load(); st.session_state.products=normalize(df)
    except Exception as e:
        st.error(f'Produktliste konnte nicht geladen werden: {e}'); st.stop()

st.title('🧱 LBrixx – Preisvergleich V20')
st.caption('Brickmo · Einzelartikelvergleich nach Deutschland · Versand 5,49 € je Bestellung · keine Freigrenze')
if not GH_ENABLED:
    st.warning('Artikel werden derzeit nur lokal in Streamlit gespeichert und können bei Neustart/Neuveröffentlichung verloren gehen. CSV regelmäßig exportieren oder GitHub-Speicherung in Secrets einrichten.')

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

st.subheader('Artikelübersicht')
df=st.session_state.products.copy()

# The Edit link in the first table column selects a row by its current position.
# All edits are committed through save(), including GitHub persistence.
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
                new_url = st.text_input('Brickmo-Produktlink', value=str(current['brickmo_url']))
            with e2:
                new_desc = st.text_input('Beschreibung', value=str(current['description']))
                new_price = st.text_input('LBrixx-Verkaufspreis (€)', value=fmt(current['lbrixx_price']).replace(' €','') if euro_num(current['lbrixx_price']) is not None else '')
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
            elif not valid_url(cleaned_url):
                st.error('Bitte eine gültige Brickmo-HTTPS-Produktadresse eintragen.')
            elif new_price.strip() and (new_price_value is None or new_price_value < 0):
                st.error('Der LBrixx-Preis muss eine gültige, nicht negative Zahl sein.')
            elif cleaned_sku in other.sku.str.upper().values:
                st.error('Diese Hersteller-SKU ist bereits vorhanden.')
            elif cleaned_url in other.brickmo_url.values:
                st.error('Dieser Brickmo-Link ist bereits vorhanden.')
            else:
                updated = df.copy()
                updated.at[edit_index, 'sku'] = cleaned_sku
                updated.at[edit_index, 'lbrixx_sku'] = new_ownsku.strip()
                updated.at[edit_index, 'description'] = new_desc.strip()
                updated.at[edit_index, 'lbrixx_price'] = f'{new_price_value:.2f}' if new_price.strip() else ''
                updated.at[edit_index, 'brickmo_url'] = cleaned_url
                # A changed URL invalidates the old shop price and availability until rechecked.
                if cleaned_url != str(current['brickmo_url']):
                    for field in ('brickmo_price', 'shipping', 'total', 'availability', 'last_checked', 'error', 'photo'):
                        updated.at[edit_index, field] = ''
                try:
                    save(updated)
                    st.query_params.clear()
                    st.success('Artikel dauerhaft gespeichert.')
                    st.rerun()
                except Exception as exc:
                    st.error(f'Änderungen konnten nicht gespeichert werden: {exc}')
if df.empty: st.info('Noch keine Artikel vorhanden.')
else:
    # Render clickable, availability-colored Brickmo price cells as safe HTML.
    # st.dataframe does not reliably combine per-cell background styles with LinkColumn.
    def cell(v):
        return html.escape(str(v if v is not None else ''), quote=True)

    def price_cell(row):
        status=str(row.availability).strip().lower()
        css_class = 'available' if status=='lieferbar' else 'unavailable' if status=='nicht lieferbar' else 'unknown'
        price=fmt(row.brickmo_price)
        url=str(row.brickmo_url).strip()
        content=cell(price)
        if valid_url(url):
            content=f'<a href="{cell(url)}" target="_blank" rel="noopener noreferrer" title="Brickmo-Produkt öffnen">{content} ↗</a>'
        return f'<td class="brickmo-price {css_class}" title="{cell(row.availability or "Nicht geprüft")}">{content}</td>'

    rows=[]
    for row_idx, r in df.iterrows():
        photo=str(r.photo).strip()
        img=f'<img src="{cell(photo)}" alt="Produktfoto" loading="lazy">' if photo.startswith('https://') else '–'
        a=euro_num(r.lbrixx_price); b=euro_num(r.total)
        diff=fmt(round(a-b,2)) if a is not None and b is not None else '–'
        rows.append('<tr>' +
            f'<td class="edit"><a href="?edit={row_idx}" target="_self" aria-label="Artikel bearbeiten">✏️ Edit</a></td>' +
            f'<td class="photo">{img}</td>' +
            f'<td>{cell(r.sku)}</td>' +
            f'<td>{cell(r.lbrixx_sku)}</td>' +
            f'<td class="description">{cell(r.description)}</td>' +
            f'<td>{cell(fmt(r.lbrixx_price))}</td>' +
            price_cell(r) +
            f'<td>{cell(fmt(r.shipping))}</td>' +
            f'<td>{cell(fmt(r.total))}</td>' +
            f'<td>{cell(diff)}</td>' +
            f'<td class="checked">{cell(r.last_checked)}</td>' +
            '</tr>')
    table_css = """
    <style>
    .lbrixx-scroll {overflow-x:auto; width:100%; border:1px solid rgba(128,128,128,.25); border-radius:10px;}
    .lbrixx-table {width:100%; border-collapse:collapse; font-size:14px; min-width:1050px;}
    .lbrixx-table th {text-align:left; font-weight:650; padding:12px 10px; background:rgba(128,128,128,.13); white-space:nowrap;}
    .lbrixx-table td {padding:10px; border-top:1px solid rgba(128,128,128,.2); vertical-align:middle;}
    .lbrixx-table tr:hover td:not(.brickmo-price) {background:rgba(128,128,128,.06);}
    .lbrixx-table .edit a {display:inline-block; padding:7px 9px; border:1px solid rgba(128,128,128,.45); border-radius:7px; white-space:nowrap; text-decoration:none; color:inherit; font-weight:600;}\n    .lbrixx-table .edit a:hover {background:rgba(128,128,128,.16);}\n    .lbrixx-table .photo img {width:65px; height:65px; object-fit:contain; border-radius:5px;}
    .lbrixx-table .description {min-width:220px;}
    .lbrixx-table .checked {font-size:12px; white-space:nowrap;}
    .lbrixx-table .brickmo-price {font-weight:700; white-space:nowrap;}
    .lbrixx-table .brickmo-price.available {background:#c8f3d2; color:#13532c;}
    .lbrixx-table .brickmo-price.unknown {background:#fff0ae; color:#704d00;}
    .lbrixx-table .brickmo-price.unavailable {background:#ffd1d1; color:#7b1e1e;}
    .lbrixx-table .brickmo-price a {color:inherit; text-decoration:underline; text-underline-offset:3px;}
    .lbrixx-table .brickmo-price a:hover {text-decoration-thickness:2px;}
    </style>
    """
    headings=['Edit','Foto','SKU','LBrixx-Nr.','Beschreibung','LBrixx-Preis','Brickmo-Preis ↗','Versand','Gesamtpreis','Differenz LBrixx − Brickmo','Stand']
    table_html=table_css+'<div class="lbrixx-scroll"><table class="lbrixx-table"><thead><tr>'+''.join(f'<th>{cell(h)}</th>' for h in headings)+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>'
    st.html(table_html)
    st.caption('Farben im Brickmo-Preisfeld: 🟢 lieferbar · 🟡 unbestätigt · 🔴 nicht lieferbar. Preis anklicken, um das Produkt bei Brickmo zu öffnen.')
    c1,c2,c3=st.columns([1,1,2])
    with c1: update=st.button('🔄 Alle Preise aktualisieren',type='primary',use_container_width=True)
    with c2: st.download_button('⬇️ CSV exportieren',data=df.to_csv(index=False).encode('utf-8-sig'),file_name='lbrixx_preisvergleich.csv',mime='text/csv',use_container_width=True)
    with c3: st.caption('Nur eindeutig lieferbare Artikel zählen für das Ranking.')
    if update:
        bar=st.progress(0); new=df.copy()
        for i,row in df.iterrows():
            try:
                result=parse_product(row.brickmo_url)
                for k,v in result.items():
                    if k=='sku' and row.sku: continue
                    if k=='description' and row.description: continue
                    if k=='photo' and not v: continue
                    new.at[i,k]=v
            except Exception as e:
                new.at[i,'error']=str(e); new.at[i,'availability']='Unbestätigt'
            bar.progress((i+1)/len(df))
        try: save(new); st.success('Preisprüfung abgeschlossen.'); st.rerun()
        except Exception as e: st.error(f'Ergebnisse konnten nicht gespeichert werden: {e}')

    with st.expander('✏️ Artikel bearbeiten / löschen'):
        edited=st.data_editor(df[['sku','lbrixx_sku','description','lbrixx_price','brickmo_url']].copy(),hide_index=True,num_rows='dynamic',use_container_width=True,key='editor',column_config={'brickmo_url':st.column_config.TextColumn('Brickmo-URL',width='large'),'lbrixx_price':st.column_config.TextColumn('LBrixx-Preis €')})
        if st.button('Änderungen speichern'):
            if edited.sku.duplicated().any() or edited.brickmo_url.duplicated().any(): st.error('Doppelte SKU oder Produkt-URL gefunden.')
            elif any(not valid_url(u) for u in edited.brickmo_url): st.error('Ungültiger Brickmo-Link.')
            elif any(x and euro_num(x) is None for x in edited.lbrixx_price): st.error('LBrixx-Preis muss eine Zahl sein.')
            else:
                rows=[]
                for pos, (_,r) in enumerate(edited.iterrows()):
                    original=df.iloc[pos] if pos < len(df) else None
                    item=original.to_dict() if original is not None else {c:'' for c in COLUMNS}
                    old_url=item.get('brickmo_url','')
                    item.update(r.to_dict())
                    if old_url != item['brickmo_url']:
                        for field in ('brickmo_price','shipping','total','availability','last_checked','error','photo'):
                            item[field]=''
                    rows.append(item)
                try: save(pd.DataFrame(rows)); st.success('Gespeichert.'); st.rerun()
                except Exception as e: st.error(f'Speichern fehlgeschlagen: {e}')

with st.expander('📥 Artikel aus CSV importieren'):
    file=st.file_uploader('CSV mit mindestens brickmo_url, optional sku, lbrixx_sku, description, lbrixx_price',type='csv')
    if file and st.button('CSV importieren'):
        try:
            incoming=pd.read_csv(file,dtype=str,keep_default_na=False,encoding='utf-8-sig')
            if 'brickmo_url' not in incoming.columns: raise ValueError('Spalte brickmo_url fehlt.')
            if not all(valid_url(u) for u in incoming.brickmo_url): raise ValueError('Mindestens eine URL ist ungültig.')
            incoming=normalize(incoming)
            combined=pd.concat([st.session_state.products,incoming],ignore_index=True).drop_duplicates(subset='brickmo_url',keep='first')
            save(combined); st.success('CSV importiert. Jetzt „Alle Preise aktualisieren“ klicken.'); st.rerun()
        except Exception as e: st.error(str(e))

with st.expander('⚙️ Dauerhafte Speicherung über GitHub einrichten'):
    st.markdown('Optional in **Streamlit Community Cloud → App settings → Secrets** hinterlegen (nicht in GitHub-Dateien):')
    st.code('GITHUB_REPO = "DEIN-USERNAME/lbrixx-pricecrawler"\nGITHUB_TOKEN = "github_pat_..."\nGITHUB_DATA_PATH = "data/lbrixx_products.csv"',language='toml')
    st.caption('Ein Fine-grained GitHub Token benötigt nur Contents: Read and write für dieses Repository. Die App speichert die Produktliste als CSV in GitHub. Niemals Tokens im Chat oder in app.py veröffentlichen.')
