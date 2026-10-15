import streamlit as st
from playwright.sync_api import sync_playwright
from datetime import datetime, timezone
import subprocess
import os
from pathlib import Path

ASIN = 'B0DYV3XVZ6'
URL = f'https://www.amazon.de/dp/{ASIN}'
st.title('LBrixx Amazon-Diagnose V5')
st.write(f'**Artikel:** 17003 · FUNWHOLE Future Bus · **ASIN:** {ASIN}')
st.link_button('Amazon-Artikel öffnen', URL)
st.caption('Referenzpreis aus dem Browser: 52,90 € inkl. Versand. Kein automatisch bestätigter Preis.')
@st.cache_resource(show_spinner='Chromium wird einmalig installiert …')
def prepare_browser():
    # Use an application-owned directory to avoid Playwright cache path mismatches.
    browser_dir = '/tmp/lbrixx-playwright-browsers'
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = browser_dir
    result = subprocess.run(
        ['python', '-m', 'playwright', 'install', 'chromium'],
        capture_output=True, text=True, timeout=180,
        env={**os.environ, 'PLAYWRIGHT_BROWSERS_PATH': browser_dir}
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout)[-1800:])
    return browser_dir

if st.button('Browser-Abfrage testen', type='primary'):
    st.write('Prüfzeitpunkt (UTC):', datetime.now(timezone.utc).isoformat(timespec='seconds'))
    try:
        prepare_browser()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
            page = browser.new_page(locale='de-DE', timezone_id='Europe/Berlin', viewport={'width': 1280, 'height': 900})
            response = page.goto(URL, wait_until='domcontentloaded', timeout=45000)
            page.wait_for_timeout(3500)
            st.write('HTTP-Status:', response.status if response else 'Keine Antwort')
            st.write('Seitentitel:', page.title())
            st.write('Endgültige URL:', page.url)
            st.write('CAPTCHA-Hinweis:', 'Ja' if 'captcha' in page.title().lower() or 'validatecaptcha' in page.url.lower() else 'Nicht erkannt')
            candidates = []
            for selector in ['#corePrice_feature_div .a-offscreen', '.priceToPay .a-offscreen', '#priceblock_ourprice', '#priceblock_dealprice', '#apex_desktop .a-price .a-offscreen']:
                for value in page.locator(selector).all_text_contents():
                    value = value.strip()
                    if value and value not in candidates:
                        candidates.append(value)
            if candidates:
                st.write('Preis-Kandidaten (noch nicht verifiziert):', candidates[:12])
                st.warning('Bitte Kandidaten mit dem sichtbaren Kaufpreis und Versand vergleichen.')
            else:
                st.info('Kein Preis in den geprüften sichtbaren Preisfeldern gefunden.')
            browser.close()
    except Exception as exc:
        st.error(f'Browser-Test fehlgeschlagen: {type(exc).__name__}: {exc}')
        st.info('Falls Chromium fehlt: Streamlit-App neu starten und Installationslogs kontrollieren.')
