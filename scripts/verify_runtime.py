"""Explicit, small real-provider smoke check. Not a general RAG quality benchmark."""
import argparse,json,re,sys,time
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from app.settings import ROOT,Settings
from app.rag import chunk_paper

CASES=[
    ('SP','SmartPulse 如何检查智能合约中的时序性质？请说明方法并引用原文。','sp_2021_e22b16be2f2d','naive'),
    ('CCS','Securify: Practical Security Analysis of Smart Contracts 使用什么方法分析智能合约？','ccs_2018_bd87acda4541','naive'),
    ('USENIX','TXSPECTOR 如何从以太坊交易中识别攻击？','usenix_2020_972e960bafb5','mix'),
    ('NDSS','Sereum 如何保护已有智能合约免受重入攻击？请给出机制与局限并引用原文。','ndss_2019_a3ab392d0155','mix'),
]

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--execute',action='store_true');parser.add_argument('--reuse-probes',type=Path);args=parser.parse_args()
    if not args.execute:
        print('Preview: 4 retrieval probes + 2 real answers; add --execute to incur provider calls.');return
    cfg=Settings.load();stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    folder=ROOT/'verification/private'/stamp;folder.mkdir(parents=True,exist_ok=False)
    report={'kind':'real_provider_smoke_not_quality_benchmark','created_at':stamp,'retrieval':[],'answers':[],'status':'running'}
    with httpx.Client(trust_env=False,timeout=180) as client:
        health=client.get('http://127.0.0.1:8003/health');health.raise_for_status()
        catalog=client.get('http://127.0.0.1:8003/api/catalog?limit=1').json()
        assert catalog['indexed_count']>0 and catalog['index']['status']=='verified'
        report['index']=catalog['index'];report['indexed_papers']=catalog['indexed_count']
        for venue,question,pid,mode in CASES:
            t=time.monotonic()
            if args.reuse_probes:
                body=json.loads((args.reuse_probes/(venue+'_retrieval.json')).read_text(encoding='utf-8'))
            else:
                r=client.post(cfg.lightrag_url+'/query/data',headers={'X-API-Key':cfg.lightrag_key},
                    json={'query':question,'mode':mode,'enable_rerank':False,'top_k':40,'chunk_top_k':80,'max_total_tokens':40000})
                r.raise_for_status();body=r.json()
            assert body['status']=='success'
            chunks=body['data']['chunks'];matching=[c for c in chunks if chunk_paper(c)[0]==pid]
            record={'venue':venue,'mode':mode,'expected_paper':pid,'chunk_count':len(chunks),'expected_paper_chunks':len(matching),
                'entity_count':len(body['data'].get('entities',[])),'relationship_count':len(body['data'].get('relationships',[])),
                'seconds':round(time.monotonic()-t,3),'passed':bool(matching),'reused_probe':bool(args.reuse_probes)}
            report['retrieval'].append(record)
            (folder/(venue+'_retrieval.json')).write_text(json.dumps(body,ensure_ascii=False,indent=2),encoding='utf-8')
            print('Retrieval',venue,mode,'target chunks',len(matching),'/',len(chunks),flush=True)
        assert all(r['passed'] for r in report['retrieval']),'One or more expected papers were not retrieved; inspect saved probes before generating.'
        headers={'Origin':cfg.allowed_origin}
        login=client.post('http://127.0.0.1:8003/api/login',json={'username':cfg.admin_username,'password':cfg.admin_password},headers=headers)
        login.raise_for_status();headers['X-Paper-User']=login.json()['user_id']
        papers=json.loads((cfg.data_dir/'manifest.json').read_text(encoding='utf-8'))['papers'];lookup={p['paper_id']:p for p in papers}
        for venue,question,pid,mode in [CASES[0],CASES[3]]:
            key=str(uuid4());payload={'question':question,'mode':mode,'venue':venue};ask_headers={**headers,'Idempotency-Key':key}
            t=time.monotonic();answer=client.post('http://127.0.0.1:8003/api/ask',headers=ask_headers,json=payload)
            body=answer.json();(folder/(venue+'_answer.json')).write_text(json.dumps(body,ensure_ascii=False,indent=2),encoding='utf-8')
            if answer.status_code!=200:
                report['status']='failed';report['failure']=body.get('detail');(folder/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            answer.raise_for_status();assert body['status']=='completed' and body['llm_generated'] is True
            refs=body['references'];assert refs and any(r['paper_id']==pid for r in refs)
            assert all(r['venue']==venue and 1<=r['page']<=lookup[r['paper_id']]['page_count'] for r in refs)
            cited=re.findall(r'\[(\d+)\]',body['answer']);assert cited and set(cited)<=set(r['reference_id'] for r in refs)
            r=client.get('http://127.0.0.1:8003/api/papers/'+refs[0]['paper_id']+'/file');assert r.status_code==200 and r.content.startswith(b'%PDF-')
            replay=client.post('http://127.0.0.1:8003/api/ask',headers=ask_headers,json=payload);replay.raise_for_status()
            assert replay.json()['request_id']==body['request_id'] and replay.json()['answer']==body['answer']
            history=client.get('http://127.0.0.1:8003/api/history/'+body['request_id']);assert history.json()['answer']==body['answer']
            report['answers'].append({'venue':venue,'mode':mode,'status':body['status'],'references':len(refs),
                'expected_paper_present':True,'citation_ids_valid':True,'pdf_served':True,'idempotent_reuse':True,
                'rerank_degraded':body['retrieval']['degraded'],'seconds':round(time.monotonic()-t,3),'request_id':body['request_id']})
            print('Answer',venue,mode,'completed; references',len(refs),'replay reused',flush=True)
        client.post('http://127.0.0.1:8003/api/logout',json={},headers=headers).raise_for_status()
        assert client.get('http://127.0.0.1:8003/api/history').status_code==401
        report['status']='passed';report['limitation']='Checks connectivity, source mapping and application behavior; does not prove factual correctness or compare retrieval quality.'
    (folder/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Status:',report['status'],'Private report:',folder/'summary.json')

if __name__=='__main__':main()
