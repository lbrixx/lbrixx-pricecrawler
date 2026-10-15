import os
import re
import sys
import subprocess
from datetime import datetime, timezone
import streamlit as st
from playwright.sync_api import sync_playwright

ASIN = 'B0DYV3XVZ6'
URL = f'https://www.amazon.de/dp/{ASIN}'
st.set_page_config(page_title='LBrixx Amazon Diagnose V9', layout='wide')
st.title('LBrixx Amazon-Diagnose V9')
st.write('Artikel 17003 · FUNWHOLE Future Bus · ASIN B0DYV3XVZ6')
st.link_button('Amazon-Artikel öffnen', URL)
st.caption('Referenzpreis im eigenen Browser: 52,90 € inklusive Versand. Kein automatisch bestätigter Preis.')

@st.cache_resource(show_spinner='Chromium wird für diese Server-Sitzung vorbereitet …')
def ensure_browser():
    with sync_playwright() as p:
        path = p.chromium.executable_path
    if os.path.isfile(path):
        return path, 'Bereits installiert'
    proc = subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'], capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError('Chromium-Installation fehlgeschlagen: ' + (proc.stderr or proc.stdout)[-2500:])
    with sync_playwright() as p:
        path = p.chromium.executable_path
    if not os.path.isfile(path):
        raise RuntimeError(f'Installation meldete Erfolg, Browserdatei fehlt aber: {path}')
    return path, 'Neu installiert'

if st.button('Browser-Screenshot und Preisbereiche prüfen', type='primary'):
    st.write('Prüfzeitpunkt (UTC):', datetime.now(timezone.utc).isoformat())
    try:
        path, status = ensure_browser()
        st.success(f'Chromium: {status}')
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
            context = browser.new_context(locale='de-DE', timezone_id='Europe/Berlin', viewport={'width': 1365, 'height': 900})
            page = context.new_page()
            response = page.goto(URL, wait_until='domcontentloaded', timeout=60000)
            page.wait_for_timeout(4000)
            st.write('HTTP-Status:', response.status if response else 'Unbekannt')
            st.write('Seitentitel:', page.title())
            st.write('Seitenadresse:', page.url)
            screenshot = page.screenshot(full_page=True, timeout=30000)
            st.image(screenshot, caption='Screenshot des automatisierten Browsers', use_container_width=True)
            st.download_button('Screenshot herunterladen', screenshot, file_name='amazon_17003_browser.png', mime='image/png')
            selectors = ['#corePrice_feature_div', '#corePriceDisplay_desktop_feature_div', '#apex_desktop', '#buybox', '.a-price', '[data-a-color="price"]']
            for selector in selectors:
                loc = page.locator(selector)
                count = min(loc.count(), 3)
                snippets = []
                for i in range(count):
                    try:
                        value = loc.nth(i).inner_text(timeout=2000).strip()
                        if value:
                            snippets.append(value[:350])
                    except Exception:
                        pass
                st.write(f'{selector}:', snippets if snippets else 'Keine sichtbaren Inhalte')
            text = page.locator('body').inner_text(timeout=10000)
            st.write('Referenzpreis im sichtbaren Text:', 'Ja' if re.search(r'52\s*[,\.]\s*90', text) else 'Nein')
            st.info('Preisangaben sind Diagnosewerte, keine automatisch bestätigten Kaufpreise. Versand und Verkäufer müssen separat geprüft werden.')
            browser.close()
    except Exception as exc:
        st.error(f'Browser-Test fehlgeschlagen: {type(exc).__name__}: {exc}')
        st.info('Falls Linux-Systembibliotheken fehlen, die Streamlit-Build-Logs prüfen. Browserinstallationen im laufenden Container sind möglicherweise nach einem Neustart verloren.')
