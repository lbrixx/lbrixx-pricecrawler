import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import requests
import streamlit as st
from bs4 import BeautifulSoup

ASIN = 'B0DYV3XVZ6'
URL = f'https://www.amazon.de/dp/{ASIN}'
st.set_page_config(page_title='LBrixx – Amazon Preisdiagnose', page_icon='🔎')
st.title('LBrixx – Amazon Preisdiagnose 2')
st.write(f'**Artikel:** 17003 · FUNWHOLE Future Bus · **ASIN:** {ASIN}')
st.link_button('Amazon-Artikel öffnen', URL)
st.caption('Unverifizierte Preis-Kandidaten sind keine bestätigten Kaufpreise.')

def normalize_price(value):
    if value is None:
        return None
    value = str(value).strip().replace('\u00a0', ' ')
    match = re.search(r'(\d{1,5}(?:[.,]\d{3})*(?:[.,]\d{2}))', value)
    if not match:
        return None
    raw = match.group(1)
    if ',' in raw and '.' in raw:
        raw = raw.replace('.', '').replace(',', '.') if raw.rfind(',') > raw.rfind('.') else raw.replace(',', '')
    elif ',' in raw:
        raw = raw.replace('.', '').replace(',', '.')
    try:
        price = Decimal(raw)
        return str(price) if Decimal('0.01') <= price <= Decimal('100000') else None
    except InvalidOperation:
        return None

def collect():
    response = requests.get(URL, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36',
        'Accept-Language': 'de-DE,de;q=0.9,en;q=0.7',
        'Accept': 'text/html,application/xhtml+xml',
    }, timeout=25)
    soup = BeautifulSoup(response.text, 'html.parser')
    selectors = [
        '#corePrice_feature_div .a-offscreen',
        '#corePriceDisplay_desktop_feature_div .a-offscreen',
        '#corePrice_desktop .a-offscreen',
        '#apex_desktop .a-price .a-offscreen',
        '#apex_offerDisplay_desktop .a-offscreen',
        '.priceToPay .a-offscreen',
        '.reinventPricePriceToPayMargin .a-offscreen',
        '#priceblock_ourprice', '#priceblock_dealprice',
        '#price_inside_buybox', '#newBuyBoxPrice',
        'span.a-price > span.a-offscreen',
    ]
    candidates = []
    for selector in selectors:
        for el in soup.select(selector)[:8]:
            raw = el.get_text(' ', strip=True)[:100]
            val = normalize_price(raw)
            if val:
                candidates.append({'Quelle': selector, 'Text': raw, 'EUR-Kandidat': val})
    for meta in soup.select('meta[property="product:price:amount"], meta[property="og:price:amount"], meta[itemprop="price"]')[:10]:
        raw = meta.get('content', '')
        val = normalize_price(raw)
        if val:
            candidates.append({'Quelle': 'meta price', 'Text': raw[:100], 'EUR-Kandidat': val})
    for script in soup.select('script[type="application/ld+json"]')[:10]:
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        def visit(obj, depth=0):
            if depth > 7:
                return
            if isinstance(obj, list):
                for item in obj[:30]: visit(item, depth+1)
            elif isinstance(obj, dict):
                if 'price' in obj and ('priceCurrency' in obj or 'availability' in obj):
                    raw = obj.get('price')
                    val = normalize_price(raw)
                    if val:
                        candidates.append({'Quelle': 'JSON-LD offer', 'Text': str(raw)[:100], 'EUR-Kandidat': val})
                for key, item in obj.items():
                    if isinstance(item, (dict, list)): visit(item, depth+1)
        visit(data)
    html_lower = response.text.lower()
    block = any(x in html_lower for x in ['validatecaptcha', 'robot check', 'enter the characters you see below'])
    return response, soup, candidates, block

if st.button('Preis-Fundstellen prüfen', type='primary'):
    try:
        with st.spinner('Amazon-Seite wird geprüft …'):
            response, soup, candidates, blocked = collect()
        st.write('**Zeitpunkt (UTC):**', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
        st.write('**HTTP-Status:**', response.status_code)
        st.write('**Seitenadresse:**', response.url)
        st.write('**Seitentitel:**', soup.title.get_text(' ', strip=True)[:180] if soup.title else 'nicht vorhanden')
        st.write('**Antwortgröße:**', f'{len(response.content):,} Bytes')
        st.write('**Blockadehinweis:**', 'Mögliche Sperre erkannt' if blocked else 'Kein typischer Hinweis')
        st.write('**Preis-Kandidaten:**', len(candidates))
        if candidates:
            st.dataframe(candidates, use_container_width=True, hide_index=True)
            st.warning('Bitte Kandidaten mit der geöffneten Amazon-Seite vergleichen. Versand, Varianten, Streichpreise und Gutscheine sind noch nicht geprüft.')
        else:
            st.info('Auch mit erweiterten Selektoren und strukturierten Daten kein Preis gefunden. Möglicherweise enthält die ausgelieferte Seite keinen Angebotspreis.')
        st.caption('Keine Cookies, Zugangsdaten oder vollständiges HTML hier teilen.')
    except requests.RequestException as exc:
        st.error(f'Netzwerkfehler: {type(exc).__name__}: {exc}')
