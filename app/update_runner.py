"""Host-side bounded updates; the active index is never edited by a second SDK process."""
from contextlib import AbstractContextManager
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
import collections,json,os,shutil,subprocess,time
import httpx
from .corpus import ROOT,atomic_json,download_one
from .update_catalog import fetch_snapshot,merge_catalog,pending_rows

IMAGE='ghcr.io/hkuds/lightrag@sha256:5bdbd524931b011df246fe20888d110cef691e6804c12cde636a2b746d7de27e'

def read(path,default=None):return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default
def live_file(root,name):
    path=root/'data'/('.live_'+name)
    return path if path.exists() else root/'data'/name

class UpdateLock(AbstractContextManager):
    def __init__(self,root):self.path=root/'runtime/updates/run.lock';self.file=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True);self.file=self.path.open('a+b');self.file.seek(0)
        if self.path.stat().st_size==0:self.file.write(b'0');self.file.flush();self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close();self.file=None;raise BlockingIOError('another_update_is_running') from None
        return self
    def __exit__(self,*args):
        if self.file:
            self.file.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_UN)
            self.file.close()

class DockerCoordinator:
    def __init__(self,root):
        self.root=root;self.binary=shutil.which('docker')
        if not self.binary:raise RuntimeError('docker_cli_unavailable')
        self.prefix=['compose','--project-name','scpaper-rag','--project-directory',str(root),'-f',str(root/'compose.yml'),'--profile','full']
    def command(self,args,timeout=60,log=None):
        if log:
            with log.open('wb') as output:
                result=subprocess.run([self.binary,*args],cwd=self.root,stdout=output,stderr=subprocess.STDOUT,timeout=timeout)
        else:result=subprocess.run([self.binary,*args],cwd=self.root,capture_output=True,timeout=timeout)
        if result.returncode:raise RuntimeError('docker_command_failed')
        return result.stdout.decode('utf-8',errors='replace').strip() if not log else ''
    def available(self):
        try:self.command(['info','--format','{{.ServerVersion}}'],timeout=10);return True
        except (RuntimeError,subprocess.TimeoutExpired,OSError):return False
    def running(self):
        # Compose versions emit either a JSON array or one JSON object per line.
        raw=self.command(self.prefix+['ps','--format','json','app','lightrag'])
        rows=json.loads(raw) if raw.startswith('[') else [json.loads(line) for line in raw.splitlines() if line]
        return {name:any(r.get('Service')==name and r.get('State')=='running' for r in rows) for name in ['app','lightrag']}
    def stop(self):
        self.command(self.prefix+['stop','--timeout','420','app'],timeout=450)
        self.command(self.prefix+['stop','--timeout','60','lightrag'],timeout=90)
    def start(self):self.command(self.prefix+['up','-d','lightrag','app'],timeout=180)
    def build(self,job,manifest):
        state='/project/'+(job/'candidate').relative_to(self.root).as_posix();source='/project/'+manifest.relative_to(self.root).as_posix()
        self.command(['run','--rm','--name','scpaper-index-update-'+job.name[-8:],'-v',str(self.root)+':/project','--entrypoint','/app/.venv/bin/python',IMAGE,
            '/project/scripts/build_index.py','--execute','--incremental','--state-dir',state,'--manifest',source],timeout=7200,log=job/'sdk.private.log')
    def cancel(self,job):
        name='scpaper-index-update-'+job.name[-8:]
        if self.command(['ps','-q','--filter','name=^/'+name+'$']):self.command(['stop','--time','60',name],timeout=90)
    def ready(self,index_id,count):
        from .settings import Settings
        cfg=Settings.load()
        with httpx.Client(trust_env=False,timeout=4) as client:
            for _ in range(40):
                try:
                    rag=client.get(cfg.lightrag_url+'/health',headers={'X-API-Key':cfg.lightrag_key})
                    app=client.get('http://127.0.0.1:8003/api/catalog?limit=1')
                    if rag.status_code==200 and app.status_code==200:
                        body=app.json()
                        if body['index']['index_id']==index_id and body['indexed_count']==count:return
                except (httpx.HTTPError,ValueError):pass
                time.sleep(1)
        raise RuntimeError('restarted_services_not_ready')

class IndexTransaction:
    NAMES=('.live_manifest.json','.live_index_receipt.json','.local_index.json')
    def __init__(self,root,job):
        self.root=root.resolve();self.job=job.resolve();self.candidate=self.job/'candidate'
        if not self.job.is_relative_to(self.root/'runtime/updates'):raise ValueError('Unsafe transaction path')
        self.gate=self.root/'data/index_build.lock'
    def journal(self,phase):atomic_json(self.job/'journal.json',{'phase':phase,'job':self.job.name,'updated_at':datetime.now(timezone.utc).isoformat()})
    def begin(self):
        if self.gate.exists():raise RuntimeError('another_index_build_is_active')
        atomic_json(self.job/'original_metadata.json',{name:read(self.root/'data'/name) for name in self.NAMES})
        with self.gate.open('x',encoding='utf-8') as f:f.write('update:'+self.job.name)
        self.journal('prepared')
    def move(self,source,target):
        for path in [source,target]:
            if not path.resolve().is_relative_to(self.root/'runtime'):raise ValueError('Index move outside this project runtime refused')
        target.parent.mkdir(parents=True,exist_ok=True);source.rename(target)
    def prepare(self):
        self.candidate.mkdir(parents=True,exist_ok=True)
        for name in ['lightrag','index_build']:
            source=self.root/'runtime'/name
            if not source.is_dir():raise RuntimeError('verified_base_storage_missing')
            shutil.copytree(source,self.candidate/'runtime'/name)
        self.journal('building')
    def verify(self,base,expected_ids):
        receipt=read(self.candidate/'data/index_receipt.json',{})
        if receipt.get('status')!='verified' or set(receipt.get('paper_ids',[]))!=set(expected_ids):raise RuntimeError('candidate_receipt_invalid')
        workspace=base['workspace']
        if receipt.get('workspace')!=workspace:raise RuntimeError('candidate_workspace_changed')
        old=read(self.root/'runtime/lightrag'/workspace/'kv_store_text_chunks.json')
        new=read(self.candidate/'runtime/lightrag'/workspace/'kv_store_text_chunks.json')
        if len(new)!=receipt['chunk_count'] or any(identifier not in new or new[identifier]['content']!=value['content'] for identifier,value in old.items()):
            raise RuntimeError('candidate_lost_old_evidence')
        return receipt
    def promote(self,manifest):
        self.journal('promoting')
        for name in ['lightrag','index_build']:
            self.move(self.root/'runtime'/name,self.job/'previous'/name)
            self.move(self.candidate/'runtime'/name,self.root/'runtime'/name)
        atomic_json(self.root/'data/.live_manifest.json',manifest)
        atomic_json(self.root/'data/.live_index_receipt.json',read(self.candidate/'data/index_receipt.json'))
        atomic_json(self.root/'data/.local_index.json',read(self.candidate/'data/.local_index.json'))
        self.clear_gate();self.journal('promoted')
    def clear_gate(self):
        if self.gate.exists() and self.gate.read_text(encoding='utf-8')=='update:'+self.job.name:self.gate.unlink()
    def rollback(self):
        for name in ['lightrag','index_build']:
            previous=self.job/'previous'/name;active=self.root/'runtime'/name
            if previous.exists():
                if active.exists():self.move(active,self.job/'rejected'/name)
                self.move(previous,active)
        for name,value in read(self.job/'original_metadata.json').items():
            if name not in self.NAMES:raise ValueError('Unexpected metadata restoration path')
            path=self.root/'data'/name
            if value is None:
                if path.exists():path.unlink()
            else:atomic_json(path,value)
        self.clear_gate();self.journal('rolled_back')

def run_update(root=ROOT,*,docker=None,fetcher=None,downloader=None,execute=True,scheduled=False):
    root=Path(root).resolve();lock=UpdateLock(root)
    try:lock.__enter__()
    except BlockingIOError:return {'status':'already_running','added_to_index':0}
    job=None;report={'started_at':datetime.now(timezone.utc).isoformat(),'status':'running','added_to_index':0,'embedding_requested':False,'generation_requested':False}
    transaction=None;services_stopped=False
    try:
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid4().hex[:8]
        job=root/'runtime/updates'/stamp;job.mkdir();report['stage']='recovery'
        # Recover before new downloads, even if the upstream repository is unavailable.
        interrupted=[p.parent for p in (root/'runtime/updates').glob('*/journal.json') if read(p,{}).get('phase') in {'prepared','building','promoting','promoted','rolled_back'}]
        gate=root/'data/index_build.lock'
        if gate.exists() and gate.read_text(encoding='utf-8').startswith('update:'):
            owner=gate.read_text(encoding='utf-8')[7:]
            if '/' in owner or '\\' in owner or owner in {'','.','..'}:raise ValueError('Invalid update lock owner')
            orphan=root/'runtime/updates'/owner
            if orphan not in interrupted and (orphan/'original_metadata.json').exists():interrupted.append(orphan)
        if interrupted:
            docker=docker or DockerCoordinator(root)
            if not docker.available():raise RuntimeError('docker_required_to_recover_interrupted_update')
            for old_job in interrupted:
                if hasattr(docker,'cancel'):docker.cancel(old_job)
                docker.stop();recovery=IndexTransaction(root,old_job);recovery.rollback();docker.start();recovery.journal('rollback_completed')
        if scheduled:
            report['stage']='project_status'
            try:docker=docker or DockerCoordinator(root)
            except RuntimeError:report['status']='skipped_services';return report
            if not docker.available() or not all(docker.running().values()):report['status']='skipped_services';return report
        report['stage']='source_check'
        if fetcher:source,markdown,latest=fetcher()
        else:
            with httpx.Client(timeout=30,follow_redirects=True,headers={'User-Agent':'Top4Sec-Paper-Update/1.0'}) as client:source,markdown,latest=fetch_snapshot(client)
        (job/'source.README.md').write_text(markdown,encoding='utf-8');atomic_json(job/'source.json',source)
        previous=read(live_file(root,'manifest.json'));base=read(live_file(root,'index_receipt.json'),{})
        manifest,delta=merge_catalog(previous,latest,source);report.update(delta,source_commit=source['commit'])
        batch=pending_rows(manifest);indexed=set(base.get('paper_ids',[]))
        if not execute:
            report.update(status='preview',download_candidates=len(batch));return report
        lookup={p['paper_id']:p for p in manifest['papers']}
        known_hashes={}
        for paper in sorted(manifest['papers'],key=lambda p:p['paper_id'] not in indexed):
            digest=paper.get('pdf_sha256')
            if paper['status']!='parsed' or not digest:continue
            if digest in known_hashes and paper['paper_id'] not in indexed:paper.update(status='duplicate_fulltext',duplicate_of=known_hashes[digest])
            else:known_hashes.setdefault(digest,paper['paper_id'])
        report['stage']='download'
        report['download_results']=[]
        for paper in batch:
            attempts=paper.get('update_attempts',0)+1
            try:result=(downloader or (lambda p:download_one(p,resolve_missing=True)))(paper)
            except Exception as error:result={**paper,'status':'failed','update_error_type':type(error).__name__}
            result['update_attempts']=attempts;result['update_managed']=True
            if result.get('status')=='parsed' and result.get('pdf_sha256') in known_hashes:
                result.update(status='duplicate_fulltext',duplicate_of=known_hashes[result['pdf_sha256']])
            elif result.get('status')=='parsed':known_hashes[result['pdf_sha256']]=result['paper_id']
            lookup[paper['paper_id']]=result
            report['download_results'].append({'paper_id':result['paper_id'],'title':result['title'],'status':result['status'],'attempts':attempts})
        manifest['papers']=[lookup[p['paper_id']] for p in manifest['papers']]
        manifest['summary']=dict(collections.Counter(p['status'] for p in manifest['papers']))
        additions=[p for p in manifest['papers'] if p['status']=='parsed' and p['paper_id'] not in indexed and p.get('index_attempts',0)<3][:10]
        blocked_index=sum(p['status']=='parsed' and p['paper_id'] not in indexed and p.get('index_attempts',0)>=3 for p in manifest['papers'])
        blocked_download=sum(bool(p.get('update_managed')) and p['status'] not in {'parsed','duplicate_fulltext','non_paper_resource'} and p.get('update_attempts',0)>=3 for p in manifest['papers'])
        report.update(download_attempts=len(batch),ready_for_index=len(additions),blocked_index_count=blocked_index,blocked_download_count=blocked_download)
        report['blocked_papers']=[{'paper_id':p['paper_id'],'stage':'index' if p['status']=='parsed' else 'download'} for p in manifest['papers']
            if (p['status']=='parsed' and p['paper_id'] not in indexed and p.get('index_attempts',0)>=3)
            or (p.get('update_managed') and p['status'] not in {'parsed','duplicate_fulltext','non_paper_resource'} and p.get('update_attempts',0)>=3)]
        if manifest['papers']!=previous['papers'] or source!=previous.get('source'):
            atomic_json(root/'data/.live_manifest.json',manifest)
        if not additions:
            report['status']='retry_limit_reached' if blocked_index or blocked_download else ('no_change' if not delta['new_titles'] and not delta['new_links'] and not batch else 'catalog_updated')
            report['stage']='completed';return report
        report['stage']='waiting_services'
        docker=docker or DockerCoordinator(root)
        if not docker.available() or not all(docker.running().values()):report['status']='pending_services';return report
        if not base.get('status')=='verified' or not read(root/'data/.local_index.json',{}).get('index_id')==base.get('index_id'):
            raise RuntimeError('verified_local_base_required')
        for paper in additions:paper['index_attempts']=paper.get('index_attempts',0)+1
        atomic_json(root/'data/.live_manifest.json',manifest)
        prepared=IndexTransaction(root,job);prepared.begin();transaction=prepared
        report['stage']='candidate_build'
        services_stopped=True;docker.stop();transaction.prepare()
        selected=indexed|{p['paper_id'] for p in additions}
        report['index_batch']=[p['paper_id'] for p in additions]
        input_manifest=job/'manifest.json';atomic_json(input_manifest,{**manifest,'papers':[p for p in manifest['papers'] if p['paper_id'] in selected]})
        report['embedding_requested']=True;docker.build(job,input_manifest)
        receipt=transaction.verify(base,selected);report['stage']='promotion';transaction.promote(manifest)
        report['stage']='restart'
        docker.start();services_stopped=False;docker.ready(receipt['index_id'],receipt['paper_count'])
        transaction.journal('completed')
        report.update(status='updated',stage='completed',added_to_index=len(additions),paper_count=receipt['paper_count'],index_id=receipt['index_id'],old_papers_reused=len(indexed))
        return report
    except Exception as error:
        report.update(status='failed',error_type=type(error).__name__)
        if transaction:
            try:
                if hasattr(docker,'cancel'):docker.cancel(job)
                if not services_stopped:docker.stop()
                transaction.rollback();docker.start();transaction.journal('rollback_completed');report['old_index_restored']=True
            except Exception as recovery_error:report.update(old_index_restored=False,recovery_error_type=type(recovery_error).__name__)
        return report
    finally:
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        try:
            if job:
                report['report_file']=str(job/'report.json');atomic_json(job/'report.json',report);atomic_json(root/'runtime/updates/latest.json',report)
        finally:lock.__exit__(None,None,None)

def requeue_paper(root,paper_id):
    root=Path(root)
    with UpdateLock(root):
        manifest=read(live_file(root,'manifest.json'));indexed=set(read(live_file(root,'index_receipt.json'),{}).get('paper_ids',[]))
        paper=next((p for p in manifest['papers'] if p['paper_id']==paper_id),None)
        if not paper or paper_id in indexed or not paper.get('update_managed'):raise ValueError('paper_not_in_update_retry_queue')
        paper.update(update_attempts=0,index_attempts=0)
        if paper['status']!='parsed':paper['status']='pending'
        atomic_json(root/'data/.live_manifest.json',manifest)
        return {'status':'requeued','paper_id':paper_id,'provider_calls':0}

def update_status(root=ROOT):
    root=Path(root)
    try:
        with UpdateLock(root):return read(root/'runtime/updates/latest.json',{'status':'never_run'})
    except BlockingIOError:return {'status':'running','latest_completed_report':read(root/'runtime/updates/latest.json')}
