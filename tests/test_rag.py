from types import SimpleNamespace
import pytest
from app.rag import select_chunks,build_evidence,rerank_candidates,PipelineError

PAPER={'paper_id':'sp_2024_abc','title':'Smart Contract Analysis','venue':'SP','year':2024,'download_url':'https://example.org/paper.pdf','status':'parsed'}
CHUNKS=[{'chunk_id':str(i),'file_path':f'sp_2024_abc__p000{i+1}.md','content':f'Page {i+1}: '+'contract analysis '*80} for i in range(3)]

def test_historical_receipt_is_not_proof_of_a_ready_local_index(tmp_path):
    import json
    from dataclasses import replace
    from app.rag import catalog_data
    from app.settings import Settings
    (tmp_path/'manifest.json').write_text(json.dumps({'papers':[PAPER]}))
    (tmp_path/'index_receipt.json').write_text(json.dumps({'status':'verified','index_id':'historical','paper_ids':[PAPER['paper_id']]}))
    _,index,indexed=catalog_data(replace(Settings(),data_dir=tmp_path))
    assert not indexed and index['status']=='not_built_locally'

def test_local_readiness_requires_matching_build_and_present_artifacts(tmp_path):
    import json
    from dataclasses import replace
    from app.rag import catalog_data
    from app.settings import Settings
    cfg=replace(Settings(),data_dir=tmp_path)
    (tmp_path/'manifest.json').write_text(json.dumps({'papers':[PAPER]}))
    (tmp_path/'index_receipt.json').write_text(json.dumps({'status':'verified','index_id':'local','paper_ids':[PAPER['paper_id']]}))
    marker=tmp_path/'.local_index.json';marker.write_text(json.dumps({'index_id':'local'}))
    for folder,suffix in [('pdfs','.pdf'),('text','.json')]:
        (tmp_path/folder).mkdir();(tmp_path/folder/(PAPER['paper_id']+suffix)).write_text('local artifact')
    assert catalog_data(cfg)[2]=={PAPER['paper_id']}
    lock=tmp_path/'index_build.lock';lock.write_text('building');assert not catalog_data(cfg)[2];lock.unlink()
    marker.write_text(json.dumps({'index_id':'another-build'}));assert not catalog_data(cfg)[2]
    marker.write_text(json.dumps({'index_id':'local'}));(tmp_path/'pdfs'/(PAPER['paper_id']+'.pdf')).unlink()
    assert not catalog_data(cfg)[2]

def test_metadata_filters_do_not_leak_out_of_scope():
    assert len(select_chunks(CHUNKS,[PAPER],venue='SP',year=2024,max_chars=30000))==3
    assert select_chunks(CHUNKS,[PAPER],venue='CCS',max_chars=30000)==[]

def test_unknown_old_hr_knowledge_chunks_are_not_used():
    chunks=[{'chunk_id':'old','file_path':'hr_relocation.md','content':'old HR data'}]
    with pytest.raises(PipelineError):select_chunks(chunks,[PAPER],max_chars=30000)

def test_budget_does_not_cut_a_chunk_in_half():
    result=select_chunks(CHUNKS,[PAPER],max_chars=len(CHUNKS[0]['content'])+4)
    assert len(result)==1 and result[0]['content']==CHUNKS[0]['content']

def test_references_keep_paper_and_pdf_page_mapping():
    bundle=build_evidence(CHUNKS,[PAPER])
    assert [r['page'] for r in bundle['references']]==[1,2,3]
    assert bundle['references'][0]['paper_id']==PAPER['paper_id']
    assert '[1]' in bundle['context'] and 'PDF page: 1' in bundle['context']

def test_invalid_rerank_indices_are_not_silently_accepted():
    class Client:
        def post(self,*a,**k):
            return SimpleNamespace(status_code=200,json=lambda:{'results':[{'index':99,'relevance_score':.9}]})
    settings=SimpleNamespace(rerank_url='https://example.org/rerank',rerank_key='private',rerank_model='model')
    with pytest.raises(PipelineError):rerank_candidates(Client(),settings,'question',CHUNKS)

def test_provider_client_does_not_implicitly_use_desktop_proxy(tmp_path,monkeypatch):
    import json,httpx
    from dataclasses import replace
    from app import rag
    from app.settings import Settings
    (tmp_path/'manifest.json').write_text(json.dumps({'papers':[PAPER]}))
    (tmp_path/'index_receipt.json').write_text(json.dumps({'status':'verified','index_id':'test','paper_ids':[PAPER['paper_id']]}))
    (tmp_path/'.local_index.json').write_text(json.dumps({'index_id':'test'}))
    for folder,suffix in [('pdfs','.pdf'),('text','.json')]:
        (tmp_path/folder).mkdir();(tmp_path/folder/(PAPER['paper_id']+suffix)).write_text('test artifact')
    original=httpx.Client;flags=[]
    def client(**kwargs):
        flags.append(kwargs.get('trust_env',True))
        return original(**kwargs,transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'status':'success','data':{'chunks':[]}})))
    monkeypatch.setattr(rag.httpx,'Client',client)
    result=rag.run_pipeline(replace(Settings(),data_dir=tmp_path),SimpleNamespace(question='Question',mode='naive',venue=None,year=None))
    assert result['llm_generated'] is False
    assert flags==[False,False]
