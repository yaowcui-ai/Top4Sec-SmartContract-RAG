"""Offline SDK import. Stop the LightRAG service before writing; execute is explicit."""
import argparse,asyncio,hashlib,json,os,sys,time
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.index_plan import page_chunks,build_custom_kg

async def build(execute=False,limit=None):
    from dotenv import dotenv_values
    from lightrag import LightRAG
    from lightrag.utils import EmbeddingFunc,compute_mdhash_id
    from lightrag.llm.openai import openai_embed,openai_complete_if_cache
    from lightrag.kg.shared_storage import initialize_pipeline_status
    from app.corpus import atomic_json
    import networkx as nx
    env=dotenv_values(ROOT/'.lightrag.env',interpolate=False)
    manifest=json.loads((ROOT/'data/manifest.json').read_text(encoding='utf-8'))
    papers=[p for p in manifest['papers'] if p['status']=='parsed' and p.get('page_count',0)>=2]
    print('Full-text papers eligible:',len(papers),'Graph: bibliographic/page structure; no LLM extraction',flush=True)
    if not execute:print('Preview only; add --execute to call the embedding provider.');return
    if not papers:raise RuntimeError('No full-text papers')
    fingerprint=hashlib.sha256(json.dumps({'papers':[(p['paper_id'],p['pdf_sha256']) for p in papers],
        'embedding_model':env['EMBEDDING_MODEL'],'embedding_dim':env['EMBEDDING_DIM'],'chunking':[1200,100],'graph':'bibliographic_pages_v1'},sort_keys=True).encode()).hexdigest()
    receipts=ROOT/'runtime/index_build/receipts';receipts.mkdir(parents=True,exist_ok=True)
    build_file=ROOT/'runtime/index_build/build.json'
    if build_file.exists() and json.loads(build_file.read_text())['index_id']!=fingerprint:
        raise RuntimeError('Corpus or embedding configuration changed; create a new isolated build before replacing a live index.')
    atomic_json(build_file,{'index_id':fingerprint,'status':'building'})
    lock=ROOT/'data/index_build.lock';lock.write_text(fingerprint)
    working=ROOT/'runtime/lightrag';working.mkdir(parents=True,exist_ok=True)
    async def embed(texts):
        return await openai_embed.func(texts,model=env['EMBEDDING_MODEL'],base_url=env['EMBEDDING_BINDING_HOST'],
            api_key=env['EMBEDDING_BINDING_API_KEY'],max_token_size=8192)
    async def complete(prompt,system_prompt=None,history_messages=None,**kwargs):
        return await openai_complete_if_cache(env['LLM_MODEL'],prompt,system_prompt=system_prompt,
            history_messages=history_messages or [],base_url=env['LLM_BINDING_HOST'],api_key=env['LLM_BINDING_API_KEY'],**kwargs)
    rag=LightRAG(working_dir=str(working),workspace='smartcontract_top4_v1',llm_model_func=complete,
        llm_model_name=env['LLM_MODEL'],embedding_func=EmbeddingFunc(embedding_dim=int(env['EMBEDDING_DIM']),max_token_size=8192,
            model_name=env['EMBEDDING_MODEL'],func=embed),embedding_batch_num=16,embedding_func_max_async=3,
        chunk_token_size=1200,chunk_overlap_token_size=100)
    await rag.initialize_storages();await initialize_pipeline_status(workspace=rag.workspace)
    total_chunks=0
    try:
        for position,paper in enumerate(papers,1):
            receipt=receipts/(paper['paper_id']+'.json')
            if receipt.exists():
                old=json.loads(receipt.read_text())
                if old.get('status')=='complete' and old['pdf_sha256']==paper['pdf_sha256']:
                    total_chunks+=old['chunk_count'];print('REUSE',position,paper['paper_id'],flush=True);continue
            document=json.loads((ROOT/paper['text_file']).read_text(encoding='utf-8'))
            chunks=page_chunks(paper,document['pages'],rag.tokenizer)
            graph=build_custom_kg(paper,chunks)
            print('IMPORT',position,'/',len(papers),paper['paper_id'],'chunks',len(chunks),flush=True)
            await rag.ainsert_custom_kg(graph,full_doc_id=paper['paper_id'])
            now=datetime.now(timezone.utc).isoformat()
            await rag.full_docs.upsert({paper['paper_id']:{'content':'\n\n'.join(p['text'] for p in document['pages']),
                'file_path':paper['paper_id']+'.pdf'}})
            await rag.doc_status.upsert({paper['paper_id']:{'content_summary':paper['title'],'content_length':document['characters'],
                'status':'processed','created_at':now,'updated_at':now,'file_path':paper['paper_id']+'.pdf',
                'chunks_count':len(chunks),'chunks_list':[compute_mdhash_id(c['content'],prefix='chunk-') for c in chunks],
                'track_id':'scpaper-custom-import','metadata':{'import_path':'custom_kg','journal':'project_per_paper_receipt_not_upstream_recovery'}}})
            await rag._insert_done()
            atomic_json(receipt,{'status':'complete','paper_id':paper['paper_id'],'pdf_sha256':paper['pdf_sha256'],
                'chunk_count':len(chunks),'completed_at':now})
            total_chunks+=len(chunks)
            if limit and position>=limit:
                await rag.finalize_storages()
                print('PILOT COMPLETE; build remains locked until full verification. Resume without --limit.',flush=True)
                return
        await rag.finalize_storages()
        store=working/'smartcontract_top4_v1'
        chunk_store=json.loads((store/'kv_store_text_chunks.json').read_text(encoding='utf-8'))
        graph=nx.read_graphml(store/'graph_chunk_entity_relation.graphml')
        actual=len(chunk_store)
        if actual!=total_chunks:raise RuntimeError(f'Chunk count mismatch: {actual} versus {total_chunks}')
        receipt={'status':'verified','index_id':fingerprint,'workspace':'smartcontract_top4_v1','paper_ids':[p['paper_id'] for p in papers],
            'paper_count':len(papers),'chunk_count':actual,'node_count':graph.number_of_nodes(),'edge_count':graph.number_of_edges(),
            'embedding_model':env['EMBEDDING_MODEL'],'embedding_dimension':int(env['EMBEDDING_DIM']),
            'chunk_token_size':1200,'overlap':100,'graph_kind':'bibliographic_and_page_structure',
            'built_at':datetime.now(timezone.utc).isoformat(),'llm_extraction_calls':0}
        atomic_json(ROOT/'data/index_receipt.json',receipt);atomic_json(build_file,receipt)
        atomic_json(ROOT/'data/.local_index.json',{'index_id':fingerprint,'workspace':receipt['workspace'],'built_at':receipt['built_at']})
        lock.unlink()
        print('VERIFIED',json.dumps({k:receipt[k] for k in ['paper_count','chunk_count','node_count','edge_count']}),flush=True)
    except Exception:
        # Leave the lock: the API must not claim a partial build is usable.
        try:await rag.finalize_storages()
        except Exception:pass
        raise

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true');parser.add_argument('--limit',type=int);args=parser.parse_args()
    asyncio.run(build(args.execute,args.limit))
