import json
import re
from urllib.parse import urlparse

import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title='LBrixx – Brickmo-Diagnose V12', layout='wide')
st.title('LBrixx – Brickmo-Diagnose V12')
st.caption('Testet öffentlich zugängliche Produktinformationen. Keine Umgehung von Zugriffssperren.')

DEFAULT_URL = 'https://www.brickmo.com/themenwelt/fahrzeuge/82179/funwhole-cyberpunk-fo9060-future-bus-fo9060?number=FO9060'
url = st.text_input('Brickmo-Produktlink', DEFAULT_URL)

def extract_jsonld(soup):
    items = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(tag.string or tag.get_text())
            items.append(data)
        except (ValueError, TypeError):
            continue
    return items

def product_nodes(obj):
    if isinstance(obj, list):
        for x in obj:
            yield from product_nodes(x)
    elif isinstance(obj, dict):
        typ = obj.get('@type', '')
        if typ == 'Product' or (isinstance(typ, list) and 'Product' in typ):
            yield obj
        for key in ('@graph', 'mainEntity', 'itemListElement'):
            if key in obj:
                yield from product_nodes(obj[key])

if st.button('Brickmo jetzt testen', type='primary'):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('brickmo.com', 'www.brickmo.com'):
        st.error('Bitte einen HTTPS-Link von brickmo.com eingeben.')
    else:
        try:
            with st.spinner('Produktseite wird abgerufen …'):
                response = requests.get(url, timeout=25, headers={'User-Agent': 'LBrixxPriceCheck/1.0 (public product page diagnostic)', 'Accept': 'text/html,application/xhtml+xml'})
            st.metric('HTTP-Status', response.status_code)
            st.write('Finale URL:', response.url)
            st.write('Antwortgröße:', f'{len(response.content):,} Bytes')
            st.write('Content-Type:', response.headers.get('Content-Type', 'nicht angegeben'))
            if response.status_code in (401, 403, 429):
                st.warning('Der Shop verweigert oder begrenzt den automatisierten Zugriff. Kein weiterer Abruf wird versucht.')
            elif not response.ok:
                st.warning('Die Seite konnte nicht erfolgreich abgerufen werden.')
            elif 'html' not in response.headers.get('Content-Type', '').lower():
                st.warning('Die Antwort ist kein HTML-Dokument.')
            else:
                soup = BeautifulSoup(response.text, 'html.parser')
                st.subheader('HTML-Metadaten')
                st.write('Seitentitel:', soup.title.get_text(' ', strip=True) if soup.title else 'nicht gefunden')
                desc = soup.find('meta', attrs={'name': 'description'})
                st.write('Beschreibung:', desc.get('content', '') if desc else 'nicht gefunden')
                for attr, value in [('property', 'product:price:amount'), ('property', 'product:price:currency'), ('property', 'og:title')]:
                    tag = soup.find('meta', attrs={attr: value})
                    if tag:
                        st.write(value + ':', tag.get('content', ''))
                st.subheader('Strukturierte Produktdaten')
                blocks = extract_jsonld(soup)
                products = list(product_nodes(blocks))
                if products:
                    for product in products:
                        st.json({k: product.get(k) for k in ('name', 'sku', 'mpn', 'offers') if k in product})
                else:
                    st.info('Keine JSON-LD-Produktdaten gefunden.')
                st.subheader('Versandhinweise')
                text = soup.get_text(' ', strip=True)
                snippets = []
                for match in list(re.finditer(r'Versandkosten|Versandkostenfrei|Lieferkosten|zzgl\. Versand', text, re.I))[:5]:
                    snippets.append(text[max(0, match.start()-65):match.end()+115])
                if snippets:
                    for snippet in snippets:
                        st.write('•', snippet)
                else:
                    st.info('Keine Versandhinweise im sichtbaren Seitentext gefunden.')
                st.caption('Preise und Versandkosten sind nur belastbar, wenn sie eindeutig für diesen Artikel und eine Lieferung nach Deutschland gelten.')
        except requests.RequestException as exc:
            st.error(f'Netzwerkfehler: {type(exc).__name__}: {exc}')
