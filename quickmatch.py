"""Shop search with explicit human confirmation. Exact SKU matches are preferred."""
import re
from urllib.parse import urljoin, urlparse, quote, parse_qs
from difflib import SequenceMatcher
import requests
from bs4 import BeautifulSoup

HOSTS={'Brickmo':('www.brickmo.com','brickmo.com'), 'Lootstore':('lootstore.de','www.lootstore.de')}
STOP={'und','der','die','das','the','mit','von','für','set','bausteine','klemmbausteine','building','blocks','brick','bricks','modell','model'}

def tokens(text):
    return [x for x in re.findall(r'[a-z0-9]+',str(text).lower()) if len(x)>2 and x not in STOP]

def candidate_url(merchant, href):
    p=urlparse(href)
    if p.scheme not in ('http','https') or p.hostname not in HOSTS[merchant]: return ''
    if merchant=='Lootstore':
        if not re.fullmatch(r'/products/[^/]+/?',p.path): return ''
    elif not re.search(r'/\d{3,}/[^/]+',p.path):
        return ''
    return f'https://{p.hostname}{p.path}'.rstrip('/')

def sku_variants(sku):
    sku=str(sku).strip().upper()
    variants={sku} if sku else set()
    m=re.fullmatch(r'F(O?)(\d{4,})',sku)
    if m: variants.update({'F'+m.group(2),'FO'+m.group(2)})
    return sorted(variants,key=lambda x:(x!=sku,len(x)))

def score(row,title,link):
    variants=sku_variants(row.get('sku',''))
    ean=re.sub(r'\D','',str(row.get('ean','')))
    parsed=urlparse(link)
    path=parsed.path
    query_number=' '.join(parse_qs(parsed.query).get('number', []))
    hay=(str(title)+' '+path.replace('-',' ')+' '+query_number).lower()
    exact=any(re.search(r'(?<![a-z0-9])'+re.escape(v.lower())+r'(?![a-z0-9])',hay) for v in variants if len(v)>=4)
    ean_match=bool(len(ean) in (8,12,13,14) and re.search(r'(?<!\d)'+ean+r'(?!\d)',hay))
    a=tokens(row.get('description','')); b=tokens(title+' '+path.replace('-',' '))
    overlap=len(set(a)&set(b))/max(1,len(set(a)))
    similarity=SequenceMatcher(None,' '.join(a),' '.join(b)).ratio() if a and b else 0
    if exact or ean_match:
        return min(100,75+(15 if exact and ean_match else 0)+round(10*overlap)), 'Exakte SKU' if exact else 'EAN'
    return round(35*overlap+20*similarity), 'Möglicher Namens-Treffer – bitte prüfen'

def search(row,merchant,timeout=12):
    sku=str(row.get('sku','')).strip()
    ean=re.sub(r'\D','',str(row.get('ean','')))
    terms=sku_variants(sku) or ([ean] if ean else [])
    if not terms:return [],'Keine SKU oder EAN vorhanden'
    base='https://lootstore.de' if merchant=='Lootstore' else 'https://www.brickmo.com'
    found={}; notes=[]
    session=requests.Session()
    session.headers.update({'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/123.0 Safari/537.36','Accept-Language':'de-DE,de;q=0.9'})
    def add(link,title,source):
        link=candidate_url(merchant,urljoin(base,str(link)))
        if not link:return False
        points,reason=score(row,str(title or ''),link)
        # Reject unrelated products even if their names contain generic words.
        if points < 75: return False
        if link not in found or points>found[link]['score']:
            found[link]={'url':link,'title':str(title or link.rsplit('/',1)[-1])[:160], 'score':points,'reason':reason,'source':source}
        return True
    for term in terms:
        if merchant=='Lootstore':
            try:
                r=session.get(base+'/search/suggest.json',params={'q':term,'resources[type]':'product','resources[limit]':'10'},timeout=timeout)
                if r.ok:
                    products=r.json().get('resources',{}).get('results',{}).get('products',[])
                    notes.append(f'{term}: Shopify {len(products)} Produkte')
                    for p in products:add(p.get('url',''),p.get('title',''),'Shopify')
                else:notes.append(f'{term}: Shopify HTTP {r.status_code}')
            except (requests.RequestException,ValueError,TypeError,AttributeError) as e:notes.append(f'{term}: Shopify {str(e)[:80]}')
        endpoints=[base+'/search?q='+quote(term)+'&type=product'] if merchant=='Lootstore' else [base+'/search?sSearch='+quote(term), base+'/search/index/sSearch/'+quote(term), base+'/?sSearch='+quote(term)]
        for endpoint in endpoints:
            try:
                r=session.get(endpoint,timeout=timeout)
                if not r.ok:
                    notes.append(f'{term}: HTML HTTP {r.status_code}');continue
                soup=BeautifulSoup(r.text,'html.parser'); count=0
                for a in soup.select('a[href]'):
                    href=a.get('href','')
                    title=a.get('title','').strip() or a.get_text(' ',strip=True)
                    if len(title)<8:
                        parent=a.find_parent(['article','li','div'])
                        if parent:title=parent.get_text(' ',strip=True)[:180]
                    if add(href,title,'HTML-Suche'):count+=1
                # Some storefronts encode product URLs in structured attributes.
                for el in soup.select('[data-url], [data-href]'):
                    add(el.get('data-url') or el.get('data-href'),el.get('title',''),'HTML-Datenattribut')
                notes.append(f'{term}: {count} Produktlinks, {len(found)} eindeutige URLs')
            except requests.RequestException as e:notes.append(f'{term}: HTML {str(e)[:80]}')
    # Brickmo's internal search sometimes returns unrelated recommendations.
    # A public search-engine index is a fallback, never a source of automatic confirmation.
    if merchant=='Brickmo' and not found:
        for term in terms:
            try:
                r=session.get('https://www.google.com/search',params={'q':f'site:brickmo.com/themenwelt/ "{term}"'},timeout=timeout,
                              headers={'User-Agent':'Mozilla/5.0'})
                if r.ok:
                    soup=BeautifulSoup(r.text,'html.parser')
                    for a in soup.select('a[href]'):
                        href=a.get('href','')
                        if href.startswith('/url?'): href=parse_qs(urlparse(href).query).get('q',[''])[0]
                        add(href,a.get_text(' ',strip=True),'Suchindex')
                    notes.append(f'{term}: externer Suchindex, {len(found)} exakte Treffer')
                else: notes.append(f'{term}: externer Suchindex HTTP {r.status_code}')
            except requests.RequestException as e: notes.append(f'{term}: externer Suchindex {str(e)[:80]}')
    ranked=sorted(found.values(),key=lambda x:(-x['score'],x['url']))
    results=ranked[:5]
    if not ranked:
        notes.append('Kein exakter SKU/EAN-Treffer; unpassende Produktlinks werden bewusst ausgeblendet')
    if not ranked:notes.append('Keine verwertbaren Produktlinks in den Shop-Antworten')
    return results,'; '.join(notes)
