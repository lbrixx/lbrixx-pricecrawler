import os
import re
import sys
import subprocess
from datetime import datetime, timezone
import streamlit as st
from playwright.sync_api import sync_playwright

ASIN = 'B0DYV3XVZ6'
URL = f'https://www.amazon.de/dp/{ASIN}'
st.set_page_config(page_title='LBrixx Amazon-Diagnose V10', layout='wide')
st.title('LBrixx Amazon-Diagnose V10')
st.write('Artikel 17003 · FUNWHOLE Future Bus · ASIN B0DYV3XVZ6')
st.link_button('Amazon-Artikel öffnen', URL)
st.caption('Referenzpreis im eigenen Browser: 52,90 € inkl. Versand. Noch nicht automatisch bestätigt.')

@st.cache_resource(show_spinner='Chromium wird vorbereitet …')
def ensure_browser():
    with sync_playwright() as p:
        path = p.chromium.executable_path
    if os.path.isfile(path):
        return 'Bereits installiert'
    proc = subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'], capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError('Chromium-Installation fehlgeschlagen: ' + (proc.stderr or proc.stdout)[-1800:])
    with sync_playwright() as p:
        if not os.path.isfile(p.chromium.executable_path):
            raise RuntimeError('Browserdatei nach Installation nicht gefunden')
    return 'Neu installiert'

def dismiss_cookie_dialog(page):
    """Only reject optional cookies; never auto-accept advertising cookies."""
    candidates = [
        'input#sp-cc-rejectall-link',
        '#sp-cc-rejectall-link',
        'button:has-text("Alle ablehnen")',
        'button:has-text("Ablehnen")',
        'button:has-text("Nur notwendige Cookies")',
        'input[value="Alle ablehnen"]',
    ]
    for selector in candidates:
        try:
            loc = page.locator(selector).first
            if loc.count() and loc.is_visible(timeout=1500):
                loc.click(timeout=4000)
                page.wait_for_timeout(1200)
                return f'Ablehnen-Schaltfläche betätigt ({selector})'
        except Exception:
            continue
    return 'Keine passende Ablehnen-Schaltfläche gefunden (Dialog eventuell weiterhin sichtbar)'

if st.button('Cookie-Dialog ablehnen und Preisbereiche prüfen', type='primary'):
    st.write('Prüfzeitpunkt (UTC):', datetime.now(timezone.utc).isoformat())
    try:
        st.success('Chromium: ' + ensure_browser())
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
            try:
                context = browser.new_context(locale='de-DE', timezone_id='Europe/Berlin', viewport={'width': 1365, 'height': 900})
                page = context.new_page()
                response = page.goto(URL, wait_until='domcontentloaded', timeout=60000)
                page.wait_for_timeout(2500)
                st.write('HTTP-Status:', response.status if response else 'Unbekannt')
                st.write('Seitentitel:', page.title())
                st.write('Cookie-Dialog:', dismiss_cookie_dialog(page))
                page.wait_for_timeout(2500)
                st.write('Aktuelle Adresse:', page.url)
                screenshot = page.screenshot(full_page=True, timeout=30000)
                st.image(screenshot, caption='Amazon-Seite nach Cookie-Behandlung', use_container_width=True)
                st.download_button('Screenshot herunterladen', screenshot, file_name='lbrixx_amazon_v10.png', mime='image/png')
                selectors = ['#corePrice_feature_div', '#corePriceDisplay_desktop_feature_div', '#apex_desktop', '#buybox', '.a-price', '[data-a-color="price"]']
                st.subheader('Sichtbare Preisbereiche (Diagnose)')
                for selector in selectors:
                    snippets = []
                    loc = page.locator(selector)
                    for i in range(min(loc.count(), 4)):
                        try:
                            if loc.nth(i).is_visible(timeout=1000):
                                value = loc.nth(i).inner_text(timeout=2000).strip()
                                if value:
                                    snippets.append(value[:300])
                        except Exception:
                            pass
                    st.write(f'`{selector}`:', snippets or 'Keine sichtbaren Inhalte')
                body = page.locator('body').inner_text(timeout=10000)
                st.write('52,90 € im sichtbaren Seitentext:', 'Ja' if re.search(r'52\s*[,\.]\s*90', body) else 'Nein')
                st.write('Hinweis auf Anmeldung:', 'Ja' if 'lege diesen Artikel in deinen Einkaufswagen' in body.lower() or 'melde dich an' in body.lower() else 'Nicht eindeutig')
                st.info('Gefundene Preisfragmente sind keine bestätigten Kaufpreise. Verkäufer, Verfügbarkeit und Versandkosten müssen separat validiert werden.')
            finally:
                browser.close()
    except Exception as exc:
        st.error(f'Test fehlgeschlagen: {type(exc).__name__}: {exc}')
