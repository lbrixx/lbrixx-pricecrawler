import sys
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import streamlit as st

ASIN = 'B0DYV3XVZ6'
URL = f'https://www.amazon.de/dp/{ASIN}'
st.set_page_config(page_title='LBrixx Amazon-Diagnose V6', page_icon='🔎')
st.title('LBrixx Amazon-Diagnose V6')
st.write(f'**Artikel:** 17003 · FUNWHOLE Future Bus · **ASIN:** {ASIN}')
st.link_button('Amazon-Artikel öffnen', URL)
st.caption('Referenzpreis aus deinem Browser: 52,90 € inkl. Versand. Nicht automatisch bestätigt.')

with st.expander('Python- und Browser-Umgebung'):
    st.code(f'Python: {sys.executable}\nVersion: {sys.version.split()[0]}\nPLAYWRIGHT_BROWSERS_PATH: {os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "(nicht gesetzt)")}\nChromium im PATH: {shutil.which("chromium") or shutil.which("chromium-browser") or "nicht gefunden"}')

if st.button('Browser-Abfrage testen', type='primary'):
    st.write('**Prüfzeitpunkt (UTC):**', datetime.now(timezone.utc).isoformat())
    try:
        import playwright
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    except ImportError as exc:
        st.error(f'Playwright ist in der aktiven Python-Umgebung nicht installiert: {exc}')
        st.info('Prüfe requirements.txt im Hauptverzeichnis und starte die Streamlit-App neu (Reboot).')
        st.stop()
    try:
        with sync_playwright() as p:
            with p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage']) as browser:
                page = browser.new_page(locale='de-DE', timezone_id='Europe/Berlin', viewport={'width': 1366, 'height': 900})
                response = page.goto(URL, wait_until='domcontentloaded', timeout=45000)
                page.wait_for_timeout(2500)
                st.write('**HTTP-Status:**', response.status if response else 'Keine Antwort')
                st.write('**Seitentitel:**', page.title())
                st.write('**Finale URL:**', page.url)
                body = page.locator('body').inner_text(timeout=10000)
                if any(term in body.lower() for term in ('captcha', 'robot check', 'zeichen eingeben')):
                    st.warning('Möglicher Bot-/CAPTCHA-Hinweis in der Seite.')
                candidates = []
                for selector in ('#corePrice_feature_div .a-offscreen', '#corePriceDisplay_desktop_feature_div .a-offscreen', '.priceToPay .a-offscreen', '#priceblock_ourprice', '#priceblock_dealprice'):
                    for node in page.locator(selector).all()[:5]:
                        value = node.inner_text().strip()
                        if value and value not in candidates:
                            candidates.append(value)
                if candidates:
                    st.warning('Unverifizierte Preis-Kandidaten (nicht als Angebotspreis bestätigt):')
                    st.write(candidates)
                else:
                    st.info('Keine Preis-Kandidaten in den geprüften Elementen gefunden.')
                st.caption('Die Browser-Abfrage allein bestätigt weder den Gesamtpreis noch die Versandkosten.')
    except Exception as exc:
        st.error(f'Browser-Test fehlgeschlagen: {type(exc).__name__}: {exc}')
        st.info('Bei fehlender Chromium-Datei: Installationslogs prüfen und sicherstellen, dass der Browser im gleichen PLAYWRIGHT_BROWSERS_PATH installiert wurde.')
