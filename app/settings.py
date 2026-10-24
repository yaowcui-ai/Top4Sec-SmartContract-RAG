"""Project-local settings with a database-name isolation guard."""
from dataclasses import dataclass,field,replace
from pathlib import Path
import os
from dotenv import dotenv_values
from sqlalchemy import URL

ROOT=Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class Settings:
    database_url:str=field(default='sqlite+pysqlite:///:memory:',repr=False)
    data_dir:Path=ROOT/'data'
    allowed_origin:str='http://127.0.0.1:8003'
    cookie_secure:bool=False
    lightrag_url:str='http://127.0.0.1:9630'
    lightrag_key:str=field(default='',repr=False)
    rerank_url:str='https://api.siliconflow.cn/v1/rerank'
    rerank_model:str='BAAI/bge-reranker-v2-m3'
    rerank_key:str=field(default='',repr=False)
    answer_url:str='https://api.deepseek.com/chat/completions'
    answer_model:str='deepseek-flash'
    answer_key:str=field(default='',repr=False)
    max_chars:int=30000
    provider_trust_env:bool=False
    admin_username:str='researcher'
    admin_password:str=field(default='',repr=False)

    @classmethod
    def load(cls):
        file=dotenv_values(ROOT/'.env',encoding='utf-8',interpolate=False)
        def value(key,default=''):return os.environ.get(key,file.get(key,default)) or default
        database=value('MYSQL_DATABASE','smartcontract_papers')
        if database!='smartcontract_papers':raise ValueError('This project only permits MYSQL_DATABASE=smartcontract_papers')
        url=URL.create('mysql+pymysql',username=value('MYSQL_USER','scpaper_app'),password=value('MYSQL_PASSWORD'),
            host=value('MYSQL_HOST','127.0.0.1'),port=int(value('MYSQL_PORT','3308')),database=database,query={'charset':'utf8mb4'})
        return cls(database_url=url.render_as_string(hide_password=False),
            data_dir=Path(value('DATA_DIR',str(ROOT/'data'))),allowed_origin=value('AUTH_ALLOWED_ORIGIN','http://127.0.0.1:8003'),
            cookie_secure=value('AUTH_COOKIE_SECURE','false').lower()=='true',
            lightrag_url=value('LIGHTRAG_URL','http://127.0.0.1:9630'),lightrag_key=value('LIGHTRAG_API_KEY'),
            rerank_url=value('RERANK_URL',cls.rerank_url),rerank_model=value('RERANK_MODEL',cls.rerank_model),rerank_key=value('RERANK_API_KEY'),
            answer_url=value('ANSWER_URL',cls.answer_url),answer_model=value('ANSWER_MODEL',cls.answer_model),answer_key=value('ANSWER_API_KEY'),
            provider_trust_env=value('PROVIDER_TRUST_ENV','false').lower()=='true',
            admin_username=value('APP_ADMIN_USERNAME','researcher'),admin_password=value('APP_ADMIN_PASSWORD'))
