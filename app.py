import re
from decimal import Decimal
from urllib.parse import urlparse
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title='LBrixx – Brickmo V17', page_icon='🧱')
st.title('LBrixx Price-Crawler – Brickmo V17')
st.caption('Deutschland: 5,49 € Versand je Bestellung, keine Freigrenze. Einzelartikelvergleich.')

PRODUCTS = {
    'Future Bus (FO9060)': 'https://www.brickmo.com/themenwelt/fahrzeuge/82179/funwhole-cyberpunk-fo9060-future-bus-fo9060?number=FO9060',
    'Polizeiauto (FO9043)': 'https://www.brickmo.com/themenwelt/fahrzeuge/82178/funwhole-cyberpunk-fo9043-polizeiauto-fo9043',
    'Eigene URL': '',
}
SHIPPING = Decimal('5.49')
choice = st.selectbox('Testartikel auswählen', list(PRODUCTS))
url = st.text_input('Brickmo-Produkt-URL', value=PRODUCTS[choice], key=f'url_{choice}')


def parse_price(raw):
    m = re.search(r'\d[\d.]*,\d{2}|\d+\.\d{2}', raw or '')
    if not m:
        return None
    value = m.group(0)
    return Decimal(value.replace('.', '').replace(',', '.')) if ',' in value else Decimal(value)


def extract_product(soup):
    # Product-detail scopes take priority over recommendations and cross-selling.
    for selector in ('.product--details', '.product-detail', '.product--detail', '[itemtype*="schema.org/Product"]', 'main'):
        for root in soup.select(selector):
            for price_sel in ('.product--price', '[itemprop="price"]'):
                for el in root.select(price_sel):
                    if el.find_parent(class_=re.compile('cross.?sell|recommend|similar|listing|slider', re.I)):
                        continue
                    raw = el.get('content') or el.get('value') or el.get_text(' ', strip=True)
                    price = parse_price(raw)
                    if price is not None:
                        return price, price_sel
    return None, None


def stock_status(soup):
    """Only interpret stock notices from the main product detail panel."""
    scopes = soup.select('.product--details, .product-detail, .product--detail')
    if not scopes:
        return 'Unbestätigt', 'Kein eindeutiger Hauptproduktbereich gefunden'

    negative = (
        'artikel steht derzeit nicht zur verfügung',
        'derzeit nicht lieferbar',
        'momentan nicht lieferbar',
        'nicht auf lager',
        'ausverkauft',
    )
    positive = ('sofort versandfertig',)
    # Prefer the most specific container instead of the entire page.
    root = min(scopes, key=lambda element: len(element.get_text(' ', strip=True)))
    text = ' '.join(root.stripped_strings).casefold()
    has_negative = any(term in text for term in negative)
    has_positive = any(term in text for term in positive)
    if has_negative and has_positive:
        return 'Unbestätigt', 'Widersprüchliche Lagerhinweise im Produktbereich'
    if has_negative:
        return 'Nicht lieferbar', 'Negativer Lagerhinweis im Produktbereich'
    if has_positive:
        return 'Lieferbar', 'Sofort versandfertig im Produktbereich'
    return 'Unbestätigt', 'Kein eindeutiger Lagerhinweis im Produktbereich'

if st.button('Brickmo-Preis prüfen', type='primary'):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('brickmo.com', 'www.brickmo.com'):
        st.error('Bitte eine gültige HTTPS-Produkt-URL von brickmo.com eingeben.')
    else:
        try:
            response = requests.get(url, timeout=20, headers={'User-Agent': 'Mozilla/5.0 (compatible; LBrixxPriceCheck/1.0)'})
            st.write('HTTP-Status:', response.status_code)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            title = soup.select_one('h1') or soup.select_one('title')
            st.subheader(title.get_text(' ', strip=True) if title else 'Produkt')
            price, source = extract_product(soup)
            status, stock_reason = stock_status(soup)
            st.write('Verfügbarkeit:', status)
            if price is None:
                st.warning('Kein eindeutig zuordenbarer Hauptproduktpreis erkannt. Bitte HTML-Struktur prüfen.')
            else:
                total = price + SHIPPING
                a, b, c = st.columns(3)
                a.metric('Artikelpreis', f'{price:.2f} €'.replace('.', ','))
                b.metric('Versand DE', '5,49 €')
                c.metric('Gesamtpreis', f'{total:.2f} €'.replace('.', ','))
                st.caption(f'Preisquelle: {source}. Versand je Bestellung, bei einem Artikel vollständig zugerechnet.')
            if status == 'Nicht lieferbar':
                st.warning('Nicht lieferbar: vom Ranking ausgeschlossen.')
            elif status == 'Lieferbar':
                st.success('Lieferbar: Angebot kann im Preisranking berücksichtigt werden.')
            else:
                st.info('Lieferbarkeit noch nicht sicher bestätigt: vom Ranking vorerst ausgeschlossen.')
            with st.expander('Diagnose'):
                st.write({'url': response.url, 'status': response.status_code, 'price_selector': source, 'availability': status, 'availability_reason': stock_reason})
        except (requests.RequestException, ValueError) as exc:
            st.error(f'Abruf fehlgeschlagen: {exc}')
