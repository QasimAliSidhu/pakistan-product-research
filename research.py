"""Public discovery, timestamped product evidence, and Pakistan trend history."""
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
from datetime import datetime, timezone, date
from urllib.parse import urlparse, parse_qs, urljoin
from urllib.robotparser import RobotFileParser
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parent
LEAD=Path(os.environ.get('LEAD_FINDER_ROOT',str(ROOT.parent/'lead-finder')))
UA='PakistanProductResearch/1.0 (public product metadata; local research)'
SITES=['daraz.pk','priceoye.pk','ishopping.pk','telemart.pk','homeshopping.pk']
_robots={}
_cooldown={}
SPELLINGS={'jewelary':'jewellery','jewellary':'jewellery','jewlery':'jewellery','jewelry':'jewellery'}

def normalize_keyword(keyword):
    return re.sub(r'\b\w+\b',lambda match:SPELLINGS.get(match.group().lower(),match.group()),keyword.strip())

def resolve_search_url(url):
    """Resolve Google's result wrappers before applying the marketplace filter."""
    for _ in range(4):
        parsed=urlparse(url)
        if not re.fullmatch(r'(?:www\.)?google\.[a-z.]+',parsed.hostname or '') or parsed.path not in ('/url','/goto'):
            return url
        params=parse_qs(parsed.query)
        direct=next((v[0] for key,v in params.items() if key in ('q','url') and v[0].startswith(('http://','https://'))),None)
        if direct: return direct
        public_url(url)
        # Read only the redirect header, not the destination product page.
        with requests.get(url,headers={'User-Agent':UA},timeout=(5,10),allow_redirects=False,stream=True) as response:
            response.raise_for_status()
            if not response.is_redirect: raise ValueError('Google result redirect could not be resolved')
            url=urljoin(url,response.headers['Location'])
    raise ValueError('Google result redirect chain is too long')

def now(): return datetime.now(timezone.utc).isoformat()

def public_url(url):
    parsed=urlparse(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Only public HTTP(S) URLs are supported')
    if parsed.port not in (None,80,443): raise ValueError('Unsupported URL port')
    for addr in socket.getaddrinfo(parsed.hostname,parsed.port or 443):
        if not ipaddress.ip_address(addr[4][0]).is_global:
            raise ValueError('Private and local network pages are not supported')
    return url

def fetch(url, limit=2500000):
    public_url(url)
    for _ in range(4):
        with requests.get(url,headers={'User-Agent':UA},timeout=(8,18),allow_redirects=False,stream=True) as r:
            if r.is_redirect:
                url=public_url(urljoin(url,r.headers['Location']))
                continue
            r.raise_for_status()
            content=bytearray()
            for part in r.iter_content(32768):
                content.extend(part)
                if len(content)>limit: raise ValueError('Page exceeds collection size limit')
            return content.decode(r.encoding or 'utf-8',errors='replace'),url
    raise ValueError('Too many redirects')

def allowed(url):
    p=urlparse(url); origin=f'{p.scheme}://{p.netloc}'
    if origin not in _robots:
        rp=RobotFileParser()
        try:
            raw,_=fetch(origin+'/robots.txt',250000)
            rp.parse(raw.splitlines())
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code==404:
                rp.parse([])
            else: return False
        except Exception: return False
        _robots[origin]=rp
    return _robots[origin].can_fetch(UA,url)

def start_search():
    base=os.environ.get('OPENSERP_URL','http://127.0.0.1:7000').rstrip('/')
    try:
        if requests.get(base+'/health',timeout=2).ok: return None
    except requests.RequestException: pass
    exe=LEAD/'bin/openserp.exe'; config=LEAD/'config/openserp.yaml'
    if not exe.exists() or not config.exists(): return None
    (ROOT/'logs').mkdir(exist_ok=True)
    with (ROOT/'logs/openserp.log').open('a',encoding='utf-8') as log:
        proc=subprocess.Popen([str(exe),'serve','--config',str(config),'-a','127.0.0.1','-p','7000'],
            cwd=LEAD,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    for _ in range(25):
        if proc.poll() is not None: return None
        try:
            if requests.get(base+'/health',timeout=1).ok: return proc
        except requests.RequestException: pass
        time.sleep(.4)
    proc.terminate()
    return None

def search(query, limit=6):
    query=normalize_keyword(query)
    errors=[]
    site_match=re.search(r'site:([^\s]+)',query)
    site=site_match.group(1).lower() if site_match else None
    terms=[t for t in re.findall(r'[a-z0-9]+',re.sub(r'site:\S+','',query.lower()))
        if t not in {'price','prices','pakistan','wholesale','supplier','suppliers','moq','in','the','and','for',
            'online','shopping','buy','sale','ecommerce','market','research','analytics','demand','trends','sales','report','reports'}]
    def relevant(rows):
        result=[]
        for row in rows:
            hostname=(urlparse(row['url']).hostname or '').lower()
            if urlparse(row['url']).scheme not in ('http','https') or not hostname: continue
            if site and hostname!=site and not hostname.endswith('.'+site): continue
            text=normalize_keyword(row.get('title','')+' '+row.get('snippet','')+' '+row['url']).lower()
            if terms and sum(t in text for t in terms)<max(1,int(len(terms)*.6+.5)): continue
            result.append(row)
        return result
    base=os.environ.get('OPENSERP_URL','http://127.0.0.1:7000').rstrip('/')
    for engine in ('google','bing'):
        if time.time()<_cooldown.get(engine,0):
            errors.append(f'{engine}: cooling down after upstream rate limit'); continue
        try:
            r=requests.get(base+'/'+engine+'/search',params=dict(text=query,limit=limit,lang='EN',region='PK'),timeout=(3,45))
            r.raise_for_status(); payload=r.json()
            if isinstance(payload,dict) and payload.get('error'): raise ValueError(str(payload['error'])[:180])
            rows=payload if isinstance(payload,list) else payload.get('results',[])
            normalized=[]
            for x in rows:
                if not x.get('url') or x.get('type','organic')!='organic': continue
                try:
                    normalized.append(dict(title=x.get('title',''),url=resolve_search_url(x['url']),snippet=x.get('snippet',''),engine='openserp:'+engine))
                except Exception as exc: errors.append(f'{engine}: result redirect failed: {type(exc).__name__}')
            rows=normalized
            rows=relevant(rows)
            if rows: return rows,errors
            errors.append(f'{engine}: no relevant results matching product and site')
        except Exception as exc:
            if isinstance(exc,requests.HTTPError) and exc.response is not None and exc.response.status_code==429:
                _cooldown[engine]=time.time()+1800
            errors.append(f'{engine}: {str(exc)[:180]}')
    try:
        from ddgs import DDGS
        rows=DDGS(timeout=15).text(query,region='pk-en',max_results=limit)
        normalized=[dict(title=x.get('title',''),url=x.get('href',''),snippet=x.get('body',''),engine='ddgs') for x in rows if x.get('href')]
        normalized=relevant(normalized)
        if not normalized: errors.append('ddgs: no relevant results')
        return normalized,errors
    except Exception as exc:
        errors.append('ddgs: '+str(exc)[:180]); return [],errors

def walk(value):
    if isinstance(value,dict):
        yield value
        for child in value.values(): yield from walk(child)
    elif isinstance(value,list):
        for child in value: yield from walk(child)

def number(value):
    try:
        value=float(str(value).replace(',','').strip())
        return value if 0<=value<1e10 else None
    except (ValueError,TypeError): return None

def extract(html,url):
    soup=BeautifulSoup(html,'html.parser'); products=[]
    if (urlparse(url).hostname or '').removeprefix('www.')=='priceoye.pk':
        for card in soup.select('.productBox'):
            anchor=card.select_one('a[href]'); price=card.select_one('.price-box'); title=card.select_one('.p-title')
            if not anchor or not price or not title: continue
            amount=number(re.sub(r'[^\d.]','',price.get_text(' ',strip=True)))
            if amount is None: continue
            products.append(dict(title=title.get_text(' ',strip=True),url=urljoin(url,anchor['href']),
                source_page=url,price=amount,currency='PKR',price_kind='Advertised listing price; verify variant and stock',
                rating=None,reviews=None,sold_count=None,seller='PriceOye',availability='',
                evidence='PriceOye product card (.price-box; excludes crossed-out retail price)',observed_at=now()))
    # Read displayed sale prices; crossed-out comparison prices are excluded.
    for card in soup.select('.card-product, .product-card-wrapper, product-card'):
        if 'card-product' in card.get('class',[]) and card.parent and 'card' in card.parent.get('class',[]): card=card.parent
        anchor=card.select_one('.card__heading a[href], a.card-title[href], a[href*="/products/"]')
        if not anchor: continue
        sale=(card.select_one('.price--on-sale .price-item--sale') or card.select_one('.price__regular .price-item--regular') or card.select_one('.price__last') or card.select_one('.price'))
        if not sale: continue
        match=re.search(r'(?:Rs\.?|PKR)\s*([\d,]+(?:\.\d+)?)',sale.get_text(' ',strip=True),re.I)
        if not match: continue
        title=anchor.get_text(' ',strip=True) or anchor.get('title') or (card.find('img') or {}).get('alt','')
        if not title: continue
        products.append(dict(title=title,url=urljoin(url,anchor['href']),source_page=url,
            price=number(match[1]),currency='PKR' if (urlparse(url).hostname or '').endswith('.pk') or 'PKR' in sale.get_text() else 'Unverified rupee currency',price_kind='Displayed sale price; verify selected variant and stock',
            rating=None,reviews=None,sold_count=None,seller=urlparse(url).hostname,
            evidence='Public storefront product card',observed_at=now()))
    for script in soup.find_all('script',type='application/ld+json'):
        try: payload=json.loads(script.string or script.get_text())
        except (ValueError,TypeError): continue
        for obj in walk(payload):
            kind=obj.get('@type','')
            if not (kind=='Product' or isinstance(kind,list) and 'Product' in kind): continue
            offers=obj.get('offers') or {}
            if isinstance(offers,list): offers=offers[0] if offers else {}
            if not isinstance(offers,dict): offers={}
            rating=obj.get('aggregateRating') or {}
            seller=offers.get('seller') or {}
            products.append(dict(title=str(obj.get('name',''))[:400],url=urljoin(url,obj.get('url') or offers.get('url') or url),source_page=url,
                price=number(offers.get('price')),currency=offers.get('priceCurrency',''),
                price_kind='offer price',rating=number(rating.get('ratingValue')),
                reviews=number(rating.get('reviewCount') or rating.get('ratingCount')),
                sold_count=None,seller=seller.get('name','') if isinstance(seller,dict) else str(seller),
                availability=str(offers.get('availability','')),evidence='JSON-LD Product',observed_at=now()))
    # Daraz search pages commonly carry an embedded listItems JSON object.
    for script in soup.find_all('script'):
        raw=script.string or script.get_text()
        if 'listItems' not in raw: continue
        start=raw.find('{'); end=raw.rfind('}')
        try: payload=json.loads(raw[start:end+1])
        except ValueError: continue
        for obj in walk(payload):
            for item in obj.get('listItems',[]) if isinstance(obj.get('listItems'),list) else []:
                product_url=item.get('productUrl','')
                if product_url.startswith('//'): product_url='https:'+product_url
                if not product_url: continue
                products.append(dict(title=str(item.get('name',''))[:400],url=product_url,
                    price=number(item.get('price')),currency='PKR',price_kind='listing price',
                    rating=number(item.get('ratingScore')),reviews=number(item.get('review')),
                    sold_count=None,sold_label=str(item.get('itemSoldCntShow','')),
                    seller=str(item.get('sellerName','')),availability='',evidence='Daraz listItems',observed_at=now()))
    unique={}
    for p in products: unique.setdefault(p['url'],p)
    return list(unique.values())

def is_challenge(html):
    soup=BeautifulSoup(html,'html.parser')
    title=soup.title.get_text(' ',strip=True) if soup.title else ''
    for element in soup(['script','style','noscript']): element.decompose()
    text=soup.get_text(' ',strip=True)
    return bool(re.search(r'just a moment|access denied|security verification',title,re.I) or
        (len(text)<3000 and re.search(r'verify you are human|complete the captcha|unusual traffic',text,re.I)))

def relevant_offer(row,keyword):
    text=(row.get('title','')+' '+row.get('url','')).lower()
    if 'jewellery' in normalize_keyword(keyword).lower():
        return not re.search(r'smart.?watch|jewell?ery[- ]?box|raindrop[- ]box',text)
    return True

def merge_evidence(payloads,keyword):
    if not payloads: return None
    result=dict(payloads[0]); merged={}
    for payload in payloads:
        for row in payload.get('evidence',[]):
            from urllib.parse import urlsplit,urlunsplit,urlencode,parse_qsl
            parts=urlsplit(row['url'])
            query=urlencode([(k,v) for k,v in parse_qsl(parts.query) if not k.startswith('utm_') and k not in ('srsltid','fbclid','gclid')])
            key=(row.get('kind'),urlunsplit((parts.scheme,parts.netloc,parts.path,query,'')))
            old=merged.get(key)
            if old is None or (old.get('status')!='observed metadata' and row.get('status')=='observed metadata'):
                merged[key]=dict(row,relevant=relevant_offer(row,keyword))
    result['evidence']=list(merged.values())
    result['note']='Latest successful observations per listing are retained across runs; check each collection date. Prices are different SKUs, not sales or wholesale quotations.'
    return result

def inspect(result,kind):
    base=dict(result,kind=kind,observed_at=now(),price=None,currency='',rating=None,reviews=None,
        sold_count=None,evidence='Search result only',status='unverified')
    snippet_prices=re.findall(r'(?:Rs\.?|PKR)\s*([\d,]+(?:\.\d{1,2})?)',result.get('snippet',''),re.I)
    if len(set(snippet_prices))==1 and '/products/' in result['url']:
        base.update(price=number(snippet_prices[0]),currency='PKR',price_kind='Cached search snippet; verify current offer',evidence='Search snippet price (not a fresh page quote)')
    try:
        if not allowed(result['url']):
            base['status']='Collection unavailable or disallowed by robots.txt'; return [base]
        html,url=fetch(result['url'])
        if is_challenge(html):
            base['status']='Blocked by site; use a manual export'; return [base]
        products=extract(html,url)
        if products:
            return [dict(base,**{k:v for k,v in p.items() if k not in ('kind','status')},status='observed metadata') for p in products[:12]]
        base['status']='No structured product metadata found'
    except Exception as exc: base['status']=str(exc)[:200]
    return [base]

def analytics_context(keyword):
    # This public category is broader than an individual jewellery SKU.
    if not any(term in normalize_keyword(keyword).lower() for term in ('jewellery','handbag','fashion accessories','purse')):
        return []
    url='https://ecdb.com/resources/sample-data/market/pk/bags-accessories'
    item=dict(provider='ECDB',url=url,scope='Pakistan Bags & Accessories ecommerce market (includes Jewelry & Watches)',
        kind='category estimate',observed_at=now(),status='Public summary only; detailed history/API not connected',metrics=[])
    try:
        if not allowed(url):
            item['status']='Public category summary collection unavailable'; return [item]
        html,_=fetch(url); text=BeautifulSoup(html,'html.parser').get_text(' ',strip=True)
        revenue=re.search(r'generated an e-commerce revenue of US\$([\d,.]+)([mb]) in (20\d{2})',text)
        if revenue:
            value=float(revenue[1].replace(',',''))*(1e6 if revenue[2]=='m' else 1e9)
            item['metrics'].append(dict(name='Category annual ecommerce revenue',value=value,currency='USD',period=revenue[3],basis='ECDB reported estimate; broader category, not product sales'))
        share=re.search(r'Jewelry & Watches is the largest category, contributing ([\d.]+)%',text)
        if share:
            item['metrics'].append(dict(name='Jewelry & Watches share of Bags & Accessories revenue',value=float(share[1]),unit='%',period=revenue[3] if revenue else 'See source',basis='Combined jewelry and watches category'))
        if not item['metrics']: item['status']='Public source found; no recognized summary metrics extracted'
    except Exception as exc: item['status']='Collection failed: '+str(exc)[:180]
    return [item]

def collect(keyword,progress,sites=None):
    sites=sites or SITES
    original_keyword=keyword; keyword=normalize_keyword(keyword)
    evidence=[]; errors=[]; seen=set()
    if 'jewellery' in keyword.lower():
        for kind,url in [('marketplace','https://rawayat.com.pk/'),('marketplace','https://shaanstudio.com/'),('supplier','https://imwholesale.pk/')]:
            progress('Reading public jewellery storefront: '+urlparse(url).netloc)
            evidence.extend(inspect(dict(title='Jewellery storefront',url=url,snippet='',engine='direct public storefront'),kind))
            seen.add(url)
    queries=[('marketplace',f'site:{site} {keyword} price Pakistan',site) for site in sites]
    queries += [('supplier',f'{keyword} wholesale supplier Pakistan MOQ',None),('supplier',f'site:alibaba.com {keyword} wholesale supplier',None)]
    # Product-specific shops are often absent from a fixed electronics-oriented list.
    queries += [('marketplace',f'{keyword} online shopping Pakistan price',None),
        ('research',f'{keyword} ecommerce market demand research Pakistan',None)]
    for i,(kind,query,site) in enumerate(queries):
        progress(f'Searching {i+1}/{len(queries)}: {query}')
        results,failed=search(query,6); errors.extend(dict(query=query,error=e) for e in failed)
        for result in results:
            url=result['url']
            if url in seen: continue
            seen.add(url)
            if kind=='marketplace' and site:
                hostname=urlparse(url).hostname or ''
                if hostname!=site and not hostname.endswith('.'+site): continue
            progress('Reading '+urlparse(url).netloc)
            if kind=='research':
                evidence.append(dict(result,kind=kind,observed_at=now(),price=None,currency='',rating=None,
                    reviews=None,sold_count=None,evidence='Search-discovered research source; verify report scope',status='Research source link'))
            else: evidence.extend(inspect(result,kind))
            time.sleep(.7)
    progress('Collecting available public category analytics')
    context=analytics_context(keyword)
    return dict(keyword=original_keyword,searched_keyword=keyword,observed_at=now(),evidence=evidence,errors=errors,analytics_context=context,
        note='Search matches may be different variants or accessories. Confirm comparable SKUs. Sold labels and reviews are not monthly sales. Suppliers are discovery leads, not approved vendors.')

def trends(keyword):
    keyword=normalize_keyword(keyword)
    from trendspyg import download_google_trends_interest_over_time
    from datetime import timedelta
    today=date.today(); start=date(today.year-5,today.month,1); end=today.replace(day=1)-timedelta(days=1)
    timeframe=f'{start.isoformat()} {end.isoformat()}'
    points=download_google_trends_interest_over_time(keyword,geo='PK',timeframe=timeframe,engine='auto',max_retries=2,retry_wait=5)
    buckets={}; current=date.today().strftime('%Y-%m')
    for p in points:
        month=p['date'][:7]
        if p.get('is_partial') or month>=current or month<start.strftime('%Y-%m'): continue
        buckets.setdefault(month,[]).append(float(p['value']))
    source=f'https://trends.google.com/trends/explore?date={requests.utils.quote(timeframe)}&geo=PK&q={requests.utils.quote(keyword)}'
    archive=ROOT/'data/trend-raw'; archive.mkdir(parents=True,exist_ok=True)
    import hashlib
    key=hashlib.sha256(keyword.encode()).hexdigest()[:16]
    (archive/f'{key}-{today.isoformat()}.json').write_text(json.dumps(dict(keyword=keyword,geo='PK',timeframe=timeframe,observed_at=now(),points=points)),encoding='utf-8')
    return [dict(date=m,value=round(sum(values)/len(values),3),metric='interest',geo='PK',source=source,
        observed_at=now(),aggregation='Mean of complete source points within month') for m,values in sorted(buckets.items())]
