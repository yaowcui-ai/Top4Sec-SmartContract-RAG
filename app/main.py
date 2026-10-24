"""Independent paper QA API: auth, short transactions, idempotency and source tracing."""
from __future__ import annotations
from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib,json,logging,mimetypes,secrets,time
from pathlib import Path
from typing import Literal
from uuid import UUID,uuid4
from fastapi import FastAPI,Depends,Header,HTTPException,Query,Request
from fastapi.responses import FileResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,ConfigDict,Field,StrictStr,field_validator
from sqlalchemy import create_engine,select,delete
from sqlalchemy.exc import IntegrityError,SQLAlchemyError
from sqlalchemy.orm import Session
from .settings import Settings,ROOT
from .storage import Base,User,SessionToken,QARecord,password_hasher,serialize,utcnow
from .rag import run_pipeline,catalog_data,PipelineError

logger=logging.getLogger('scpaper')
DUMMY_HASH=password_hasher.hash('dummy-auth-work-only')
COOKIE='scpaper_session'

class LoginRequest(BaseModel):
    username:StrictStr=Field(min_length=3,max_length=32,pattern=r'^[a-z][a-z0-9_]{2,31}$')
    password:StrictStr=Field(min_length=1,max_length=128)

class AskRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    question:StrictStr=Field(min_length=1,max_length=2000)
    mode:Literal['naive','mix']='naive'
    venue:Literal['SP','CCS','USENIX','NDSS']|None=None
    year:int|None=Field(default=None,ge=2010,le=2030,strict=True)
    @field_validator('question')
    @classmethod
    def nonblank(cls,value):
        value=value.strip()
        if not value:raise ValueError('请输入问题')
        return value

def create_app(settings:Settings|None=None,*,engine=None,initialize_admin=True):
    cfg=settings or Settings.load()
    if engine is None:
        options={'pool_pre_ping':True,'hide_parameters':True}
        if not cfg.database_url.startswith('sqlite'):options['connect_args']={'connect_timeout':5,'read_timeout':10,'write_timeout':10}
        engine=create_engine(cfg.database_url,**options)

    @asynccontextmanager
    async def lifespan(app):
        Base.metadata.create_all(engine)
        if initialize_admin and cfg.admin_password:
            with Session(engine) as db,db.begin():
                if db.scalar(select(User).where(User.username==cfg.admin_username)) is None:
                    db.add(User(username=cfg.admin_username,password_hash=password_hasher.hash(cfg.admin_password)))
        app.state.answerer=lambda req:run_pipeline(cfg,req)
        yield
        engine.dispose()

    app=FastAPI(title='四大顶会区块链智能合约论文问答系统',version='1.0.0',lifespan=lifespan)
    app.state.engine=engine;app.state.settings=cfg

    @app.middleware('http')
    async def boundary(request,call_next):
        if request.method in ('POST','PUT','PATCH','DELETE') and request.url.path.startswith('/api/'):
            if request.headers.get('origin')!=cfg.allowed_origin:
                return JSONResponse({'detail':'请求来源不受信任。'},status_code=403)
            if request.headers.get('content-type','').split(';')[0]!='application/json':
                return JSONResponse({'detail':'需要 JSON 请求。'},status_code=415)
        try:return await call_next(request)
        except SQLAlchemyError:
            logger.error(json.dumps({'event':'database_error','path':request.url.path}))
            return JSONResponse({'detail':'数据库暂时不可用，请检查本项目 MySQL 服务。'},status_code=503)

    def current_user(request:Request):
        token=request.cookies.get(COOKIE)
        if not token:raise HTTPException(401,'请先登录。')
        hashed=hashlib.sha256(token.encode()).hexdigest()
        with Session(engine) as db:
            session=db.get(SessionToken,hashed)
            if not session or session.expires_at<=utcnow():raise HTTPException(401,'登录已失效。')
            user=db.get(User,session.user_id)
            if not user:raise HTTPException(401,'登录已失效。')
            expected=request.headers.get('x-paper-user')
            if expected and expected!=user.id:raise HTTPException(409,'浏览器账号已改变，请重新确认登录状态。')
            return {'user_id':user.id,'username':user.username}

    @app.get('/health')
    def health():return {'status':'alive','service':'smartcontract-paper-rag','models_checked':False}

    @app.post('/api/login')
    def login(body:LoginRequest):
        with Session(engine) as db,db.begin():
            user=db.scalar(select(User).where(User.username==body.username))
            valid=password_hasher.verify(body.password,user.password_hash if user else DUMMY_HASH)
            if not valid or not user:raise HTTPException(401,'用户名或密码错误。')
            token=secrets.token_urlsafe(32)
            db.add(SessionToken(token_hash=hashlib.sha256(token.encode()).hexdigest(),user_id=user.id,expires_at=utcnow()+timedelta(hours=2)))
            result={'user_id':user.id,'username':user.username}
        response=JSONResponse(result)
        response.set_cookie(COOKIE,token,max_age=7200,httponly=True,secure=cfg.cookie_secure,samesite='lax',path='/')
        return response

    @app.post('/api/logout')
    def logout(request:Request):
        token=request.cookies.get(COOKIE)
        if token:
            with Session(engine) as db,db.begin():db.execute(delete(SessionToken).where(SessionToken.token_hash==hashlib.sha256(token.encode()).hexdigest()))
        response=JSONResponse({'status':'logged_out'});response.delete_cookie(COOKIE,path='/');return response

    @app.get('/api/me')
    def me(user=Depends(current_user)):return user

    @app.get('/api/catalog')
    def catalog(venue:Literal['SP','CCS','USENIX','NDSS']|None=None,year:int|None=None,q:str='',limit:int=Query(30,ge=1,le=250),offset:int=Query(0,ge=0)):
        source,index,indexed=catalog_data(cfg)
        rows=[]
        for paper in source['papers']:
            if venue and paper['venue']!=venue:continue
            if year and paper['year']!=year:continue
            if q and q.casefold() not in paper['title'].casefold():continue
            rows.append({k:paper.get(k) for k in ['paper_id','title','venue','year','source_url','status','page_count']})
            rows[-1]['indexed']=paper['paper_id'] in indexed
        return {'items':rows[offset:offset+limit],'total':len(rows),'indexed_count':len(indexed),
            'download_summary':source.get('summary',{}),'index':{k:index.get(k) for k in ['status','index_id','chunk_count','node_count','edge_count','graph_kind']},
            'source_repository':'https://github.com/hzysvilla/Academic_Smart_Contract_Papers'}

    @app.get('/api/papers/{paper_id}/file')
    def paper_file(paper_id:str,user=Depends(current_user)):
        source,_,_=catalog_data(cfg)
        if not any(p['paper_id']==paper_id and p.get('status')=='parsed' for p in source['papers']):raise HTTPException(404,'论文全文不可用。')
        path=(cfg.data_dir/'pdfs'/f'{paper_id}.pdf').resolve()
        if not path.is_relative_to((cfg.data_dir/'pdfs').resolve()) or not path.is_file():raise HTTPException(404,'论文全文不在本机。')
        return FileResponse(path,media_type='application/pdf',content_disposition_type='inline',filename=f'{paper_id}.pdf')

    @app.get('/api/history')
    def history(user=Depends(current_user),limit:int=Query(10,ge=1,le=50),offset:int=Query(0,ge=0)):
        with Session(engine) as db:
            records=db.scalars(select(QARecord).where(QARecord.user_id==user['user_id']).order_by(QARecord.created_at.desc(),QARecord.id.desc()).offset(offset).limit(limit+1)).all()
            return {'items':[serialize(r) for r in records[:limit]],'has_more':len(records)>limit,'next_offset':offset+limit if len(records)>limit else None}

    @app.get('/api/history/{request_id}')
    def read_record(request_id:UUID,user=Depends(current_user)):
        with Session(engine) as db:
            record=db.get(QARecord,str(request_id))
            if not record or record.user_id!=user['user_id']:raise HTTPException(404,'记录不存在。')
            return serialize(record)

    def existing_result(record,fingerprint):
        if record.fingerprint!=fingerprint:raise HTTPException(409,{'message':'相同操作编号对应的问题、范围、模式或知识库不同。','request_id':record.id})
        if record.status=='processing':raise HTTPException(409,{'message':'该请求仍未确认完成，请查看个人历史。','request_id':record.id,'status':'processing'})
        if record.status=='failed':raise HTTPException(record.http_status or 502,{'message':'原请求未成功完成；如需重新执行，请主动发起新操作。','request_id':record.id,'stage':record.error_stage,'kind':record.error_kind})
        return serialize(record)

    @app.post('/api/ask')
    def ask(body:AskRequest,key:UUID=Header(alias='Idempotency-Key'),user=Depends(current_user)):
        _,index,_=catalog_data(cfg)
        fingerprint=hashlib.sha256(json.dumps({**body.model_dump(),'index_id':index.get('index_id','unbuilt')},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        rid=str(uuid4());started=time.perf_counter()
        with Session(engine) as db:
            old=db.scalar(select(QARecord).where(QARecord.user_id==user['user_id'],QARecord.idempotency_key==str(key)))
            if old:return existing_result(old,fingerprint)
            try:
                db.add(QARecord(id=rid,user_id=user['user_id'],idempotency_key=str(key),fingerprint=fingerprint,question=body.question,mode=body.mode,venue=body.venue,year=body.year,status='processing'))
                db.commit()
            except IntegrityError:
                db.rollback();old=db.scalar(select(QARecord).where(QARecord.user_id==user['user_id'],QARecord.idempotency_key==str(key)))
                if old:return existing_result(old,fingerprint)
                raise
        logger.info(json.dumps({'request_id':rid,'stage':'processing','mode':body.mode}))
        failure=None
        try:result=app.state.answerer(body)
        except PipelineError as error:failure=error
        except Exception:failure=PipelineError('application','unexpected_error',500)
        if failure:
            error=failure
            try:
                with Session(engine) as db,db.begin():
                    record=db.get(QARecord,rid);record.status='failed';record.error_stage=error.stage;record.error_kind=error.kind;record.http_status=error.status;record.duration_ms=round((time.perf_counter()-started)*1000)
            except SQLAlchemyError:raise HTTPException(503,{'message':'处理失败且最终状态未保存，请按记录编号核对。','request_id':rid,'stage':'persistence'}) from None
            logger.warning(json.dumps({'request_id':rid,'stage':error.stage,'kind':error.kind}))
            raise HTTPException(error.status,{'message':'服务未成功完成，请根据阶段检查服务状态。','request_id':rid,'stage':error.stage,'kind':error.kind}) from None
        try:
            with Session(engine) as db,db.begin():
                record=db.get(QARecord,rid);record.answer=result['answer'];record.references=result['references'];record.retrieval=result['retrieval'];record.llm_generated=result['llm_generated'];record.status='completed';record.http_status=200;record.duration_ms=round((time.perf_counter()-started)*1000)
                db.flush();output=serialize(record)
        except SQLAlchemyError:raise HTTPException(503,{'message':'答案已返回但数据库未确认保存，请核对记录后再决定是否重试。','request_id':rid,'stage':'persistence'}) from None
        logger.info(json.dumps({'request_id':rid,'stage':'completed','duration_ms':output['duration_ms']}))
        return output

    web=ROOT/'app/web'
    mimetypes.add_type('application/javascript','.js');mimetypes.add_type('application/javascript','.mjs')
    if web.exists():
        @app.get('/')
        def home():return FileResponse(web/'index.html')
        app.mount('/static',StaticFiles(directory=web),name='static')
    return app

app=create_app()
