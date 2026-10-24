"""Generate new isolated credentials; optionally reuse ONLY the earlier model settings."""
from pathlib import Path
import argparse,secrets
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--reuse-from',type=Path);p.add_argument('--lightrag-from',type=Path);args=p.parse_args()
    if (ROOT/'.env').exists() or (ROOT/'.lightrag.env').exists():raise SystemExit('Private config already exists; no file was overwritten.')
    old=dotenv_values(args.reuse_from,interpolate=False) if args.reuse_from else {}
    rag=dotenv_values(args.lightrag_from,interpolate=False) if args.lightrag_from else {}
    service=secrets.token_hex(24)
    app={'MYSQL_HOST':'127.0.0.1','MYSQL_PORT':'3308','MYSQL_DATABASE':'smartcontract_papers','MYSQL_USER':'scpaper_app',
        'MYSQL_PASSWORD':secrets.token_urlsafe(24),'MYSQL_ROOT_PASSWORD':secrets.token_urlsafe(32),
        'LIGHTRAG_URL':'http://127.0.0.1:9630','LIGHTRAG_API_KEY':service,'AUTH_ALLOWED_ORIGIN':'http://127.0.0.1:8003',
        'AUTH_COOKIE_SECURE':'false','PROVIDER_TRUST_ENV':'false','APP_ADMIN_USERNAME':'researcher','APP_ADMIN_PASSWORD':secrets.token_urlsafe(12),
        'RERANK_URL':old.get('RERANK_URL','https://api.siliconflow.cn/v1/rerank'),'RERANK_MODEL':old.get('RERANK_MODEL','BAAI/bge-reranker-v2-m3'),
        'RERANK_API_KEY':old.get('RERANK_API_KEY',''),'ANSWER_URL':old.get('ANSWER_URL','https://api.deepseek.com/chat/completions'),
        'ANSWER_MODEL':old.get('ANSWER_MODEL','deepseek-flash'),'ANSWER_API_KEY':old.get('ANSWER_API_KEY','')}
    allowed=['LLM_BINDING','LLM_BINDING_HOST','LLM_BINDING_API_KEY','LLM_MODEL','EMBEDDING_BINDING','EMBEDDING_BINDING_HOST','EMBEDDING_BINDING_API_KEY','EMBEDDING_MODEL','EMBEDDING_DIM','EMBEDDING_TOKEN_LIMIT','OPENAI_LLM_EXTRA_BODY']
    graph=dict(dotenv_values(ROOT/'.lightrag.env.example',interpolate=False))
    graph.update({key:rag[key] for key in allowed if rag.get(key) is not None})
    graph.update({'HOST':'0.0.0.0','PORT':'9621','WORKSPACE':'smartcontract_top4_v1','LIGHTRAG_API_KEY':service,'MAX_ASYNC':'3','EMBEDDING_BATCH_NUM':'16','CHUNK_SIZE':'1200','CHUNK_OVERLAP_SIZE':'100','MAX_TOTAL_TOKENS':'40000'})
    def write(name,values):
        for value in values.values():
            if '\n' in value or "'" in value:raise ValueError('Unsupported env quoting; configure manually without exposing credentials.')
        (ROOT/name).write_text('\n'.join(k+"='"+v+"'" for k,v in values.items())+'\n',encoding='utf-8')
    write('.env',app);write('.lightrag.env',graph)
    print('New private config prepared; supplier keys reused where available. No credential values displayed.')
    print('Database: smartcontract_papers; user: scpaper_app; ports: MySQL 3308 / LightRAG 9630 / app 8003')

if __name__=='__main__':main()
