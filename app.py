import os, re, io, csv, json, base64, hashlib
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

st.title('🧱 LBrixx – Preisvergleich')
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
if df.empty: st.info('Noch keine Artikel vorhanden.')
else:
    # Use an actual image column; other columns editable through the editor below.
    display=pd.DataFrame({
        'Foto':df.photo,'SKU':df.sku,'LBrixx-Nr.':df.lbrixx_sku,'Beschreibung':df.description,
        'LBrixx-Preis':df.lbrixx_price.map(fmt),'Brickmo-Preis':df.brickmo_price.map(fmt),
        'Versand':df.shipping.map(fmt),'Gesamtpreis':df.total.map(fmt),
        'Differenz LBrixx − Brickmo': [fmt(round(euro_num(a)-euro_num(b),2)) if euro_num(a) is not None and euro_num(b) is not None else '–' for a,b in zip(df.lbrixx_price,df.total)],
        'Verfügbarkeit':df.availability.replace('','Nicht geprüft'),'Stand':df.last_checked,
        'Brickmo-Link':df.brickmo_url
    })
    st.dataframe(display,hide_index=True,use_container_width=True,row_height=76,column_config={'Foto':st.column_config.ImageColumn('Foto',width='small'),'Brickmo-Link':st.column_config.LinkColumn('Brickmo-Link',display_text='Produkt öffnen'),'Beschreibung':st.column_config.TextColumn('Beschreibung',width='large')})
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
                lookup={r.brickmo_url:r for _,r in df.iterrows()}
                rows=[]
                for _,r in edited.iterrows():
                    original=lookup.get(r.brickmo_url)
                    item=original.to_dict() if original is not None else {c:'' for c in COLUMNS}
                    item.update(r.to_dict()); rows.append(item)
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
