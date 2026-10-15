import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
from datetime import datetime, timezone
import re

st.set_page_config(page_title='LBrixx eBay-Test', page_icon='🔎')
st.title('LBrixx Price-Crawler – eBay-Test V11')
st.write('**Artikel:** 17003 · FUNWHOLE Future Bus · **Hersteller:** F9060')
st.caption('Diagnose öffentlich zugänglicher eBay-Suchergebnisse. Keine verifizierten Preise ohne eindeutige Zuordnung.')
queries=['FUNWHOLE F9060', 'FUNWHOLE 17003']
for q in queries:
    st.link_button(f'eBay-Suche öffnen: {q}', f'https://www.ebay.de/sch/i.html?_nkw={quote(q)}&LH_ItemCondition=1000')

def fetch(q):
    url=f'https://www.ebay.de/sch/i.html?_nkw={quote(q)}&LH_ItemCondition=1000'
    try:
        r=requests.get(url, headers={'User-Agent':'Mozilla/5.0 (compatible; LBrixxPriceResearch/0.1)'}, timeout=18)
        soup=BeautifulSoup(r.text,'html.parser')
        title=soup.title.get_text(' ',strip=True) if soup.title else '(kein Titel)'
        text=soup.get_text(' ',strip=True)
        challenge=bool(re.search(r'captcha|robot check|verify you are human|unusual traffic',text,re.I))
        cards=soup.select('li.s-item')
        examples=[]
        for card in cards[:40]:
            name=card.select_one('.s-item__title')
            price=card.select_one('.s-item__price')
            ship=card.select_one('.s-item__shipping, .s-item__logisticsCost')
            a=card.select_one('a.s-item__link')
            if name and price:
                examples.append({'Titel':name.get_text(' ',strip=True), 'Angezeigter Preis':price.get_text(' ',strip=True), 'Versandhinweis':ship.get_text(' ',strip=True) if ship else 'Nicht eindeutig', 'Link':a.get('href','') if a else ''})
            if len(examples)>=12:break
        return {'url':r.url,'status':r.status_code,'title':title,'size':len(r.content),'challenge':challenge,'examples':examples}
    except requests.RequestException as e:
        return {'error':str(e)}

if st.button('eBay-Suchseiten diagnostizieren',type='primary'):
    st.write('**Prüfzeitpunkt (UTC):**',datetime.now(timezone.utc).isoformat())
    for q in queries:
        st.subheader(q)
        with st.spinner('Öffentliche Suchseite wird geprüft …'):
            result=fetch(q)
        if 'error' in result:
            st.error(result['error']);continue
        st.write('HTTP-Status:',result['status'],'· Antwortgröße:',result['size'],'Bytes')
        st.write('Seitentitel:',result['title'])
        st.write('Weiterleitung:',result['url'])
        if result['challenge']:st.warning('Mögliche Zugriffsprüfung erkannt.')
        if result['examples']:
            st.info('Unverifizierte Suchtreffer: Preis und Versand müssen vor Übernahme geprüft werden.')
            st.dataframe(result['examples'],use_container_width=True,hide_index=True)
        else:st.warning('Keine strukturierten Angebote extrahierbar. Kein Preis erfunden.')
st.caption('Hinweis: eBay kann automatisierte Zugriffe beschränken. Für den dauerhaften Betrieb ist eine autorisierte API vorzuziehen.')
