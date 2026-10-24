import json
from pathlib import Path
import pytest
from app.corpus import atomic_json,parse_catalog

def load(path):return json.loads(path.read_text(encoding='utf8'))

@pytest.fixture
def setup(tmp_path):
    paper=parse_catalog('## S&P\n### sp2024\n* [Existing](https://example.org/old.pdf)','a'*40)[0]
    paper.update(status='parsed',pdf_sha256='oldhash',page_count=3)
    manifest={'source':{'commit':'a'*40},'papers':[paper]}
    receipt={'status':'verified','index_id':'old','workspace':'smartcontract_top4_v1','paper_ids':[paper['paper_id']],'paper_count':1,'chunk_count':1}
    atomic_json(tmp_path/'data/manifest.json',manifest);atomic_json(tmp_path/'data/index_receipt.json',receipt)
    atomic_json(tmp_path/'data/.local_index.json',{'index_id':'old'})
    atomic_json(tmp_path/'runtime/lightrag/smartcontract_top4_v1/kv_store_text_chunks.json',{'old-chunk':{'content':'old evidence'}})
    atomic_json(tmp_path/'runtime/index_build/build.json',receipt)
    atomic_json(tmp_path/'runtime/index_build/receipts'/f'{paper["paper_id"]}.json',{'status':'complete','pdf_sha256':'oldhash','chunk_count':1})
    for folder,suffix in [('pdfs','.pdf'),('text','.json')]:
        path=tmp_path/'data'/folder/(paper['paper_id']+suffix);path.parent.mkdir(exist_ok=True);path.write_text('old artifact')
    return tmp_path,paper

class Docker:
    def __init__(self,fail=False):self.events=[];self.fail=fail
    def available(self):return True
    def running(self):return {'app':True,'lightrag':True}
    def stop(self):self.events.append('stop')
    def start(self):self.events.append('start')
    def ready(self,index_id,count):self.events.append(('ready',index_id,count))
    def build(self,job,manifest):
        self.events.append('build')
        if self.fail:raise RuntimeError('simulated build failure')
        data=load(manifest);candidate=job/'candidate'
        chunks=load(candidate/'runtime/lightrag/smartcontract_top4_v1/kv_store_text_chunks.json')
        chunks['new-chunk']={'content':'new evidence'}
        atomic_json(candidate/'runtime/lightrag/smartcontract_top4_v1/kv_store_text_chunks.json',chunks)
        receipt={'status':'verified','index_id':'new','workspace':'smartcontract_top4_v1','paper_ids':[p['paper_id'] for p in data['papers']],
            'paper_count':len(data['papers']),'chunk_count':2}
        atomic_json(candidate/'data/index_receipt.json',receipt);atomic_json(candidate/'data/.local_index.json',{'index_id':'new'})
        atomic_json(candidate/'runtime/index_build/build.json',receipt)

def snapshot(old,include_new=True):
    rows=[old]
    if include_new:rows+=parse_catalog('## NDSS\n### ndss2026\n* [New Paper](https://example.org/new.pdf)','b'*40)
    return {'commit':'b'*40},'# source fixture',rows

def downloader(root):
    def download(paper):
        for folder,suffix in [('pdfs','.pdf'),('text','.json')]:
            path=root/'data'/folder/(paper['paper_id']+suffix);path.parent.mkdir(exist_ok=True);path.write_text('new artifact')
        return {**paper,'status':'parsed','pdf_sha256':'newhash','page_count':3}
    return download

def test_no_new_papers_never_stop_or_build_and_check_mode_never_downloads(setup):
    from app.update_runner import run_update
    root,old=setup;docker=Docker()
    report=run_update(root,docker=docker,fetcher=lambda:snapshot(old,False),downloader=lambda p:pytest.fail('no download'))
    assert report['status']=='no_change' and not docker.events
    report=run_update(root,docker=docker,fetcher=lambda:snapshot(old),downloader=lambda p:pytest.fail('check only'),execute=False)
    assert report['status']=='preview' and report['new_titles']==1 and not docker.events

def test_success_promotes_only_new_paper_and_duplicate_run_is_noop(setup):
    from app.update_runner import run_update
    root,old=setup;docker=Docker()
    result=run_update(root,docker=docker,fetcher=lambda:snapshot(old),downloader=downloader(root))
    assert result['status']=='updated' and result['added_to_index']==1
    assert load(root/'data/.live_index_receipt.json')['paper_count']==2
    assert load(root/'runtime/lightrag/smartcontract_top4_v1/kv_store_text_chunks.json')['old-chunk']['content']=='old evidence'
    assert not (root/'data/index_build.lock').exists()
    assert load(root/'data/manifest.json')['papers']==[old]
    again=run_update(root,docker=docker,fetcher=lambda:snapshot(old),downloader=lambda p:pytest.fail('duplicate download'))
    assert again['status']=='no_change' and docker.events.count('build')==1

def test_failed_candidate_does_not_replace_old_index_and_restores_services(setup):
    from app.update_runner import run_update
    root,old=setup;docker=Docker(fail=True)
    result=run_update(root,docker=docker,fetcher=lambda:snapshot(old),downloader=downloader(root))
    assert result['status']=='failed'
    assert load(root/'data/.local_index.json')['index_id']=='old'
    assert 'old-chunk' in load(root/'runtime/lightrag/smartcontract_top4_v1/kv_store_text_chunks.json')
    assert not (root/'data/index_build.lock').exists() and docker.events[-1]=='start'

def test_update_lock_prevents_a_second_writer(setup):
    from app.update_runner import UpdateLock,run_update
    root,old=setup
    with UpdateLock(root):
        result=run_update(root,docker=Docker(),fetcher=lambda:pytest.fail('no network under another writer'))
    assert result['status']=='already_running'

def test_status_does_not_present_the_previous_success_as_current_completion(setup):
    from app.update_runner import UpdateLock,update_status
    root,_=setup;atomic_json(root/'runtime/updates/latest.json',{'status':'no_change'})
    with UpdateLock(root):assert update_status(root)['status']=='running'
    assert update_status(root)['status']=='no_change'

def test_recovery_from_interrupted_promotion_restores_complete_old_state(setup):
    from app.update_runner import IndexTransaction
    root,old=setup;job=root/'runtime/updates/interrupted';job.mkdir(parents=True)
    transaction=IndexTransaction(root,job);transaction.begin();transaction.prepare()
    transaction.journal('promoting')
    transaction.move(root/'runtime/lightrag',job/'previous/lightrag')
    (root/'runtime/lightrag').mkdir();(root/'runtime/lightrag/partial').write_text('partial index')
    transaction.rollback()
    assert 'old-chunk' in load(root/'runtime/lightrag/smartcontract_top4_v1/kv_store_text_chunks.json')
    assert not (root/'data/index_build.lock').exists()

def test_download_only_when_docker_is_off_does_not_start_services(setup):
    from app.update_runner import run_update
    root,old=setup;docker=Docker();docker.available=lambda:False
    result=run_update(root,docker=docker,fetcher=lambda:snapshot(old),downloader=downloader(root))
    assert result['status']=='pending_services' and not docker.events
    assert len(load(root/'data/.live_manifest.json')['papers'])==2

def test_scheduled_run_skips_all_network_when_project_is_stopped(setup):
    from app.update_runner import run_update
    root,_=setup;docker=Docker();docker.available=lambda:False
    result=run_update(root,docker=docker,scheduled=True,fetcher=lambda:pytest.fail('stopped schedule must not fetch'))
    assert result['status']=='skipped_services' and not docker.events

def test_fulltext_duplicate_of_an_earlier_pending_run_is_not_imported_twice(setup):
    from app.update_runner import run_update
    root,old=setup;offline=Docker();offline.available=lambda:False
    first=run_update(root,docker=offline,fetcher=lambda:snapshot(old),downloader=downloader(root))
    assert first['status']=='pending_services'
    source,markdown,rows=snapshot(old)
    rows+=parse_catalog('## CCS\n### ccs2026\n* [Different title same PDF](https://example.org/copy.pdf)','c'*40)
    report=run_update(root,docker=Docker(),fetcher=lambda:(source,markdown,rows),downloader=downloader(root))
    assert report['added_to_index']==1
    assert sum(p['status']=='duplicate_fulltext' for p in load(root/'data/.live_manifest.json')['papers'])==1

def test_recovery_failure_still_has_a_current_audit_report(setup):
    from app.update_runner import run_update,IndexTransaction
    root,_=setup;job=root/'runtime/updates/interrupted';job.mkdir(parents=True)
    IndexTransaction(root,job).begin();docker=Docker();docker.available=lambda:False
    report=run_update(root,docker=docker,fetcher=lambda:pytest.fail('recovery before network'))
    assert report['status']=='failed' and load(root/'runtime/updates/latest.json')['status']=='failed'

def test_exhausted_index_attempts_are_visible_and_can_be_explicitly_requeued(setup):
    from app.update_runner import run_update,requeue_paper
    root,old=setup;source,markdown,rows=snapshot(old);new=rows[-1]
    new.update(status='parsed',pdf_sha256='newhash',page_count=3,update_managed=True,index_attempts=3)
    atomic_json(root/'data/.live_manifest.json',{'source':source,'papers':rows})
    report=run_update(root,docker=Docker(),fetcher=lambda:(source,markdown,rows),downloader=lambda p:pytest.fail('already parsed'))
    assert report['status']=='retry_limit_reached' and report['blocked_index_count']==1
    assert requeue_paper(root,new['paper_id'])['status']=='requeued'
    queued=load(root/'data/.live_manifest.json')['papers'][-1]
    assert queued['index_attempts']==0
    with pytest.raises(ValueError):requeue_paper(root,old['paper_id'])

def test_existing_manual_build_is_never_stopped_or_unlocked(setup):
    from app.update_runner import run_update
    root,old=setup;docker=Docker();gate=root/'data/index_build.lock';gate.write_text('manual-build')
    result=run_update(root,docker=docker,fetcher=lambda:snapshot(old),downloader=downloader(root))
    assert result['status']=='failed' and docker.events==[]
    assert gate.read_text()=='manual-build'

def test_failed_restart_after_promotion_rolls_back_both_index_and_metadata(setup):
    from app.update_runner import run_update
    root,old=setup;docker=Docker()
    def not_ready(*args):raise RuntimeError('startup_failed')
    docker.ready=not_ready
    result=run_update(root,docker=docker,fetcher=lambda:snapshot(old),downloader=downloader(root))
    assert result['status']=='failed' and result['old_index_restored'] is True
    assert not (root/'data/.live_index_receipt.json').exists()
    assert load(root/'data/.local_index.json')['index_id']=='old'
