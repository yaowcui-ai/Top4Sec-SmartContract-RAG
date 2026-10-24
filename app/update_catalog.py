"""Bounded, append-only updates from the four-venue public catalog."""
import collections,copy,json,re
from datetime import datetime,timezone
import httpx
from .corpus import parse_catalog,normalized_title,REPOSITORY

ALLOWED={'SP','CCS','USENIX','NDSS'}

def logical_url(url):
    # A different README commit alone is not a new relative PDF link.
    return re.sub(r'^(https://raw\.githubusercontent\.com/hzysvilla/Academic_Smart_Contract_Papers/)[0-9a-f]{40}/',r'\1@revision/',url)

def fetch_snapshot(client):
    response=client.get('https://api.github.com/repos/hzysvilla/Academic_Smart_Contract_Papers/commits',params={'path':'README.md','per_page':1})
    response.raise_for_status();commit=response.json()[0]['sha']
    if not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('Invalid source commit')
    url=f'https://raw.githubusercontent.com/hzysvilla/Academic_Smart_Contract_Papers/{commit}/README.md'
    response=client.get(url);response.raise_for_status()
    if len(response.content)>2*1024*1024:raise ValueError('Catalog exceeds update limit')
    markdown=response.content.decode('utf-8');papers=parse_catalog(markdown,commit)
    if {p['venue'] for p in papers}!=ALLOWED or len(papers)<100:raise ValueError('Incomplete four-venue catalog; existing data retained')
    return {'repository':REPOSITORY,'commit':commit,'readme_url':url,'license':'GPL-3.0'},markdown,papers

def merge_catalog(previous,latest,source):
    rows=copy.deepcopy(previous['papers']);lookup={normalized_title(p['title']):p for p in rows}
    delta={'new_titles':0,'new_links':0}
    for incoming in latest:
        if incoming['venue'] not in ALLOWED:raise ValueError('Unsupported venue')
        key=normalized_title(incoming['title'])
        if key not in lookup:
            paper={**copy.deepcopy(incoming),'update_managed':True,'update_attempts':0,'first_seen_commit':source['commit']}
            rows.append(paper);lookup[key]=paper;delta['new_titles']+=1
        else:
            old=lookup[key]
            if old['status']=='parsed':continue
            urls={logical_url(url):url for url in old.get('candidate_urls',[])}
            additions={logical_url(url) for url in incoming['candidate_urls']}-set(urls)
            urls.update({logical_url(url):url for url in incoming['candidate_urls']})
            old['candidate_urls']=list(urls.values())
            if additions:
                old.update(status='pending',update_managed=True,update_attempts=0)
                if not old.get('source_url'):old['source_url']=incoming.get('source_url','')
                delta['new_links']+=1
    return {'source':source,'updated_at':datetime.now(timezone.utc).isoformat(),
        'summary':dict(collections.Counter(p['status'] for p in rows)),'papers':rows},delta

def pending_rows(manifest,max_papers=10,max_attempts=3):
    return [p for p in manifest['papers'] if p.get('update_managed') and p['status'] not in {'parsed','duplicate_fulltext','non_paper_resource'} and p.get('update_attempts',0)<max_attempts][:max_papers]

def validate_incremental(previous,documents,papers,env):
    if previous.get('status')!='verified':raise ValueError('A verified base index is required')
    if (previous.get('embedding_model'),previous.get('embedding_dimension'))!=(env['EMBEDDING_MODEL'],int(env['EMBEDDING_DIM'])):
        raise ValueError('Embedding configuration changed; incremental import refused')
    if (previous.get('chunk_token_size'),previous.get('overlap'))!=(1200,100):raise ValueError('Chunking configuration changed')
    lookup={p['paper_id']:p for p in papers}
    for identifier in previous['paper_ids']:
        old=documents.get(identifier)
        if identifier not in lookup or not old or old.get('status')!='complete' or old.get('pdf_sha256')!=lookup[identifier]['pdf_sha256']:
            raise ValueError('Existing paper removed, changed or missing a complete receipt')
