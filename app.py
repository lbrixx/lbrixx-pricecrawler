import re
from datetime import datetime, timezone
import requests
import streamlit as st
from bs4 import BeautifulSoup

ASIN = 'B0DYV3XVZ6'
URL = f'https://www.amazon.de/dp/{ASIN}'
st.set_page_config(page_title='LBrixx Amazon Diagnose V3')
st.title('LBrixx Amazon-Diagnose V3')
st.write(f'**Artikel:** 17003 · FUNWHOLE Future Bus · **ASIN:** {ASIN}')
st.link_button('Amazon-Artikel öffnen', URL)
st.caption('Referenzpreis im Browser: 52,90 € inklusive Versand. Kein automatisch bestätigter Preis.')

if st.button('HTML auf Referenzpreis prüfen', type='primary'):
    try:
        with st.spinner('Amazon-Produktseite wird abgerufen …'):
            response = requests.get(URL, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
                'Accept-Language': 'de-DE,de;q=0.9,en;q=0.8',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            }, timeout=25)
        html = response.text
        soup = BeautifulSoup(html, 'html.parser')
        st.write('**Prüfzeitpunkt (UTC):**', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
        st.write('**HTTP-Status:**', response.status_code)
        st.write('**Antwortgröße:**', len(response.content), 'Bytes')
        st.write('**Seitentitel:**', soup.title.get_text(' ', strip=True) if soup.title else 'Nicht vorhanden')
        st.write('**Weiterleitungsziel:**', response.url)
        patterns = {
            '52,90': r'52,90',
            '52.90': r'52\.90',
            '5.290 / 5290 (mögliche Cent-Angabe)': r'(?<!\d)5290(?!\d)',
            'HTML-kodiert (52&#44;90 / 52\\u002c90)': r'52(?:&#44;|&#x2c;|\\u002[cC]|%2[Cc])90',
            'Getrennte Preisbestandteile (52 + 90)': r'52\s*</span>\s*<span[^>]*>\s*90',
        }
        st.subheader('Fundstellen des Referenzpreises')
        found = False
        for label, pattern in patterns.items():
            matches = list(re.finditer(pattern, html, flags=re.IGNORECASE))
            st.write(f'**{label}:** {len(matches)} Treffer')
            if matches:
                found = True
                with st.expander(f'Kontext für {label} (maximal 3 Beispiele)'):
                    for m in matches[:3]:
                        excerpt = html[max(0, m.start()-120):min(len(html), m.end()+120)]
                        excerpt = re.sub(r'(?i)(token|session|cookie|password|secret|key)(["\s:=]+)[^\s,"}]+', r'\1\2[REDACTED]', excerpt)
                        st.code(excerpt[:280], language='text')
        st.subheader('Diagnose')
        if found:
            st.warning('Der Referenzbetrag kommt im HTML vor. Das beweist noch nicht, dass es der aktuelle Angebotspreis ist; er kann auch in anderen Daten stehen.')
        else:
            st.info('Keine der geprüften Schreibweisen gefunden. Möglicherweise liefert Amazon an den Server eine andere Preisdarstellung oder keinen Angebotspreis.')
        st.caption('Es werden keine vollständigen HTML-Seiten, Cookies oder Sitzungsdaten angezeigt oder gespeichert.')
    except requests.RequestException as exc:
        st.error(f'Netzwerkfehler: {type(exc).__name__}: {exc}')
