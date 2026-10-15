import io
import re
from datetime import datetime, timezone
import streamlit as st
from playwright.sync_api import sync_playwright, Error as PlaywrightError

URL = 'https://www.amazon.de/dp/B0DYV3XVZ6'
st.set_page_config(page_title='LBrixx Amazon-Diagnose V8', layout='wide')
st.title('LBrixx Amazon-Diagnose V8')
st.write('**Artikel:** 17003 · FUNWHOLE Future Bus · **ASIN:** B0DYV3XVZ6')
st.link_button('Amazon-Artikel öffnen', URL)
st.caption('Referenzpreis aus deinem Browser: 52,90 € inklusive Versand. Kein automatisch bestätigter Preis.')
st.info('Diese Diagnose zeigt die tatsächlich gerenderte Amazon-Seite. Keine automatischen Preise werden erfunden oder gespeichert.')

if st.button('Browser-Screenshot und Preisbereiche prüfen', type='primary'):
    st.write('Prüfzeitpunkt (UTC):', datetime.now(timezone.utc).isoformat())
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
            context = browser.new_context(locale='de-DE', timezone_id='Europe/Berlin', viewport={'width': 1365, 'height': 900})
            page = context.new_page()
            response = page.goto(URL, wait_until='domcontentloaded', timeout=45000)
            page.wait_for_timeout(3500)
            st.write('HTTP-Status:', response.status if response else 'Keine Antwort')
            st.write('Seitentitel:', page.title())
            st.write('Endgültige URL:', page.url)
            screenshot = page.screenshot(full_page=True, animations='disabled')
            st.subheader('Screenshot der Server-Browsersitzung')
            st.image(screenshot, caption='Was Chromium auf Streamlit tatsächlich sieht', use_container_width=True)
            st.download_button('Screenshot herunterladen', screenshot, file_name='amazon_17003_server.png', mime='image/png')
            st.subheader('Sichtbare Preisbereiche')
            selectors = ['#corePrice_feature_div', '#corePriceDisplay_desktop_feature_div', '#apex_desktop', '#price', '.a-price', '#buybox', '#availability', '#deliveryBlockMessage', '#contextualIngressPtLabel']
            found = 0
            for selector in selectors:
                locator = page.locator(selector)
                count = min(locator.count(), 4)
                for i in range(count):
                    item = locator.nth(i)
                    try:
                        if item.is_visible(timeout=1000):
                            snippet = re.sub(r'\s+', ' ', item.inner_text(timeout=2000)).strip()[:500]
                            if snippet:
                                st.write(f'**{selector}**: {snippet}')
                                found += 1
                    except Exception:
                        pass
            if not found:
                st.warning('Keiner der untersuchten Preis- oder Angebotsbereiche enthielt sichtbaren Text.')
            st.caption('Preisangaben in diesen Bereichen sind zunächst nur Diagnosehinweise und noch kein verifizierter Gesamtpreis inklusive Versand.')
            context.close()
            browser.close()
    except PlaywrightError as exc:
        st.error(f'Browserfehler: {exc}')
        st.info('Wenn Chromium fehlt, muss es in der aktiven Python-Umgebung mit python -m playwright install chromium installiert werden. Die erfolgreiche V7-Browserinstallation kann nach einem Neustart verloren gehen.')
    except Exception as exc:
        st.error(f'Diagnose fehlgeschlagen: {type(exc).__name__}: {exc}')
