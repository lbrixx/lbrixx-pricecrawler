import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title='LBrixx – Brickmo V13', layout='wide')
st.title('LBrixx – Brickmo-Preisvergleich V13')
st.caption('Öffentliche Produktseite; keine Umgehung von Zugriffssperren. Ein einzelner Abruf pro Klick.')
DEFAULT = 'https://www.brickmo.com/themenwelt/fahrzeuge/82179/funwhole-cyberpunk-fo9060-future-bus-fo9060?number=FO9060'
url = st.text_input('Brickmo-Produktlink', DEFAULT)
shipping_mode = st.selectbox('Versandkosten nach Deutschland', ['Unbekannt', 'Manuell eintragen (verifiziert)', 'Kostenlos (verifiziert)'])
shipping_input = st.number_input('Verifizierte Versandkosten (€)', min_value=0.0, value=4.90, step=0.10) if shipping_mode.startswith('Manuell') else None

def euro(value):
    return f'{value:,.2f} €'.replace(',', 'X').replace('.', ',').replace('X', '.')

def amount(raw):
    if raw is None:
        return None
    s = re.sub(r'[^\d,.]', '', str(raw))
    if not s:
        return None
    if ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.') if s.rfind(',') > s.rfind('.') else s.replace(',', '')
    elif ',' in s:
        s = s.replace('.', '').replace(',', '.')
    try:
        v = Decimal(s)
        return v if Decimal('0') < v < Decimal('100000') else None
    except InvalidOperation:
        return None

def extract(soup):
    title = soup.select_one('h1') or soup.title
    name = title.get_text(' ', strip=True) if title else 'Unbekannt'
    candidates = []
    # Shopware price element, product-scoped meta tags, and OpenGraph product price.
    for selector, attr in [('.product-detail-price', None), ('.product--price', None), ('[itemprop="price"]', 'content'), ('meta[property="product:price:amount"]', 'content')]:
        for tag in soup.select(selector)[:4]:
            raw = tag.get(attr) if attr and tag.get(attr) else tag.get_text(' ', strip=True)
            m = re.search(r'\d[\d.,]*\s*€?', str(raw))
            v = amount(m.group()) if m else None
            if v is not None:
                candidates.append((selector, v))
    text = soup.get_text(' ', strip=True)
    # Fallback is only accepted when the price follows an explicit product availability label.
    if not candidates:
        m = re.search(r'Artikel steht derzeit nicht zur Verfügung!\s*€\s*([\d.,]+)', text, re.I)
        if m:
            v = amount(m.group(1))
            if v is not None:
                candidates.append(('Produkttext neben Verfügbarkeit', v))
    unavailable = bool(re.search(r'Artikel steht derzeit nicht zur Verfügung|derzeit nicht lieferbar|aktuell nicht verfügbar|ausverkauft', text, re.I))
    available = bool(re.search(r'\bSofort lieferbar\b|\bAuf Lager\b|\bsofort verfügbar\b', text, re.I))
    availability = 'Nicht lieferbar' if unavailable else ('Lieferbar (Texthinweis)' if available else 'Unbekannt')
    return name, candidates, availability

if st.button('Brickmo-Preis prüfen', type='primary'):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('brickmo.com', 'www.brickmo.com'):
        st.error('Nur HTTPS-Produktlinks von brickmo.com sind zulässig.')
    else:
        try:
            response = requests.get(url, timeout=25, headers={'User-Agent': 'LBrixxPriceCheck/1.0', 'Accept': 'text/html'})
            st.write('HTTP-Status:', response.status_code)
            if response.status_code in (401, 403, 429):
                st.warning('Zugriff verweigert oder begrenzt. Kein weiterer Abruf.')
            elif not response.ok:
                st.error(f'Abruf fehlgeschlagen: HTTP {response.status_code}')
            elif 'html' not in response.headers.get('Content-Type', '').lower():
                st.error('Keine HTML-Antwort erhalten.')
            else:
                soup = BeautifulSoup(response.text, 'html.parser')
                name, candidates, availability = extract(soup)
                st.subheader(name)
                st.write('Verfügbarkeit:', availability)
                unique = set(v for _, v in candidates)
                if not candidates:
                    st.warning('Kein eindeutig zuordenbarer Produktpreis gefunden. Parser muss angepasst werden.')
                elif len(unique) != 1:
                    st.warning('Widersprüchliche Preisangaben gefunden. Keine automatische Übernahme.')
                    st.write('Kandidaten:', [(source, euro(value)) for source, value in candidates])
                else:
                    price = candidates[0][1]
                    st.metric('Artikelpreis', euro(price))
                    st.caption('Aus HTML erkannt über: ' + ', '.join(sorted(set(src for src, _ in candidates))))
                    shipping = Decimal(str(shipping_input)) if shipping_input is not None else (Decimal('0') if shipping_mode.startswith('Kostenlos') else None)
                    if shipping is None:
                        st.info('Versandkosten unbekannt – Gesamtpreis nicht berechenbar.')
                    else:
                        st.metric('Gesamtpreis inkl. Versand', euro(price + shipping))
                    if availability == 'Nicht lieferbar':
                        st.warning('Nicht lieferbar: Preis sichtbar, aber vom günstigsten kaufbaren Angebot ausgeschlossen.')
                    elif availability == 'Unbekannt':
                        st.warning('Verfügbarkeit unbekannt: Nicht in das Ranking kaufbarer Angebote übernehmen.')
                    elif shipping is None:
                        st.warning('Lieferbar, aber Versandkosten unbekannt: kein vollständiger Gesamtpreisvergleich.')
                    else:
                        st.success('Kaufbares Angebot mit Gesamtpreis: für den Vergleich geeignet (Lieferbarkeit vor Kauf erneut prüfen).')
                with st.expander('Diagnose'):
                    st.write('Finale URL:', response.url)
                    st.write('Antwortgröße:', len(response.content), 'Bytes')
                    st.write('Gefundene Preiskandidaten:', [(source, euro(value)) for source, value in candidates])
        except requests.RequestException as exc:
            st.error(f'Netzwerkfehler: {type(exc).__name__}: {exc}')
