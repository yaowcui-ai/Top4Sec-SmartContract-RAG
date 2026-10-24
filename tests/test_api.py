from dataclasses import replace
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
import pytest
from app.settings import Settings
from app.main import create_app
from app.storage import Base,User,password_hasher

ORIGIN={'Origin':'http://127.0.0.1:8003'}

@pytest.fixture
def app(tmp_path):
    engine=create_engine('sqlite+pysqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    cfg=replace(Settings(),database_url='sqlite+pysqlite://',data_dir=tmp_path)
    application=create_app(cfg,engine=engine,initialize_admin=False)
    with TestClient(application) as c:
        with engine.begin() as conn:
            conn.execute(User.__table__.insert(),[{'id':str(uuid4()),'username':n,'password_hash':password_hasher.hash('TestPass8')} for n in ['alice','bob']])
        application.state.answerer=lambda req:{'answer':'论文证据回答 [1]','references':[],'llm_generated':True,'retrieval':{'mode':req.mode}}
        yield application,c

def login(c,user='alice'):
    r=c.post('/api/login',json={'username':user,'password':'TestPass8'},headers=ORIGIN)
    assert r.status_code==200

def ask(c,key=None,**body):
    return c.post('/api/ask',json={'question':'论文采用什么方法？',**body},headers={**ORIGIN,'Idempotency-Key':key or str(uuid4())})

def test_health_does_not_claim_models_ready(app):
    _,c=app;r=c.get('/health');assert r.status_code==200
    assert r.json()['service']=='smartcontract-paper-rag'
    assert r.json()['models_checked'] is False

def test_private_endpoints_require_login(app):
    _,c=app
    assert c.get('/api/history').status_code==401
    assert ask(c).status_code==401

def test_login_origin_cookie_and_logout(app):
    _,c=app
    assert c.post('/api/login',json={'username':'alice','password':'TestPass8'}).status_code==403
    r=c.post('/api/login',json={'username':'alice','password':'TestPass8'},headers=ORIGIN)
    assert 'HttpOnly' in r.headers['set-cookie'] and 'scpaper_session=' in r.headers['set-cookie']
    assert c.get('/api/me').json()['username']=='alice'
    assert c.post('/api/logout',json={},headers=ORIGIN).status_code==200
    assert c.get('/api/history').status_code==401

def test_wrong_password_returns_generic_failure(app):
    _,c=app;r=c.post('/api/login',json={'username':'alice','password':'incorrect'},headers=ORIGIN)
    assert r.status_code==401 and 'alice' not in r.text

@pytest.mark.parametrize('question',['   ',123,None,True,'x'*2001])
def test_invalid_questions_rejected_before_answerer(app,question):
    application,c=app;login(c)
    application.state.answerer=lambda req:pytest.fail('No model call for invalid question')
    assert ask(c,question=question).status_code==422

def test_modes_are_explicit_and_checked(app):
    _,c=app;login(c)
    assert ask(c,mode='invalid').status_code==422
    assert ask(c,mode='mix').json()['retrieval']['mode']=='mix'

def test_same_key_reuses_saved_result_and_changed_question_conflicts(app):
    application,c=app;login(c);calls=[]
    application.state.answerer=lambda req:(calls.append(req) or {'answer':'saved','references':[],'llm_generated':True,'retrieval':{'mode':req.mode}})
    key=str(uuid4());first=ask(c,key);second=ask(c,key)
    assert first.status_code==200 and second.json()['request_id']==first.json()['request_id']
    assert len(calls)==1
    assert ask(c,key,question='不同的问题').status_code==409
    assert ask(c,key,mode='mix').status_code==409

def test_history_is_personal_and_foreign_ids_return_404(app):
    _,c=app;login(c);rid=ask(c).json()['request_id']
    assert len(c.get('/api/history').json()['items'])==1
    c.post('/api/logout',json={},headers=ORIGIN);login(c,'bob')
    assert c.get('/api/history').json()['items']==[]
    assert c.get('/api/history/'+rid).status_code==404
    assert c.get('/api/history/not-a-uuid').status_code==422

def test_no_secret_or_hash_is_exposed_in_api(app):
    _,c=app;login(c)
    assert 'password_hash' not in c.get('/api/me').text
    assert 'token_hash' not in c.get('/api/me').text

def test_request_bound_to_another_browser_identity_is_rejected(app):
    _,c=app;login(c)
    assert c.get('/api/history',headers={'X-Paper-User':str(uuid4())}).status_code==409

def test_unexpected_pipeline_error_is_saved_without_private_error_text(app):
    application,c=app;login(c)
    def fail(req):raise RuntimeError('sensitive-provider-diagnostic')
    application.state.answerer=fail
    r=ask(c)
    assert r.status_code==500 and 'sensitive-provider-diagnostic' not in r.text
    saved=c.get('/api/history/'+r.json()['detail']['request_id']).json()
    assert saved['status']=='failed' and saved['error_kind']=='unexpected_error'

def test_upstream_timeout_saves_failure_instead_of_no_evidence(app):
    from app.rag import PipelineError
    application,c=app;login(c)
    def fail(req):raise PipelineError('generation','timeout',504)
    application.state.answerer=fail
    result=ask(c);assert result.status_code==504
    saved=c.get('/api/history/'+result.json()['detail']['request_id']).json()
    assert saved['status']=='failed' and saved['error_stage']=='generation'

def test_numeric_filters_and_foreign_venues_are_checked(app):
    _,c=app;login(c)
    assert ask(c,venue='ICSE').status_code==422
    assert ask(c,year=1900).status_code==422

def test_page_served_without_model_call(app):
    application,c=app;application.state.answerer=lambda req:pytest.fail('page must not query model')
    r=c.get('/');assert r.status_code==200
    assert '四大顶会' in r.text
