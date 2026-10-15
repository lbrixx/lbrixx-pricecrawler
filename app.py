import os
import sys
import subprocess
from datetime import datetime, timezone
import streamlit as st
from playwright.sync_api import sync_playwright

ASIN = 'B0DYV3XVZ6'
URL = f'https://www.amazon.de/dp/{ASIN}'
st.set_page_config(page_title='LBrixx Amazon Browser-Test V7')
st.title('LBrixx Amazon Browser-Test V7')
st.write('Artikel 17003 · FUNWHOLE Future Bus · ASIN B0DYV3XVZ6')
st.link_button('Amazon-Artikel öffnen', URL)
st.caption('Referenzpreis im eigenen Browser: 52,90 € inkl. Versand; kein automatisch bestätigter Preis.')

@st.cache_resource(show_spinner='Chromium wird bei Bedarf installiert …')
def ensure_browser():
    with sync_playwright() as p:
        path = p.chromium.executable_path
    if os.path.isfile(path):
        return path, 'Bereits vorhanden'
    result = subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'], capture_output=True, text=True, timeout=180)
    if result.returncode != 0 or not os.path.isfile(path):
        raise RuntimeError('Chromium-Installation fehlgeschlagen.\n' + (result.stderr or result.stdout)[-3000:])
    return path, 'Neu installiert'

if st.button('Chromium installieren und Amazon testen', type='primary'):
    st.write('Prüfzeitpunkt (UTC):', datetime.now(timezone.utc).isoformat())
    try:
        executable, status = ensure_browser()
        st.success(f'Chromium: {status}')
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
            page = browser.new_page(locale='de-DE', timezone_id='Europe/Berlin')
            response = page.goto(URL, wait_until='domcontentloaded', timeout=45000)
            page.wait_for_timeout(3000)
            st.write('HTTP-Status:', response.status if response else 'Keine Antwort')
            st.write('Seitentitel:', page.title())
            st.write('Seitenadresse:', page.url)
            selectors = ['#corePrice_feature_div .a-offscreen', '#corePriceDisplay_desktop_feature_div .a-offscreen', '.priceToPay .a-offscreen', '#priceblock_ourprice', '#priceblock_dealprice']
            matches = []
            for selector in selectors:
                for node in page.locator(selector).all()[:5]:
                    value = node.text_content(timeout=2000)
                    if value and value.strip():
                        matches.append({'Selektor': selector, 'Text': value.strip()})
            if matches:
                st.warning('Preis-Kandidaten – bitte mit dem sichtbaren Kaufpreis vergleichen; Versand nicht automatisch bestätigt.')
                st.dataframe(matches)
            else:
                st.info('Keine eindeutigen Preis-Kandidaten gefunden. Kein Preis wird erfunden.')
            browser.close()
    except Exception as exc:
        st.error(f'Test fehlgeschlagen: {type(exc).__name__}: {exc}')
        st.info('Falls die Installation an Ressourcen- oder Systembibliotheken scheitert, benötigen wir einen anderen Browser-Host oder eine offizielle Preisdatenquelle.')
