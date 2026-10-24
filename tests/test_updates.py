"""Update planning is deterministic and makes no provider requests."""
import copy,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.corpus import parse_catalog

SOURCE={'commit':'a'*40,'repository':'https://github.com/hzysvilla/Academic_Smart_Contract_Papers'}
def row(title='Existing',url='https://example.org/old.pdf'):
    return parse_catalog(f'## S&P\n### sp2024\n* [{title}]({url})',SOURCE['commit'])[0]

def test_new_titles_only_and_old_indexed_identity_is_preserved():
    from app.update_catalog import merge_catalog,pending_rows
    old={**row(),'status':'parsed','pdf_sha256':'digest','page_count':4}
    latest=[row(),row('New paper')]
    manifest,delta=merge_catalog({'papers':[old]},latest,SOURCE)
    assert delta['new_titles']==1
    assert manifest['papers'][0]==old
    assert [p['title'] for p in pending_rows(manifest)]==['New paper']
    again,delta=merge_catalog(manifest,latest,SOURCE)
    assert delta['new_titles']==0 and len(again['papers'])==2

def test_old_unavailable_rows_are_not_all_retried_but_new_links_are():
    from app.update_catalog import merge_catalog,pending_rows
    old={**row(url=''),'status':'missing_link'}
    same,_=merge_catalog({'papers':[old]},[row(url='')],SOURCE)
    assert pending_rows(same)==[]
    changed,delta=merge_catalog(same,[row(url='https://example.org/new.pdf')],SOURCE)
    assert delta['new_links']==1 and len(pending_rows(changed))==1

def test_scheduled_batch_and_attempt_caps_and_no_automatic_deletion():
    from app.update_catalog import merge_catalog,pending_rows
    old={**row(),'status':'parsed'}
    manifest,_=merge_catalog({'papers':[old]},[row(str(i)) for i in range(15)],SOURCE)
    assert old in manifest['papers'] and len(pending_rows(manifest))==10
    for p in manifest['papers'][1:]:p['update_attempts']=3
    assert not pending_rows(manifest)

def test_catalog_scope_excludes_other_conferences():
    from app.update_catalog import merge_catalog
    with pytest.raises(ValueError,match='venue'):
        merge_catalog({'papers':[]},[{**row(),'venue':'DSN'}],SOURCE)

def test_relative_url_commit_changes_do_not_reset_exhausted_retries():
    from app.update_catalog import merge_catalog,pending_rows
    markdown='## S&P\n### sp2024\n* [Relative Paper](papers/relative.pdf)'
    old=parse_catalog(markdown,'a'*40)[0];old.update(status='unavailable',update_managed=True,update_attempts=3)
    latest=parse_catalog(markdown,'b'*40)
    manifest,delta=merge_catalog({'papers':[old]},latest,{'commit':'b'*40})
    assert delta['new_links']==0 and manifest['papers'][0]['update_attempts']==3
    assert pending_rows(manifest)==[]

def test_incremental_rejects_changed_old_content_or_embedding_and_allows_superset():
    from app.update_catalog import validate_incremental
    old={**row(),'status':'parsed','pdf_sha256':'oldhash'};new={**row('New'),'status':'parsed','pdf_sha256':'newhash'}
    receipt={'status':'verified','paper_ids':[old['paper_id']],'embedding_model':'BAAI/bge-m3','embedding_dimension':1024,'chunk_token_size':1200,'overlap':100}
    docs={old['paper_id']:{'status':'complete','pdf_sha256':'oldhash'}};env={'EMBEDDING_MODEL':'BAAI/bge-m3','EMBEDDING_DIM':'1024'}
    validate_incremental(receipt,docs,[old,new],env)
    with pytest.raises(ValueError):validate_incremental(receipt,docs,[new],env)
    with pytest.raises(ValueError):validate_incremental(receipt,docs,[{**old,'pdf_sha256':'changed'},new],env)
    with pytest.raises(ValueError):validate_incremental(receipt,docs,[old,new],{**env,'EMBEDDING_DIM':'768'})
