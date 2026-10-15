"""Conservative, user-confirmed merchant discovery. No automatic matching/writes."""
import re, time
from difflib import SequenceMatcher
from urllib.parse import urljoin, urlparse, quote
import requests
from bs4 import BeautifulSoup

HOSTS={'Brickmo':('www.brickmo.com','brickmo.com'), 'Lootstore':('lootstore.de','www.lootstore.de')}
STOP={'und','der','die','das','the','mit','von','für','set','bausteine','klemmbausteine','building','blocks','brick','bricks','modell','model'}

def tokens(text):
    return [x for x in re.findall(r'[a-z0-9]+',str(text).lower()) if len(x)>2 and x not in STOP]

def candidate_url(merchant, href):
    p=urlparse(href)
    if p.scheme!='https' or p.hostname not in HOSTS[merchant]: return ''
    if merchant=='Lootstore':
        if not re.fullmatch(r'/products/[^/]+/?',p.path): return ''
    else:
        # Brickmo product paths are category-dependent and end in a numeric product identifier.
        if not re.search(r'/\d{3,}/[^/]+',p.path): return ''
    return f'https://{p.hostname}{p.path}'.rstrip('/')

def score(row, title, link):
    sku=str(row.get('sku','')).strip().lower()
    ean=re.sub(r'\D','',str(row.get('ean','')))
    hay=(title+' '+urlparse(link).path.replace('-',' ')).lower()
    words=tokens(title)
    product=tokens(row.get('description',''))
    similarity=SequenceMatcher(None,' '.join(product),' '.join(words)).ratio() if product and words else 0
    overlap=len(set(product)&set(words))/max(1,len(set(product)))
    # Strong SKU match must respect alphanumeric boundaries.
    sku_match=bool(sku and len(sku)>=4 and re.search(r'(?<![a-z0-9])'+re.escape(sku)+r'(?![a-z0-9])',hay))
    ean_match=bool(len(ean) in (8,12,13,14) and re.search(r'(?<!\d)'+ean+r'(?!\d)',hay))
    if not (sku_match or ean_match or (similarity>=0.78 and overlap>=0.65)):
        return 0, 'Kein hinreichend eindeutiger Treffer'
    points=min(100,round((65 if ean_match else 0)+(55 if sku_match else 0)+25*similarity+15*overlap))
    reasons=[]
    if ean_match: reasons.append('EAN')
    if sku_match: reasons.append('SKU')
    if similarity>=0.5: reasons.append('Name')
    return points,', '.join(reasons)

def search(row,merchant,timeout=12):
    """Uses public shop search; fail closed when site changes or throttles."""
    sku=str(row.get('sku','')).strip()
    ean=re.sub(r'\D','',str(row.get('ean','')))
    term=sku or ean
    if not term: return [],'Keine SKU oder EAN vorhanden'
    if merchant=='Lootstore':
        base='https://lootstore.de'; endpoint=base+'/search?q='+quote(term)
    else:
        base='https://www.brickmo.com'; endpoint=base+'/search?sSearch='+quote(term)
    r=requests.get(endpoint,headers={'User-Agent':'Mozilla/5.0 (compatible; LBrixxCatalogMatcher/1.0)','Accept-Language':'de-DE,de;q=0.9'},timeout=timeout)
    if r.status_code in (403,429): return [],f'{merchant} begrenzt Suchanfragen (HTTP {r.status_code})'
    r.raise_for_status()
    soup=BeautifulSoup(r.text,'html.parser')
    found={}
    for a in soup.select('a[href]'):
        link=candidate_url(merchant,urljoin(base,a.get('href','')))
        if not link: continue
        title=a.get('title','').strip() or a.get_text(' ',strip=True)
        if len(title)<8:
            parent=a.find_parent(['article','li','div'])
            if parent: title=parent.get_text(' ',strip=True)[:180]
        points,reason=score(row,title,link)
        if points>=60 and (link not in found or points>found[link]['score']):
            found[link]={'url':link,'title':title[:160],'score':points,'reason':reason}
    return sorted(found.values(),key=lambda x:-x['score'])[:3],''
