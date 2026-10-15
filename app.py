import re
from datetime import datetime, timezone
import streamlit as st
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URL = 'https://www.amazon.de/dp/B0DYV3XVZ6'
st.set_page_config(page_title='LBrixx Amazon Diagnose V4', page_icon='🔎')
st.title('LBrixx Amazon-Diagnose V4')
st.write('**Artikel:** 17003 · FUNWHOLE Future Bus · **ASIN:** B0DYV3XVZ6')
st.link_button('Amazon-Artikel öffnen', URL)
st.caption('Referenzwert aus deinem Browser: 52,90 € inkl. Versand. Kein automatisch bestätigter Preis.')

if st.button('Browser-Abfrage testen', type='primary'):
    with st.spinner('Chromium öffnet die Amazon-Seite …'):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
                page = browser.new_page(locale='de-DE', timezone_id='Europe/Berlin', viewport={'width': 1280, 'height': 900})
                response = page.goto(URL, wait_until='domcontentloaded', timeout=45000)
                page.wait_for_timeout(3500)
                st.write('**Zeitpunkt (UTC):**', datetime.now(timezone.utc).isoformat(timespec='seconds'))
                st.write('**HTTP-Status:**', response.status if response else 'Keine Antwort')
                st.write('**Endgültige URL:**', page.url)
                st.write('**Seitentitel:**', page.title())
                body_text = page.locator('body').inner_text(timeout=12000)
                blocked = any(x in body_text.lower() for x in ['zeichen eingeben', 'geben sie die zeichen', 'captcha', 'robot check'])
                st.write('**CAPTCHA-/Blockadehinweis:**', 'Möglicherweise' if blocked else 'Kein typischer Hinweis erkannt')
                selectors = ['#corePrice_feature_div .a-offscreen', '#corePriceDisplay_desktop_feature_div .a-offscreen', '.priceToPay .a-offscreen', '#priceblock_ourprice', '#priceblock_dealprice', '#apex_desktop .a-offscreen']
                candidates = []
                for selector in selectors:
                    try:
                        values = page.locator(selector).all_text_contents()
                        for value in values[:5]:
                            cleaned = ' '.join(value.split())
                            if cleaned and re.search(r'\d+[,.]\d{2}', cleaned):
                                candidates.append({'Quelle': selector, 'Text': cleaned[:100]})
                    except Exception:
                        pass
                if candidates:
                    st.write('**Preis-Kandidaten (nicht verifiziert):**')
                    st.dataframe(candidates, use_container_width=True)
                else:
                    st.warning('Kein Preis in den geprüften sichtbaren Preisfeldern gefunden.')
                st.write('**Referenzpreis im sichtbaren Seitentext:**', 'Ja' if '52,90' in body_text or '52.90' in body_text else 'Nein')
                st.info('Ein Treffer ist noch kein bestätigter Kaufpreis. Versand, Gutscheine und Verkäufer müssen separat geprüft werden.')
                browser.close()
        except PlaywrightTimeoutError:
            st.error('Zeitüberschreitung beim Browserabruf. Kein Preis erfasst.')
        except Exception as exc:
            st.error(f'Browserabruf fehlgeschlagen: {type(exc).__name__}: {str(exc)[:500]}')
            st.caption('Prüfe, ob Chromium auf dem Hoster installiert wurde (packages.txt und postBuild).')
