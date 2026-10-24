"""Explicit local administration; reads only this project's database and paths."""
import argparse,json,subprocess,sys
from getpass import getpass
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.settings import ROOT,Settings
from app.storage import User,password_hasher
from sqlalchemy import create_engine,select,text
from sqlalchemy.orm import Session
import httpx

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['status','create-user','backup-db']);parser.add_argument('--username');args=parser.parse_args()
    cfg=Settings.load()
    if args.command=='status':
        subprocess.run(['docker','compose','ps'],cwd=ROOT,check=False)
        for endpoint in ['/health','/api/catalog?limit=1']:
            try:
                body=httpx.get('http://127.0.0.1:8003'+endpoint,timeout=8,trust_env=False).json()
                if endpoint.startswith('/api'):body={k:body[k] for k in ['total','indexed_count','index']}
                print(endpoint,json.dumps(body,ensure_ascii=False))
            except Exception as error:print(endpoint,'unavailable:',type(error).__name__)
        return
    if args.command=='create-user':
        import re
        if not args.username or not re.fullmatch('[a-z][a-z0-9_]{2,31}',args.username):raise SystemExit('Use --username with 3-32 lowercase letters/digits/underscore.')
        password=getpass('New password (8-64 characters): ');confirmation=getpass('Confirm: ')
        if password!=confirmation or not 8<=len(password)<=64:raise SystemExit('Password mismatch or invalid length; no changes made.')
        engine=create_engine(cfg.database_url,hide_parameters=True)
        with Session(engine) as db,db.begin():
            if db.scalar(select(User).where(User.username==args.username)):raise SystemExit('User already exists; existing password was not changed.')
            db.add(User(username=args.username,password_hash=password_hasher.hash(password)))
        engine.dispose();print('Created:',args.username);return
    if args.command=='backup-db':
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');out=ROOT/'backups'/stamp/'smartcontract_papers.sql';out.parent.mkdir(parents=True)
        command=['docker','compose','exec','-T','mysql','sh','-c','export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; exec mysqldump --default-character-set=utf8mb4 --single-transaction -uroot --databases smartcontract_papers']
        result=subprocess.run(command,cwd=ROOT,capture_output=True)
        if result.returncode or not result.stdout:raise SystemExit('Backup failed; no SQL dump was written. Check this project mysql container.')
        out.write_bytes(result.stdout);print('Private UTF-8 backup saved:',out)

if __name__=='__main__':main()
