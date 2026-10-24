import pytest
from app.corpus import parse_catalog,canonical_pdf_urls,extract_pdf_pages,validate_pdf

SAMPLE='''## S&P
### sp2024
* [security] [SmartPulse: Checking Contracts](https://arxiv.org/abs/2101.01917)
## CCS
### ccs2023
* [security] [Other paper]() | **[MyTLDR](TLDR.md#x)**
## USENIX Security
### sec2025
* [security] [One](https://x.test/one.pdf). [security] [Two](papers/two.pdf)
* [security] [One](https://x.test/alternate.pdf)
## NDSS
### ndss2024
* [security] [Four](https://x.test/four.pdf)
## DSN
### dsn2025
* [security] [Excluded](https://x.test/excluded.pdf)
'''

def test_only_four_venues_and_multiple_links_on_same_line():
    rows=parse_catalog(SAMPLE,'abc123')
    assert len(rows)==5
    assert {r['venue'] for r in rows}=={'SP','CCS','USENIX','NDSS'}
    assert not any(r['title']=='Excluded' for r in rows)
    assert next(r for r in rows if r['title']=='Two')['source_url'].endswith('/abc123/papers/two.pdf')

def test_duplicate_titles_keep_alternate_links_and_empty_links_are_explicit():
    rows=parse_catalog(SAMPLE,'abc123')
    one=next(r for r in rows if r['title']=='One')
    assert len(one['candidate_urls'])==2
    assert next(r for r in rows if r['title']=='Other paper')['status']=='missing_link'
    assert len({r['paper_id'] for r in rows})==len(rows)

def test_arxiv_abstract_and_repository_blob_urls_become_downloads():
    assert canonical_pdf_urls('https://arxiv.org/abs/2101.01917')[0]=='https://arxiv.org/pdf/2101.01917'
    assert canonical_pdf_urls('https://github.com/a/b/blob/main/papers/a.pdf')[0]=='https://raw.githubusercontent.com/a/b/main/papers/a.pdf'
    assert canonical_pdf_urls('https://eprint.iacr.org/2023/123')[0].endswith('/2023/123.pdf')

def test_download_rejects_html_or_non_pdf():
    with pytest.raises(ValueError):validate_pdf(b'<html>login required</html>')

def test_pdf_pages_are_kept_separate():
    import pymupdf
    doc=pymupdf.open()
    for i in range(2):doc.new_page().insert_text((72,72),f'Paper page {i+1} '+('contract analysis '*30))
    pages=extract_pdf_pages(doc.tobytes())
    assert [p['page'] for p in pages]==[1,2]
    assert 'Paper page 2' in pages[1]['text']

def test_cached_single_page_poster_is_not_treated_as_full_paper(tmp_path,monkeypatch):
    from app import corpus
    import hashlib,json
    monkeypatch.setattr(corpus,'ROOT',tmp_path)
    (tmp_path/'data/pdfs').mkdir(parents=True);(tmp_path/'data/text').mkdir()
    raw=b'%PDF-test';(tmp_path/'data/pdfs/one.pdf').write_bytes(raw)
    (tmp_path/'data/text/one.json').write_text(json.dumps({'pdf_sha256':hashlib.sha256(raw).hexdigest(),'page_count':1,'characters':3000,'download_url':'https://example.org/poster.pdf'}))
    row=corpus.download_one({'paper_id':'one'})
    assert row['status']=='non_paper_resource'

def test_fresh_clone_rehydrates_parsed_manifest_without_local_files(tmp_path,monkeypatch):
    from app import corpus
    import json
    monkeypatch.setattr(corpus,'ROOT',tmp_path)
    source=tmp_path/'data/source';source.mkdir(parents=True)
    markdown='## S&P\n### sp2024\n* [Example Paper](https://example.org/paper.pdf)\n'
    (source/'catalog.README.md').write_text(markdown)
    (source/'catalog.provenance.json').write_text(json.dumps({'commit':'test'}))
    paper=corpus.parse_catalog(markdown,'test')[0];paper['status']='parsed'
    (tmp_path/'data/manifest.json').write_text(json.dumps({'papers':[paper]}))
    calls=[]
    def download(row,resolve_missing):calls.append(row['paper_id']);return {**row,'status':'unavailable'}
    monkeypatch.setattr(corpus,'download_one',download)
    corpus.collect(workers=1)
    assert calls==[paper['paper_id']]
