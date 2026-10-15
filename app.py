import streamlit as st
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
import re

ASIN = 'B0DYV3XVZ6'
URL = f'https://www.amazon.de/dp/{ASIN}'
st.set_page_config(page_title='LBrixx Price-Crawler', page_icon='🧱')
st.title('LBrixx Price-Crawler – Amazon-Test')
st.write('**Artikel:** 17003 · FUNWHOLE Future Bus · **ASIN:** B0DYV3XVZ6')
st.link_button('Produkt bei Amazon öffnen', URL)
st.caption('Testabruf: kein garantierter Live-Preis. Amazon kann automatisierte Zugriffe blockieren.')

def check():
    try:
        r = requests.get(URL, headers={'User-Agent':'Mozilla/5.0 (compatible; LBrixxPriceTest/0.1)', 'Accept-Language':'de-DE,de;q=0.9'}, timeout=12)
        if r.status_code != 200:
            return None, f'HTTP {r.status_code}: Zugriff nicht verfügbar'
        soup = BeautifulSoup(r.text, 'html.parser')
        title = soup.title.get_text(' ', strip=True) if soup.title else ''
        if any(x in title.lower() for x in ['captcha', 'robot check']) or 'captcha' in r.url.lower():
            return None, 'Amazon fordert eine Überprüfung (CAPTCHA)'
        selectors = ['#corePrice_feature_div .a-offscreen', '#corePriceDisplay_desktop_feature_div .a-offscreen', '#priceblock_ourprice', '#priceblock_dealprice']
        for selector in selectors:
            for element in soup.select(selector):
                value = element.get_text(' ', strip=True)
                match = re.search(r'(\d{1,5}(?:\.\d{3})*,\d{2})\s*€|€\s*(\d{1,5}(?:\.\d{3})*,\d{2})', value)
                if match:
                    return (match.group(1) or match.group(2)), 'Unbestätigter Seitenpreis; Versand und Gutscheine nicht erfasst'
        return None, 'Kein eindeutiger Preis im HTML gefunden'
    except requests.RequestException as e:
        return None, f'Netzwerkfehler: {type(e).__name__}'

if st.button('Amazon-Abfrage testen', type='primary'):
    with st.spinner('Abruf läuft …'):
        price, status = check()
    st.write('**Prüfzeitpunkt (UTC):**', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
    st.write('**Status:**', status)
    if price:
        st.metric('Erkannter Artikelpreis (nicht verifiziert)', price + ' €')
        st.warning('Nicht als finalen Vergleichspreis verwenden: Verkäufer, Versand und Gutscheine müssen geprüft werden.')
    else:
        st.info('Kein Preis erfasst. Es wird kein Ersatzpreis erfunden.')
