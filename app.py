import re
from decimal import Decimal
from urllib.parse import urlparse
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title='LBrixx Brickmo V15', page_icon='🧱')
st.title('LBrixx – Brickmo Preisprüfung V15')
PRODUCTS = {
    'Future Bus (FO9060)': 'https://www.brickmo.com/themenwelt/fahrzeuge/82179/funwhole-cyberpunk-fo9060-future-bus-fo9060?number=FO9060',
    'Polizeiauto (FO9043)': 'https://www.brickmo.com/themenwelt/fahrzeuge/82178/funwhole-cyberpunk-fo9043-polizeiauto-fo9043',
    'Eigene URL': '',
}
choice = st.selectbox('Testartikel auswählen', list(PRODUCTS))
url = st.text_input('Brickmo-Produkt-URL', value=PRODUCTS[choice], key=choice)
shipping_choice = st.selectbox('Versandkosten nach Deutschland', ['Unbekannt', '0,00 €', '4,90 €', 'Eigener Betrag'])
shipping_custom = st.number_input('Versandkosten (€)', min_value=0.0, step=0.5) if shipping_choice == 'Eigener Betrag' else None

def parse_euro(s):
    match = re.search(r'(\d{1,5}(?:\.\d{3})*,\d{2})\s*€?', s)
    return Decimal(match.group(1).replace('.', '').replace(',', '.')) if match else None

def valid_url(value):
    parsed = urlparse(value)
    return parsed.scheme == 'https' and parsed.hostname in ('brickmo.com', 'www.brickmo.com')

if st.button('Brickmo-Preis prüfen', type='primary'):
    if not valid_url(url):
        st.error('Bitte eine HTTPS-Produktadresse von brickmo.com eingeben.')
        st.stop()
    try:
        resp = requests.get(url, timeout=20, headers={'User-Agent': 'Mozilla/5.0 (compatible; LBrixxPriceChecker/1.0)'})
        st.write('HTTP-Status:', resp.status_code)
        resp.raise_for_status()
    except requests.RequestException as exc:
        st.error(f'Produktseite konnte nicht geladen werden: {exc}')
        st.stop()
    soup = BeautifulSoup(resp.text, 'html.parser')
    h1 = soup.select_one('h1')
    title = h1.get_text(' ', strip=True) if h1 else (soup.title.get_text(' ', strip=True) if soup.title else 'Unbekannt')
    st.subheader(title)
    # Select a main product region and avoid recommendation cards.
    region = soup.select_one('.product--details') or soup.select_one('.product-detail') or soup.select_one('.product-details') or soup.select_one('[itemtype*="schema.org/Product"]') or soup
    candidates = []
    for sel in ('.product--price', '[itemprop="price"]', '.product-detail-price', '.product-price'):
        for node in region.select(sel):
            if node.find_parent(class_=re.compile('recommend|similar|cross.sell|listing|slider', re.I)):
                continue
            val = parse_euro(node.get('content', '') or node.get_text(' ', strip=True))
            if val is not None:
                candidates.append((sel, val))
        if candidates:
            break
    if not candidates:
        st.warning('Kein eindeutig zugeordneter Artikelpreis gefunden.')
    elif len(set(v for _, v in candidates)) > 1:
        st.warning('Mehrere Preise im Hauptproduktbereich gefunden – bitte prüfen.')
        st.json([{'Selektor': s, 'Preis': str(v)} for s, v in candidates])
    else:
        price = candidates[0][1]
        st.metric('Artikelpreis', f'{price:.2f} €'.replace('.', ','))
        st.caption(f'Erkannt über {candidates[0][0]}')
    text = region.get_text(' ', strip=True)
    if re.search(r'Artikel steht derzeit nicht zur Verfügung|derzeit nicht lieferbar|nicht verfügbar', text, re.I):
        available = False
        st.warning('Nicht lieferbar – vom günstigsten kaufbaren Angebot ausgeschlossen.')
    elif re.search(r'in den Warenkorb|sofort lieferbar|auf Lager', text, re.I):
        available = True
        st.success('Hinweis auf Lieferbarkeit erkannt – bitte im Shop verifizieren.')
    else:
        available = None
        st.info('Verfügbarkeit unbekannt – nicht automatisch ins Ranking übernommen.')
    shipping = None if shipping_choice == 'Unbekannt' else (Decimal(str(shipping_custom)) if shipping_choice == 'Eigener Betrag' else parse_euro(shipping_choice))
    if candidates and len(set(v for _, v in candidates)) == 1 and shipping is not None:
        st.metric('Gesamtpreis inkl. eingetragener Versandkosten', f'{price + shipping:.2f} €'.replace('.', ','))
    else:
        st.info('Gesamtpreis noch nicht berechenbar (Preis oder Versandkosten unklar).')
    with st.expander('Diagnose'):
        st.write('Finale URL:', resp.url)
        st.write('HTML-Größe:', len(resp.content), 'Bytes')
        st.write('Preis-Kandidaten:', [(s, str(v)) for s, v in candidates])
        st.write('Verfügbarkeit:', available)

st.caption('Versandkosten sind manuelle Angaben und werden nicht als automatisch ermittelt ausgegeben. Öffentliche Shopdaten können sich ändern.')
