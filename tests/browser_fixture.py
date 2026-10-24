"""Temporary SQLite and synthetic paper evidence, never production/model calls."""
import sys,json
from dataclasses import replace
from contextlib import asynccontextmanager
from pathlib import Path
import uvicorn
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.settings import Settings
from app.main import create_app
from app.storage import User,password_hasher
from app.rag import PipelineError

def main():
    root=Path(sys.argv[1]);port=int(sys.argv[2]);root.mkdir(parents=True,exist_ok=True)
    paper={'paper_id':'sp_2024_fixture','title':'Synthetic Smart Contract Paper','venue':'SP','year':2024,'status':'parsed','source_url':'https://example.org/paper.pdf'}
    (root/'manifest.json').write_text(json.dumps({'papers':[paper],'summary':{'parsed':1}}))
    (root/'index_receipt.json').write_text(json.dumps({'status':'verified','index_id':'fixture','paper_ids':[paper['paper_id']],'chunk_count':1}))
    (root/'.local_index.json').write_text(json.dumps({'index_id':'fixture'}))
    for folder,suffix in [('pdfs','.pdf'),('text','.json')]:
        (root/folder).mkdir();(root/folder/(paper['paper_id']+suffix)).write_text('synthetic test artifact')
    engine=create_engine('sqlite+pysqlite:///'+str(root/'test.db'),connect_args={'check_same_thread':False})
    cfg=replace(Settings(),database_url='sqlite+pysqlite:///'+str(root/'test.db'),data_dir=root,allowed_origin=f'http://127.0.0.1:{port}')
    app=create_app(cfg,engine=engine,initialize_admin=False);original=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with original(application):
            with Session(engine) as db,db.begin():
                for name in ['alice','bob']:db.add(User(username=name,password_hash=password_hasher.hash('TestPass8')))
            def answer(req):
                if req.question=='Synthetic timeout test':raise PipelineError('generation','timeout',504)
                return {'answer':'Synthetic paper answer **only for testing** [1]',
                'references':[{'reference_id':'1','paper_id':paper['paper_id'],'title':paper['title'],'venue':'SP','year':2024,'page':1,'content':['Synthetic evidence only.']}],
                'llm_generated':False,'retrieval':{'mode':req.mode,'candidate_count':1,'scoped_count':1,'selected_count':1,'degraded':False}}
            application.state.answerer=answer
            yield
    app.router.lifespan_context=lifespan
    uvicorn.run(app,host='127.0.0.1',port=port,log_level='warning')

if __name__=='__main__':main()
