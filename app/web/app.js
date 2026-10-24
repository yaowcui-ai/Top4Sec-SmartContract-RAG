import {renderMarkdown} from './markdown.js';

const $=id=>document.getElementById(id);
let user=null,revision=0,busy=false,paperOffset=0,historyOffset=0,papers=[],pending=null;
const turns=new Map();
const venueNames={SP:'IEEE S&P',CCS:'ACM CCS',USENIX:'USENIX Security',NDSS:'NDSS'};
const message=value=>typeof value==='string'?value:value?.message||'请求参数不符合要求，或服务暂时不可用。';
function error(text){$('error').textContent=text;$('error').hidden=false;}
function storageKey(){return 'scpaper.pending.v1:'+user.user_id;}
function setBusy(value){busy=value;for(const id of ['question','mode','submit-question','retry-pending','end-pending'])$(id).disabled=value||!user;$('submit-question').textContent=value?'正在处理…':'提交问题 ↗';}
function showPending(){
  pending=user?JSON.parse(sessionStorage.getItem(storageKey())||'null'):null;
  $('pending-panel').hidden=!pending;$('pending-question').textContent=pending?pending.question+'\n原模式：'+pending.mode:'';
}
function clearPrivate(){revision++;turns.clear();$('conversation').replaceChildren();$('history-list').replaceChildren();$('empty-chat').hidden=false;$('question').value='';$('error').hidden=true;historyOffset=0;}
function identity(next){
  if(user?.user_id!==next?.user_id)clearPrivate();
  user=next;$('login-form').hidden=Boolean(user);$('signed-in').hidden=!user;
  $('account-name').textContent=user?'当前账号：'+user.username:'';$('refresh-history').disabled=!user;
  $('auth-status').textContent=user?'私人问答只对当前账号可见。':'账号由本项目独立创建，不使用原 DocTrace 账号。';
  setBusy(false);try{showPending();}catch{error('浏览器操作记录无法读取，请检查会话存储后再提交。');$('submit-question').disabled=true;}
}
async function api(path,options={}){
  const headers=new Headers(options.headers);if(options.body)headers.set('Content-Type','application/json');
  if(user)headers.set('X-Paper-User',user.user_id);
  const response=await fetch(path,{...options,headers,credentials:'same-origin',cache:'no-store'});
  const body=await response.json();if(!response.ok){if(response.status===401)identity(null);throw Object.assign(new Error(message(body.detail)),{status:response.status,detail:body.detail});}return body;
}
function broadcast(){try{localStorage.setItem('scpaper.identity.changed',crypto.randomUUID());}catch{}}
async function session(){try{identity(await api('/api/me'));if(user)await loadHistory();}catch{identity(null);}}
$('login-form').addEventListener('submit',async event=>{event.preventDefault();$('login-button').disabled=true;try{identity(await api('/api/login',{method:'POST',body:JSON.stringify({username:$('username').value.trim(),password:$('password').value})}));$('password').value='';broadcast();await loadHistory();}catch(e){$('auth-status').textContent=e.message;}finally{$('login-button').disabled=false;}});
$('logout-button').addEventListener('click',async()=>{try{await api('/api/logout',{method:'POST',body:'{}'});identity(null);broadcast();}catch{error('退出未确认，请保持谨慎并重试。');}});
window.addEventListener('storage',event=>{if(event.key==='scpaper.identity.changed'){clearPrivate();session();}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!busy)session();});

function focus(turn){turn.scrollIntoView({behavior:'smooth',block:'start'});turn.focus({preventScroll:true});for(const b of $('history-list').querySelectorAll('button'))b.classList.toggle('active',b.dataset.id===turn.dataset.id);}
function createTurn(question){
  $('empty-chat').hidden=true;const turn=document.createElement('article');turn.className='turn';turn.tabIndex=-1;
  const q=document.createElement('div');q.className='question-bubble';const s=document.createElement('span');s.className='speaker';s.textContent='你';const text=document.createElement('span');text.textContent=question;q.append(s,text);
  const answer=document.createElement('div');answer.className='answer-bubble';const speaker=document.createElement('span');speaker.className='speaker';speaker.textContent='论文助手';const content=document.createElement('div');content.className='answer-content';content.textContent='正在检索论文证据…';answer.append(speaker,content);turn.append(q,answer);$('conversation').append(turn);return turn;
}
function render(record,pendingTurn=null,activate=true){
  let turn=turns.get(record.request_id)||pendingTurn||createTurn(record.question);
  if(pendingTurn&&turn!==pendingTurn)pendingTurn.remove();
  turns.set(record.request_id,turn);turn.dataset.id=record.request_id;turn.dataset.status=record.status;turn.dataset.time=record.created_at||new Date().toISOString();
  const answer=turn.querySelector('.answer-bubble'),content=turn.querySelector('.answer-content');
  answer.querySelectorAll('.references,.record-meta').forEach(e=>e.remove());
  if(record.status==='completed')renderMarkdown(content,record.answer||'未保存答案。');
  else content.textContent=record.status==='failed'?`本次请求未完成。阶段：${record.error_stage||'unknown'}；类型：${record.error_kind||'unknown'}`:'该记录尚未保存最终结果，可能仍在处理或中途停止。';
  const refs=document.createElement('div');refs.className='references';const targets=new Map();
  if(record.references?.length){const title=document.createElement('p');title.textContent='原文依据（PDF 文件页码）';refs.append(title);}
  for(const reference of record.references||[]){
    const detail=document.createElement('details'),summary=document.createElement('summary');
    summary.textContent=`[${reference.reference_id}] ${reference.title} · ${venueNames[reference.venue]||reference.venue} ${reference.year} · PDF 第${reference.page}页`;
    const link=document.createElement('a');link.href='/api/papers/'+encodeURIComponent(reference.paper_id)+'/file#page='+reference.page;link.target='_blank';link.rel='noopener';link.textContent='打开本机原始 PDF ↗';detail.append(summary,link);
    for(const excerpt of reference.content||[]){const pre=document.createElement('pre');pre.textContent=excerpt;detail.append(pre);}refs.append(detail);targets.set(String(reference.reference_id),detail);
  }
  if(targets.size){answer.append(refs);const walker=document.createTreeWalker(content,NodeFilter.SHOW_TEXT),nodes=[];while(walker.nextNode())if(!walker.currentNode.parentElement.closest('pre,code,a,button'))nodes.push(walker.currentNode);
    for(const node of nodes){let start=0;const fragment=document.createDocumentFragment();for(const match of node.textContent.matchAll(/\[(\d+)\]/g)){if(!targets.has(match[1]))continue;fragment.append(node.textContent.slice(start,match.index));const button=document.createElement('button');button.type='button';button.className='citation';button.textContent=match[0];button.addEventListener('click',()=>{const d=targets.get(match[1]);d.open=true;d.scrollIntoView({behavior:'smooth',block:'nearest'});});fragment.append(button);start=match.index+match[0].length;}if(start){fragment.append(node.textContent.slice(start));node.replaceWith(fragment);}}
  }
  const meta=document.createElement('details');meta.className='record-meta';const heading=document.createElement('summary');heading.textContent='记录与检索信息';const info=document.createElement('p');
  info.textContent=`记录编号：${record.request_id}\n模式：${record.mode}\n状态：${record.status}\n耗时：${record.duration_ms??'?'} ms`+(record.retrieval?`\n候选：${record.retrieval.candidate_count??'?'}；范围内：${record.retrieval.scoped_count??'?'}；保留：${record.retrieval.selected_count??'?'}\n重排降级：${record.retrieval.degraded?'是':'否'}`:'');meta.append(heading,info);answer.append(meta);
  const all=[...$('conversation').children].sort((a,b)=>String(a.dataset.time||'').localeCompare(String(b.dataset.time||'')));for(const item of all)$('conversation').append(item);
  if(activate)focus(turn);return turn;
}
async function loadHistory(append=false){
  if(!user)return;const version=revision;
  try{if(!append)historyOffset=0;const data=await api(`/api/history?limit=10&offset=${historyOffset}`);if(version!==revision)return;
    if(!append)$('history-list').replaceChildren();
    for(const record of data.items){const li=document.createElement('li'),button=document.createElement('button');button.dataset.id=record.request_id;const title=document.createElement('span');title.textContent=record.question;const meta=document.createElement('small');meta.textContent=`${record.mode} · ${record.status}`;button.append(title,meta);button.addEventListener('click',()=>render(record));li.append(button);$('history-list').append(li);render(record,null,false);}
    $('history-status').textContent=data.items.length?'点击记录定位到对应对话。':'暂无问答记录。';$('history-more').hidden=!data.has_more;historyOffset=data.next_offset;
  }catch(e){if(version===revision)$('history-status').textContent=e.message;}
}
$('refresh-history').addEventListener('click',()=>loadHistory());$('history-more').addEventListener('click',()=>loadHistory(true));

async function send(operation){
  if(busy||!user)return;const version=revision;setBusy(true);$('error').hidden=true;
  const turn=turns.get('pending-'+operation.key)||createTurn(operation.question);turns.set('pending-'+operation.key,turn);focus(turn);
  try{const record=await api('/api/ask',{method:'POST',headers:{'Idempotency-Key':operation.key},body:JSON.stringify({question:operation.question,mode:operation.mode,venue:operation.venue,year:operation.year})});if(version!==revision)return;render(record,turn);turns.delete('pending-'+operation.key);sessionStorage.removeItem(storageKey());showPending();$('request-status').textContent='回答已保存，点击引用可核对原文。';}
  catch(e){if(version!==revision)return;
    if(e.detail?.request_id){const saved=turns.get(e.detail.request_id);if(saved&&saved!==turn)turn.remove();else{turn.dataset.id=e.detail.request_id;turns.set(e.detail.request_id,turn);}turns.delete('pending-'+operation.key);}
    if(turn.isConnected&&turn.dataset.status!=='completed')turn.querySelector('.answer-content').textContent=e.detail?.request_id?`请求尚未成功确认。记录编号：${e.detail.request_id}，阶段：${e.detail.stage||'unknown'}。`:'连接中断或结果未确认。服务端可能仍在处理，请先查看个人历史。';error(e.message+(e.detail?.request_id?'\n记录编号：'+e.detail.request_id:''));}
  finally{if(version===revision){setBusy(false);await loadHistory();}}
}
$('question-form').addEventListener('submit',event=>{event.preventDefault();if(!user||busy)return;const question=$('question').value.trim();if(!question)return error('请输入具体问题。');
  try{showPending();if(pending)return error('请先核对或结束上一条操作的跟踪。');const operation={key:crypto.randomUUID(),question,mode:$('mode').value,venue:$('venue').value||null,year:$('year').value?Number($('year').value):null};sessionStorage.setItem(storageKey(),JSON.stringify(operation));showPending();send(operation);}catch{error('无法保存本页操作编号，本次没有发送请求。');}});
$('retry-pending').addEventListener('click',()=>{showPending();if(pending)send(pending);});
$('end-pending').addEventListener('click',()=>{if(!busy&&user){sessionStorage.removeItem(storageKey());showPending();$('request-status').textContent='已结束本页跟踪，不会取消或删除服务端记录。';}});

async function loadPapers(append=false){
  if(!append){paperOffset=0;$('paper-list').replaceChildren();}
  const params=new URLSearchParams({limit:30,offset:paperOffset,q:$('search').value});if($('venue').value)params.set('venue',$('venue').value);if($('year').value)params.set('year',$('year').value);
  try{const data=await api('/api/catalog?'+params);$('catalog-count').textContent=`范围内 ${data.total} 篇`;$('index-badge').textContent=`全文已索引 ${data.indexed_count} 篇`;
    for(const paper of data.items){const card=document.createElement('article');card.className='paper-card';const title=document.createElement('h3');title.textContent=paper.title;const meta=document.createElement('div');meta.className='paper-meta';const label=document.createElement('span');label.textContent=`${venueNames[paper.venue]} · ${paper.year}`;const state=document.createElement('span');state.className=paper.indexed?'ready':'unavailable';state.textContent=paper.indexed?'全文已索引':paper.status==='parsed'?'全文已解析，待建索引':paper.status==='pending'?'待处理':'全文暂不可用';meta.append(label,state);
      if(paper.source_url){const link=document.createElement('a');link.href=paper.source_url;link.target='_blank';link.rel='noopener';link.textContent='来源 ↗';meta.append(link);}if(paper.indexed){const button=document.createElement('button');button.textContent='针对这篇提问';button.addEventListener('click',()=>{$('question').value=`请根据《${paper.title}》说明它解决的问题、核心方法与局限，并提供原文依据。`;$('question').scrollIntoView({behavior:'smooth',block:'center'});$('question').focus();});meta.append(button);}card.append(title,meta);$('paper-list').append(card);
    }paperOffset+=data.items.length;$('papers-more').hidden=paperOffset>=data.total;
  }catch(e){$('catalog-count').textContent='目录加载失败';error(e.message);}
}
for(let year=2026;year>=2015;year--){const option=document.createElement('option');option.value=year;option.textContent=year;$('year').append(option);}
$('search-button').addEventListener('click',()=>loadPapers());$('venue').addEventListener('change',()=>loadPapers());$('year').addEventListener('change',()=>loadPapers());$('papers-more').addEventListener('click',()=>loadPapers(true));
setBusy(false);loadPapers();session();
