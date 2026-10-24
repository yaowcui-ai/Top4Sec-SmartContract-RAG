"""Freeze four-venue metadata, obtain public PDFs, and retain page-aware text locally."""
from __future__ import annotations
import argparse,collections,concurrent.futures,hashlib,json,re,threading,time,unicodedata
from datetime import datetime,timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse,quote,urljoin
import httpx

ROOT=Path(__file__).resolve().parents[1]
VENUES={'S&P':'SP','CCS':'CCS','USENIX Security':'USENIX','NDSS':'NDSS'}
REPOSITORY='https://github.com/hzysvilla/Academic_Smart_Contract_Papers'

def normalized_title(title):
    return re.sub(r'[^\w]+',' ',unicodedata.normalize('NFKC',title).casefold()).strip()

def parse_catalog(markdown:str,commit:str)->list[dict]:
    venue=None;year=None;by_title={}
    for line in markdown.splitlines():
        if line.startswith('## '):venue=VENUES.get(line[3:].strip())
        if line.startswith('### '):
            match=re.search(r'(20\d{2})',line);year=int(match[1]) if match else None
        if not venue or line.startswith('#'):continue
        for title,url in re.findall(r'\[([^\]]+)\]\(([^)]*)\)',line):
            if title.startswith('MyTLDR') or url.startswith('#'):continue
            title=title.strip();key=normalized_title(title)
            if not key:continue
            if url and not urlparse(url).scheme:
                url=f'https://raw.githubusercontent.com/hzysvilla/Academic_Smart_Contract_Papers/{commit}/'+url.lstrip('/')
            if url and urlparse(url).scheme not in ('http','https'):url=''
            entry={'venue':venue,'year':year,'source_url':url}
            if key in by_title:
                row=by_title[key];row['catalog_entries'].append(entry)
                if url and url not in row['candidate_urls']:row['candidate_urls'].append(url)
                continue
            by_title[key]={'paper_id':f'{venue.lower()}_{year}_{hashlib.sha256(key.encode()).hexdigest()[:12]}',
                'title':title,'venue':venue,'year':year,'source_url':url,'candidate_urls':[url] if url else [],
                'catalog_entries':[entry],'status':'pending' if url else 'missing_link',
                'metadata_basis':'upstream_catalog_label_not_independent_venue_verification'}
    return list(by_title.values())

def canonical_pdf_urls(url:str)->list[str]:
    url=url.replace('www.arxiv.org','arxiv.org')
    if 'github.com/' in url and '/blob/' in url:
        url=url.replace('https://github.com/','https://raw.githubusercontent.com/').replace('/blob/','/')
    if 'arxiv.org/abs/' in url:url=url.replace('/abs/','/pdf/')
    if re.match(r'https?://eprint\.iacr\.org/\d{4}/\d+$',url):url+='.pdf'
    if 'dropbox.com/' in url:url+=('&' if '?' in url else '?')+'dl=1'
    return [url]

def validate_pdf(content:bytes)->None:
    if not content.lstrip().startswith(b'%PDF-'):raise ValueError('not_a_pdf')
    if len(content)>35*1024*1024:raise ValueError('pdf_too_large')

def extract_pdf_pages(content:bytes)->list[dict]:
    import pymupdf
    validate_pdf(content)
    with pymupdf.open(stream=content,filetype='pdf') as doc:
        if doc.is_encrypted:raise ValueError('encrypted_pdf')
        if len(doc)>200:raise ValueError('document_requires_manual_review')
        return [{'page':i+1,'text':re.sub(r'[ \t]+',' ',page.get_text(sort=True)).strip()} for i,page in enumerate(doc)]

def atomic_json(path:Path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(path)

def resolve_open_urls(client:httpx.Client,title:str)->list[str]:
    """Only accept OA metadata whose title strongly matches the catalog title."""
    candidates=[];target=normalized_title(title)
    try:
        response=client.get('https://api.openalex.org/works',params={'search':title,'per-page':2},timeout=20)
        if response.status_code==200:
            for work in response.json().get('results',[]):
                if SequenceMatcher(None,target,normalized_title(work.get('title',''))).ratio()<.86:continue
                locations=[work.get('best_oa_location'),work.get('primary_location')]+work.get('locations',[])
                for location in locations:
                    if location and location.get('pdf_url'):candidates.append(location['pdf_url'])
                if work.get('open_access',{}).get('oa_url'):candidates.append(work['open_access']['oa_url'])
    except (httpx.HTTPError,ValueError,KeyError):pass
    # Crossref is metadata discovery, never a license/paywall bypass.
    if not candidates:
        try:
            response=client.get('https://api.crossref.org/works',params={'query.title':title,'rows':1},timeout=20)
            if response.status_code==200:
                for work in response.json().get('message',{}).get('items',[]):
                    name=(work.get('title') or [''])[0]
                    if SequenceMatcher(None,target,normalized_title(name)).ratio()<.86:continue
                    candidates.extend(link['URL'] for link in work.get('link',[]) if 'pdf' in link.get('content-type',''))
        except (httpx.HTTPError,ValueError,KeyError):pass
    return list(dict.fromkeys(u for u in candidates if isinstance(u,str) and u.startswith(('http://','https://'))))[:4]

def download_one(row:dict,resolve_missing:bool=False)->dict:
    row=dict(row);identifier=row['paper_id'];pdf=ROOT/'data/pdfs'/f'{identifier}.pdf';text=ROOT/'data/text'/f'{identifier}.json'
    if pdf.exists() and text.exists():
        data=json.loads(text.read_text(encoding='utf-8'))
        if hashlib.sha256(pdf.read_bytes()).hexdigest()==data['pdf_sha256']:
            if data['page_count']<2:return {**row,'status':'non_paper_resource','error':'single_page_not_accepted_as_full_paper'}
            return {**row,**{k:data[k] for k in ['pdf_sha256','page_count','characters','download_url']},'status':'parsed','pdf_file':f'data/pdfs/{identifier}.pdf','text_file':f'data/text/{identifier}.json'}
        raise ValueError('Existing PDF hash mismatch: '+identifier)
    notes=[]
    with httpx.Client(follow_redirects=True,timeout=httpx.Timeout(40,connect=12),headers={'User-Agent':'AcademicPaperRAG/1.0 (personal research; public documents)'}) as client:
        urls=list(row['candidate_urls']);extra_done=False
        while True:
            for url in list(dict.fromkeys(urls)):
                for candidate in canonical_pdf_urls(url):
                    try:
                        if 'slides' in urlparse(candidate).path.lower():notes.append({'url':candidate,'error':'slides_not_full_paper'});continue
                        with client.stream('GET',candidate) as response:
                            if response.status_code!=200:notes.append({'url':candidate,'http_status':response.status_code});continue
                            content=bytearray()
                            for part in response.iter_bytes():
                                content.extend(part)
                                if len(content)>35*1024*1024:raise ValueError('pdf_too_large')
                        raw=bytes(content)
                        if not raw.lstrip().startswith(b'%PDF-'):
                            html=raw[:150000].decode('utf-8',errors='replace')
                            links=re.findall(r'<meta[^>]+name=[\"\x27]citation_pdf_url[\"\x27][^>]+content=[\"\x27]([^\"\x27]+)',html,re.I)
                            if links:
                                linked=urljoin(candidate,links[0])
                                raw=client.get(linked).content;candidate=linked
                        pages=extract_pdf_pages(raw)
                        if len(pages)<2:raise ValueError('single_page_not_accepted_as_full_paper')
                        first=' '.join(p['text'] for p in pages[:2])
                        title_words={w for w in normalized_title(row['title']).split() if len(w)>3}
                        actual=set(normalized_title(first).split())
                        if title_words and len(title_words&actual)/len(title_words)<.55:raise ValueError('pdf_title_mismatch')
                        characters=sum(len(p['text']) for p in pages)
                        if characters<2000:raise ValueError('insufficient_extracted_text')
                        digest=hashlib.sha256(raw).hexdigest()
                        pdf.parent.mkdir(parents=True,exist_ok=True);pdf.write_bytes(raw)
                        payload={**row,'pages':pages,'pdf_sha256':digest,'page_count':len(pages),'characters':characters,'download_url':candidate,'text_extraction':'PyMuPDF sorted text; figures/formulae not visually interpreted'}
                        atomic_json(text,payload)
                        return {**row,'status':'parsed','pdf_sha256':digest,'page_count':len(pages),'characters':characters,'download_url':candidate,'pdf_file':f'data/pdfs/{identifier}.pdf','text_file':f'data/text/{identifier}.json','attempts':notes}
                    except (httpx.HTTPError,ValueError,OSError) as error:notes.append({'url':candidate,'error':type(error).__name__ if isinstance(error,httpx.HTTPError) else str(error)[:100]})
            if resolve_missing and not extra_done:
                extra_done=True;urls=resolve_open_urls(client,row['title'])
                if urls:continue
            break
    return {**row,'status':'unavailable' if row['candidate_urls'] else 'missing_link','attempts':notes}

def collect(workers=4,resolve_missing=False,limit=None):
    provenance=json.loads((ROOT/'data/source/catalog.provenance.json').read_text(encoding='utf-8'))
    rows=parse_catalog((ROOT/'data/source/catalog.README.md').read_text(encoding='utf-8'),provenance['commit'])
    manifest=ROOT/'data/manifest.json'
    if manifest.exists():
        previous={r['paper_id']:r for r in json.loads(manifest.read_text(encoding='utf-8'))['papers']}
        rows=[previous.get(r['paper_id'],r) for r in rows]
    for row in rows:
        identifier=row['paper_id']
        if row['status']=='parsed' and not all((ROOT/path/f'{identifier}{suffix}').is_file() for path,suffix in [('data/pdfs','.pdf'),('data/text','.json')]):
            row['status']='pending'
            # Reuse a previously discovered public author link without assuming its file exists.
            row['candidate_urls']=list(dict.fromkeys(([row['download_url']] if row.get('download_url') else [])+row['candidate_urls']))
    positions={r['paper_id']:i for i,r in enumerate(rows)}
    selected=[r for r in rows if r['status']!='parsed']
    if limit:selected=selected[:limit]
    def save():
        atomic_json(manifest,{'source':provenance,'updated_at':datetime.now(timezone.utc).isoformat(),
            'summary':dict(collections.Counter(r['status'] for r in rows)),'papers':rows})
    save()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(download_one,row,resolve_missing):row['paper_id'] for row in selected}
        for future in concurrent.futures.as_completed(futures):
            identifier=futures[future]
            try:row=future.result()
            except Exception as error:row={**rows[positions[identifier]],'status':'failed','error':type(error).__name__}
            rows[positions[identifier]]=row;save()
            print(identifier,row['status'],row.get('page_count',''),flush=True)
    print('Corpus summary:',dict(collections.Counter(r['status'] for r in rows)),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['collect']);parser.add_argument('--workers',type=int,default=4);parser.add_argument('--resolve-missing',action='store_true');parser.add_argument('--limit',type=int)
    args=parser.parse_args();collect(args.workers,args.resolve_missing,args.limit)
