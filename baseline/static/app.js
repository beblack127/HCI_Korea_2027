'use strict';
const CONDITION = document.body?.dataset?.condition || 'proposed';
const BASELINE = CONDITION === 'baseline';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stateLabels = {exploring:'방향 탐색',forming:'방향 형성',unknown:'판단 유보'};
const difficultyLabels = {concept:'개념 이해',connection:'관심과의 연결',selection:'후보 선택',formulation:'주제 구체화',unspecified:'유형 판단 유보'};
const supportLabels = {directions:'다음 탐색 방향',browse:'주제 둘러보기',concept:'개념 설명',connection:'관심과의 연결',compare:'후보 비교',formulate:'주제 구체화',literature:'문헌 검토',orient:'탐색 흐름 정리',wrapup:'탐색 정리'};
const historyLabels = {user_input:'질의 입력',topic_browse:'둘러보기',topic_refine:'구체화하기',paper_search_submit:'논문 검색',abstract_expand:'초록 펼치기',abstract_collapse:'초록 접기',paper_open:'원문 링크 열기',paper_save:'관심 논문에 담기',paper_unsave:'관심 논문에서 빼기',session_wrapup:'탐색 정리 선택'};
let snapshot = null, status = null, busy = false, searching = false, pendingTurn = null;
let selections = new Map(), expanded = new Set(), eventQueue = Promise.resolve(), toastTimer;
const localTurns = new Map();
let sendMotion = null;
let paperResult = null, showingSaved = false, searchPage = 1, allHistory = false;
let searchOrigin = null, thinkingStarted = 0, thinkingTimer = null, progressTimer = null, progressRun = 0;
const phaseLabels = {waiting:'요청을 전달하고 있어요',queued:'이전 요청이 끝나기를 기다리고 있어요',
  estimating:'대화와 선택을 바탕으로 탐색 상태를 정리하고 있어요',querying:'관련 문헌을 찾을 영어 검색어를 정리하고 있어요',
  retrieving:'OpenAlex에서 관련 문헌을 찾고 있어요',generating:'현재 탐색에 필요한 도움과 다음 행동을 정리하고 있어요',
  saving:'답변과 탐색 기록을 정리하고 있어요',completed:'답변을 마무리하고 있어요',error:'응답을 확인하고 있어요'};
if(BASELINE){phaseLabels.estimating='답변을 준비하고 있어요';phaseLabels.generating='답변과 주제 후보를 작성하고 있어요';}
function duration(seconds) {const s=Math.max(0,Math.floor(seconds));return s>=60?`${Math.floor(s/60)}분 ${s%60}초`:`${s}초`;}
function startThinking(requestId) {
  thinkingStarted=performance.now();$('thinkingPhase').textContent='탐색 맥락을 확인하고 있어요';$('turnDuration').hidden=true;
  const tick=()=>{const elapsed=(performance.now()-thinkingStarted)/1000;$('thinkingTime').textContent=`${duration(elapsed)} 동안 답변 생성 중`;$('thinkingNote').hidden=elapsed<90;};
  tick();thinkingTimer=setInterval(tick,1000);
  const run=++progressRun;
  const poll=async()=>{
    try {const p=await api(`/api/turn/progress?request_id=${encodeURIComponent(requestId)}`);
      if(run===progressRun&&busy){
        $('thinkingPhase').textContent=phaseLabels[p.phase]||phaseLabels.waiting;
        if(!BASELINE&&p.estimate&&p.estimate.request_id===requestId&&snapshot){
          snapshot.estimate=p.estimate;
          const known=new Set(snapshot.events.map(e=>e.id));
          snapshot.events.push(...(p.evidence_events||[]).filter(e=>!known.has(e.id)));
          snapshot.events.sort((a,b)=>a.id-b.id);renderState();
        }
      }
    } catch (_) {/* A failed progress check must not interrupt the actual generation request. */}
    if(run===progressRun&&busy)progressTimer=setTimeout(poll,2000);
  };
  progressTimer=setTimeout(poll,1000);
}
function stopThinking() {clearInterval(thinkingTimer);clearTimeout(progressTimer);progressRun++;}

async function api(path, body) {
  let response;
  try {
    response = await fetch(path, body === undefined ? {credentials:'same-origin'} : {
      method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Workspace-Request':'1'},body:JSON.stringify(body)});
  } catch (_) { throw new Error('서버에 연결할 수 없어요. 실행 중인 터미널을 확인해 주세요.'); }
  let value;
  try { value = await response.json(); } catch (_) { throw new Error('서버 응답을 확인하지 못했어요. 새로고침 후 다시 시도해 주세요.'); }
  if (!response.ok) {
    if (value.snapshot) renderSnapshot(value.snapshot);
    throw new Error(value.error || '요청을 처리하지 못했어요. 다시 시도해 주세요.');
  }
  return value;
}

function toast(message) {
  $('toast').textContent=message; $('toast').hidden=false;
  clearTimeout(toastTimer); toastTimer=setTimeout(()=>{$('toast').hidden=true;},3000);
}
function timeOf(iso) { return new Date(iso).toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit',hour12:false}); }
function setBusy(value) {
  busy=value; $('thinking').hidden=!value; $('sendButton').disabled=value;
  $('newSession').disabled=value||searching; $('chatInput').disabled=value;
  document.querySelectorAll('.rec-actions button').forEach(b=>b.disabled=value||!selections.get(b.dataset.message));
  if (value) scrollConversation();
}
function scrollConversation(immediate=false) {
  const pane=$('conversation');
  if(immediate){const behavior=pane.style.scrollBehavior;pane.style.scrollBehavior='auto';pane.scrollTop=pane.scrollHeight;pane.style.scrollBehavior=behavior;}
  else requestAnimationFrame(()=>{pane.scrollTop=pane.scrollHeight;});
}

function reduceMotion() {return typeof matchMedia==='function'&&matchMedia('(prefers-reduced-motion: reduce)').matches;}
function sendOrigin() {
  if(reduceMotion()||typeof $('chatInput').getBoundingClientRect!=='function')return null;
  const r=$('chatInput').getBoundingClientRect();
  return {left:r.left-16,top:r.top-11,width:r.width+32,height:r.height+22};
}
function bubbleForRequest(requestId) {
  return [...$('messages').querySelectorAll('[data-client-request]')]
    .find(row=>row.dataset.clientRequest===requestId)?.querySelector('.user-content');
}
function finishSendMotion(motion=sendMotion) {
  if(!motion||motion.done)return;
  motion.done=true;
  motion.bubble.classList.remove('motion-target');
  motion.floating?.remove();
  motion.animation?.cancel();
  if(sendMotion===motion)sendMotion=null;
}
function animateSendBubble(requestId,origin) {
  finishSendMotion();
  if(!origin||reduceMotion()||typeof document.createElement!=='function')return;
  const bubble=bubbleForRequest(requestId);
  if(!bubble||typeof bubble.animate!=='function')return;
  const target=bubble.getBoundingClientRect();
  if(!target.width||!target.height)return;
  // Very long messages stay readable in place instead of flying over the whole workspace.
  if(target.height>Math.max(180,$('conversation').clientHeight*.7)){
    bubble.animate([{transform:'translateY(12px)',opacity:.4},{transform:'translateY(0)',opacity:1}],{duration:180,easing:'ease-out'});
    return;
  }
  const floating=bubble.cloneNode(true);
  floating.classList.add('send-flight');floating.setAttribute('aria-hidden','true');
  const motion={bubble,floating,animation:null,done:false};
  try {
    document.body.appendChild(floating);
    const end={left:`${target.left}px`,top:`${target.top}px`,width:`${target.width}px`,height:`${target.height}px`,opacity:1,borderRadius:'22px 22px 7px 22px',backgroundColor:getComputedStyle(bubble).backgroundColor};
    motion.animation=floating.animate([
      {left:`${origin.left}px`,top:`${origin.top}px`,width:`${origin.width}px`,height:`${origin.height}px`,opacity:.94,borderRadius:'24px',backgroundColor:'#ffffff'},
      {...end,offset:.84,top:`${target.top-3}px`},end
    ],{duration:420,easing:'cubic-bezier(.18,.8,.25,1)',fill:'forwards'});
    sendMotion=motion;bubble.classList.add('motion-target');
    motion.animation.finished.then(()=>finishSendMotion(motion),()=>finishSendMotion(motion));
  } catch(_){finishSendMotion(motion);}
}

function renderSnapshot(value, updateMessages=true) {
  snapshot=value; $('sessionTitle').textContent=value.session.title;
  $('savedCount').textContent=value.saved_papers.length;
  let reconciled=false;
  for(const m of value.messages) {
    if(m.role==='user'&&localTurns.delete(m.request_id||m.content.request_id))reconciled=true;
  }
  renderState();
  if (updateMessages||reconciled) renderMessages();
}

function renderState() {
  if(BASELINE)return;
  const estimate=snapshot.estimate, state=estimate?.state;
  $('stageLabel').textContent=state ? stateLabels[state.direction_stage] || '판단 유보' : '탐색 전';
  $('stageLabel').classList.toggle('neutral',!state||state.direction_stage==='unknown');
  $('currentDirection').textContent=state?.current_direction || '관심에서 출발해 보세요';
  $('stateSummary').textContent=state?.summary || '대화와 선택을 바탕으로 현재 탐색을 정리합니다.';
  $('problemSection').hidden=!state?.research_problem;
  $('researchProblem').textContent=state?.research_problem||'';
  $('changeSection').hidden=!state?.recent_change;
  $('recentChange').textContent=state?.recent_change||'';
  const aspectLabels={interest:'관심·질문',problem:'연구 문제',perspective:'관점',scope:'검토 범위'};
  const aspects=(state?.clarified_aspects||[]).filter(a=>a.kind!=='problem');
  $('aspectsSection').hidden=!aspects.length;
  $('clarifiedAspects').innerHTML=aspects.map(a=>`<div class="state-aspect"><span>${esc(aspectLabels[a.kind]||'발화에서 확인')}</span><p>${esc(a.text)}</p></div>`).join('');
  const questions=state?.open_questions||state?.unresolved||[],directions=state?.observed_directions||[];
  $('openSection').hidden=!questions.length&&!directions.length;
  $('openQuestions').innerHTML=questions.map(q=>`<li>${esc(q)}</li>`).join('');
  $('observedDirections').innerHTML=directions.length?`<p class="section-note observed-label">선택·검토 기록이 있는 후보</p>${directions.map(d=>`<div class="observed-direction"><span>${esc(d.action)}</span><p>${esc(d.title)}</p></div>`).join('')}`:'';
  $('flowSection').hidden=!state?.flow_summary;
  $('flowSummary').textContent=state?.flow_summary||'';
  const readiness=state?.readiness;
  $('readinessSection').hidden=!['ready','confirmed'].includes(readiness?.status);
  $('readinessMessage').textContent=readiness?.message||'';
  $('readinessChecks').innerHTML=(readiness?.checks||[]).map(c=>`<li>${esc(c.label)} · ${c.met?'기록 확인':'확인 전'}</li>`).join('');
  const pending=estimate ? snapshot.events.filter(e=>e.id>estimate.through_event_id && (historyLabels[e.type]||e.type==='topic_select')).length : 0;
  const responseNote=state?.response_status==='generating'?' · 상태 추정 완료, 답변 생성 중':state?.response_status==='error'?' · 상태 저장됨, 답변 재시도 가능':'';
  $('stateUpdated').textContent=estimate ? `${timeOf(estimate.created_at)} 대화 기준${pending ? ` · 이후 활동 ${pending}건 기록` : ''}${responseNote}` : '';
  const difficulties=state?.difficulties||[], expressed=difficulties.some(d=>d.status==='expressed');
  const pendingDifficulties=state?.pending_difficulties||(state?.difficulty_history||[]).filter(h=>h.lifecycle==='unconfirmed'&&h.status==='expressed');
  const supportIssues=state?.support_issues||[];
  const difficultyLevel=difficulties.length ? (expressed?'expressed':'possible') : pendingDifficulties.length?'unconfirmed':'unknown';
  $('statePane').dataset.difficulty=difficultyLevel;
  $('difficultySection').hidden=!state||(!difficulties.length&&!state?.difficulty_history?.length&&!supportIssues.length);
  $('difficultySection').dataset.level=difficultyLevel;
  const unconfirmed=(state?.difficulty_history||[]).some(h=>h.lifecycle==='unconfirmed');
  $('difficultyStatus').textContent=difficulties.length ? (expressed?'어려움 표현 확인':'가능성 추정') : (unconfirmed?'이전 어려움 후속 확인 전':state?.difficulty_status==='clear'?'해결 표현 확인':'판단 유보');
  $('difficultyBadge').hidden=!difficulties.length&&!pendingDifficulties.length;
  $('difficultyBadge').textContent=difficulties.length ? difficulties.map(d=>difficultyLabels[d.type]||'탐색').join(' · ') + (expressed?' · 어려움 표현':' · 지원 필요 가능성') : pendingDifficulties.length?'이전 어려움 · 후속 확인 전':'';
  const shownDifficulties=[...difficulties,...pendingDifficulties.slice().reverse()];
  $('difficultyList').innerHTML=shownDifficulties.length ? shownDifficulties.map(d=>{
    const evidence=snapshot.events.slice().reverse().find(e=>d.evidence_ids?.includes(e.id)&&e.type==='user_input');
    const quote=d.evidence_quotes?.[0]?.quote||evidence?.payload.text||'';
    const waiting=d.lifecycle==='unconfirmed';
    const signals=d.recent_signals||d.signals||[];
    return `<div class="difficulty-item"><b>${esc(difficultyLabels[d.type]||'탐색의 어려움')}${waiting?' · 후속 확인 전':d.status==='possible'?' · 지원 필요 가능성':''}</b><p>${esc(waiting?'이전에 표현한 어려움이에요. 해소 여부는 아직 확인되지 않았어요.':d.description)}</p>${quote?`<blockquote class="difficulty-evidence">${esc(quote.slice(0,180))}${quote.length>180?'…':''}</blockquote>`:''}${signals.length?`<ul class="state-questions">${signals.map(s=>`<li>${esc(s.observation)}</li>`).join('')}</ul>`:''}</div>`;
  }).join('') : `<p class="difficulty-empty">${unconfirmed?'이전 어려움이 해소되었는지는 아직 확인되지 않았어요.':state?.difficulty_status==='clear'?'후속 발화에서 해당 어려움의 해소를 표현했어요.':'현재 기록만으로는 어려움의 유무를 판단하기 어려워요.'}</p>`;
  $('supportIssueList').innerHTML=supportIssues.map(s=>`<p class="section-note">${esc(s.observation)}</p>`).join('');
  const history=state?.difficulty_history||[], lifecycleLabels={active:'진행 중',unconfirmed:'후속 확인 전',resolved:'해결 표현 확인',out_of_scope:'현재 방향 밖'};
  $('difficultyHistory').hidden=!history.length;
  $('difficultyHistoryList').innerHTML=history.slice().reverse().map(h=>`<div class="difficulty-history-item"><b>${esc(difficultyLabels[h.type]||'탐색')} · ${esc(lifecycleLabels[h.lifecycle]||'후속 확인 전')}</b><p>${esc(h.topic||'이전 탐색 방향')}</p>${h.support_provided?'<span>관련 지원 제공됨 · 지원 제공 자체는 해결의 근거가 아닙니다.</span>':''}</div>`).join('');
  $('evidenceList').innerHTML=state?.evidence?.length ? state.evidence.map(e=>`<div class="evidence-item"><p class="evidence-observation">${esc(e.observation)}</p><p class="evidence-interpretation">해석: ${esc(e.interpretation)}</p></div>`).join('') : '<p class="empty-copy">탐색을 시작하면 실제 발화와 행동을 근거로 보여줍니다.</p>';
  $('nextSection').hidden=!state?.next_step;
  $('nextStep').textContent=state?.next_step||''; $('nextReason').textContent=state?.next_step_reason||'';
  const contexts=state?.recommendation_context||[];
  $('recommendationContext').hidden=!contexts.length;
  $('recommendationReasons').innerHTML=contexts.map(r=>`<div class="recommendation-reason"><b>${esc(r.title)}</b><span>${esc(r.relation_label)}</span><p>${esc(r.rationale)}</p></div>`).join('');
  const picked=snapshot.events.slice().reverse().find(e=>['topic_select','topic_browse','topic_refine'].includes(e.type)&&e.id>=(state?.direction_context_start_event_id||0));
  const pickedRec=picked?snapshot.messages.flatMap(m=>m.content.recommendations||[]).find(r=>r.id===picked.payload.recommendation_id):null;
  $('selectedConnection').hidden=!pickedRec;
  $('selectedConnection').innerHTML=pickedRec?`<div class="selected-connection"><span>최근 선택한 검토 후보</span><b>${esc(pickedRec.title)}</b><p>${esc(pickedRec.rationale||'이 방향을 검토 대상으로 선택한 기록이에요. 연구 문제의 확정을 의미하지는 않아요.')}</p></div>`:'';
  let events=snapshot.events.filter(e=>historyLabels[e.type]);
  $('historyCount').textContent=`${events.length}개 활동`;
  const total=events.length;
  if (!allHistory) events=events.slice(-12);
  $('historyList').innerHTML=events.length ? events.slice().reverse().map(e=>{
    const p=e.payload, content=p.text||p.title||p.query||'';
    return `<li class="history-item"><div class="history-type"><span>${esc(historyLabels[e.type])}</span><time>${timeOf(e.created_at)}</time></div><p class="history-text">${esc(content)}</p></li>`;
  }).join('') + (total>12&&!allHistory ? `<li><button class="text-button" id="moreHistory">이전 활동 ${total-12}건 더 보기</button></li>` : '') : '<li class="history-empty">어떤 주제를 살펴봤는지 여기에 기록됩니다.</li>';
  $('moreHistory')?.addEventListener('click',()=>{allHistory=true;renderState();});
}

function recapView(recap) {
  if(!recap)return '';
  const list=items=>items.map(s=>`<li>${esc(s)}</li>`).join('');
  return `<section class="wrapup-summary"><h3>${esc(recap.title)}</h3><div><b>살펴본 연구 문제</b><p>${esc(recap.research_problem)}</p></div>
    ${recap.aspects?.length?`<div><b>검토한 관점·범위</b><ul>${list(recap.aspects.map(a=>a.text))}</ul></div>`:''}
    ${recap.reviewed_directions?.length?`<div><b>선택·검토한 방향</b><ul>${list(recap.reviewed_directions.map(d=>`${d.title} · ${d.action}`))}</ul></div>`:''}
    ${recap.remaining_questions?.length?`<div><b>아직 확인할 점</b><ul>${list(recap.remaining_questions)}</ul></div>`:''}
    <div><b>후속 검토</b><ul>${list(recap.next_checks||[])}</ul></div><p class="section-note">${esc(recap.note)}</p></section>`;
}

function renderMessages() {
  finishSendMotion();
  const acknowledged=new Set(snapshot.messages.filter(m=>m.role==='user').map(m=>m.request_id||m.content.request_id));
  const local=[...localTurns.values()].filter(t=>!acknowledged.has(t.payload.request_id));
  $('welcome').hidden=snapshot.messages.length>0||local.length>0;
  $('messages').innerHTML=snapshot.messages.map((m,messageIndex)=>{
    const c=m.content;
    if (m.role==='user') return `<article class="message user" data-client-request="${esc(m.request_id||c.request_id||'')}"><div class="user-content">${esc(c.text)}</div></article>`;
    const selected=selections.get(String(m.id));
    const recs=c.recommendations||[];
    const earlier=BASELINE?null:snapshot.messages.slice(0,messageIndex).reverse().find(p=>p.role==='assistant'&&p.content.recommendations?.length);
    const help=c.assistance;
    const readiness=BASELINE?null:snapshot.estimate?.state?.readiness;
    const pendingIntent=snapshot.events.some(e=>e.id>(snapshot.estimate?.through_event_id??0)&&['user_input','topic_browse','topic_refine'].includes(e.type));
    const showWrapup=!BASELINE&&c.wrapup_offer&&!pendingIntent&&readiness?.status==='ready'&&c.wrapup_offer.signature===readiness.signature&&!readiness.confirmed_signatures?.includes(c.wrapup_offer.signature);
    const compactReply=c.response_format==='11.10-v3';
    const paperButtons=papers=>(papers||[]).map(p=>`<button class="support-paper" data-support-paper="${esc(p.id)}"><b>${esc(p.title)}</b><span>${esc(p.year||'')} · ${esc((p.authors||[]).slice(0,2).join(', '))}</span></button>`).join('');
    const helpBody=help?.details?`<details class="help-details" ${help.expanded?'open':''}><summary><span class="help-preview message-answer">${esc(help.body)}</span><span class="help-open-label">자세한 설명 보기</span><span class="help-close-label">간단히 보기</span></summary><div class="message-answer">${esc(help.details)}</div></details>`:`<div class="message-answer">${esc(help?.body)}</div>`;
    return `<article class="message assistant" data-message-id="${m.id}">
      <div class="message-meta"><span class="agent-mark" aria-hidden="true">R</span><b>탐색 조력자</b><span>${timeOf(m.created_at)}</span>${!BASELINE?`<span>· ${esc(supportLabels[c.support_mode]||'다음 탐색 방향')}</span>`:''}${c.elapsed_seconds!=null?`<span class="response-duration">${duration(c.elapsed_seconds)} 소요</span>`:''}</div>
      ${c.mode==='preview'?'<div class="preview-answer-label">동작 확인용 예시 · Gemini 미연결</div>':''}
      <div class="assistant-bubble">
      ${!BASELINE?`<p class="message-state" data-difficulty="${['expressed','possible','unconfirmed'].includes(c.difficulty_status)?c.difficulty_status:'unknown'}">${esc(c.state_summary)}</p>`:''}
      ${help ? (help.body||help.papers?.length?`<div class="message-help">${help.title&&!compactReply?`<h3>${esc(help.title)}</h3>`:''}${helpBody}${paperButtons(help.papers)}</div>`:'') : `<div class="message-answer">${esc(c.answer)}</div>${c.support?.title||c.support?.body?`<div class="support-block"><h3>${esc(c.support.title)}</h3><p>${esc(c.support.body)}</p>${paperButtons(c.support.papers)}</div>`:''}`}
      ${!BASELINE?recapView(c.recap):''}
      ${!BASELINE&&c.next_step?`<div class="next-action${compactReply?' compact':''}" aria-label="다음 탐색 제안">${compactReply?'':'<h3>다음 탐색 제안</h3>'}<p>${esc(c.next_step)}</p>${c.next_step_reason&&!compactReply?`<p class="next-action-reason">${esc(c.next_step_reason)}</p>`:''}</div>`:''}
      ${c.retrieval_error?`<p class="retrieval-error">${esc(c.retrieval_error)}</p>`:''}
      ${!BASELINE&&!recs.length&&earlier&&c.recommendation_action==='preserve'?`<button class="text-button previous-candidates" data-previous-candidates="${earlier.id}">기존 탐색 후보 보기 ↑</button>`:''}
      </div>
      ${showWrapup?`<div class="wrapup-offer"><p>${esc(c.wrapup_offer.message)}</p><button class="button" data-wrapup="${esc(c.wrapup_offer.signature)}" ${busy?'disabled':''}>탐색 정리하기</button></div>`:''}
      ${recs.length?`<div class="recommendations"><div class="rec-heading">다음으로 살펴볼 주제<span class="rec-count">${recs.length}개 후보</span></div>
      ${recs.map((r,i)=>`<div class="recommendation-row${selected===r.id?' selected':''}"><button class="recommendation" data-rec="${esc(r.id)}" data-message="${m.id}" aria-pressed="${selected===r.id}"><span class="rec-number">${String(i+1).padStart(2,'0')}</span><span><span class="rec-title">${esc(r.title)}</span><span class="rec-description">${r.relation_label?`<span class="rec-relation">${esc(r.relation_label)}</span>`:''}${esc(r.description)}</span></span><span class="rec-indicator" aria-hidden="true"></span></button>${r.search_query?`<div class="rec-search"><div><span class="rec-search-label">검색 키워드</span><span class="rec-query" lang="en">${esc(r.search_query)}</span></div><button class="rec-search-button" data-search-rec="${esc(r.id)}" ${searching?'disabled':''} aria-label="${esc(r.title)} 관련 논문 검색">논문 검색 <span aria-hidden="true">↗</span></button></div>`:''}</div>`).join('')}
      <div class="rec-actions"><span class="selection-hint">${selected?'선택한 주제로 이어갈 수 있어요':'후보를 골라 이어가세요'}</span><button class="button" data-action="browse" data-message="${m.id}" ${!selected||busy?'disabled':''}>둘러보기</button><button class="button primary" data-action="refine" data-message="${m.id}" ${!selected||busy?'disabled':''}>구체화하기</button></div></div>`:''}</article>`;
  }).join('') + local.map(t=>`<article class="message user local-message" data-client-request="${esc(t.payload.request_id)}"><div class="user-content">${esc(t.display)}</div>${t.failed?`<div class="local-message-status">전송 확인 필요 <button class="local-retry" data-retry-local="${esc(t.payload.request_id)}" ${busy?'disabled':''}>다시 보내기</button></div>`:''}</article>`).join('');
  document.querySelectorAll('[data-rec]').forEach(b=>b.addEventListener('click',()=>selectRecommendation(b.dataset.message,b.dataset.rec)));
  document.querySelectorAll('[data-action]').forEach(b=>b.addEventListener('click',()=>sendTurn({action:b.dataset.action,recommendation_id:selections.get(b.dataset.message)})));
  document.querySelectorAll('[data-wrapup]').forEach(b=>b.addEventListener('click',()=>sendTurn({action:'wrapup',readiness_signature:b.dataset.wrapup})));
  document.querySelectorAll('[data-support-paper]').forEach(b=>b.addEventListener('click',()=>showSupportPaper(b.dataset.supportPaper)));
  document.querySelectorAll('[data-search-rec]').forEach(b=>b.addEventListener('click',()=>searchRecommendation(b.dataset.searchRec)));
  document.querySelectorAll('[data-retry-local]').forEach(b=>b.addEventListener('click',()=>sendTurn(localTurns.get(b.dataset.retryLocal)?.payload,true)));
  document.querySelectorAll('[data-previous-candidates]').forEach(b=>b.addEventListener('click',()=>document.querySelector(`[data-message-id="${b.dataset.previousCandidates}"] .recommendations`)?.scrollIntoView({behavior:reduceMotion()?'instant':'smooth',block:'center'})));
}

function logAction(type, detail) {
  const payload={type,...detail,client_time:new Date().toISOString(),client_id:crypto.randomUUID()};
  const task=eventQueue.then(async()=>{
    const r=await api('/api/events',payload);renderSnapshot(r.snapshot,false);return r;
  });
  // Keep later observations deliverable even if an earlier network request failed.
  eventQueue=task.catch(e=>{toast(e.message);});
  return task;
}

function selectRecommendation(messageId, recommendationId) {
  if (busy) return;
  selections.set(String(messageId),recommendationId);renderMessages();
  logAction('topic_select',{recommendation_id:recommendationId}).catch(()=>{});
}

async function sendTurn(input, retry=false) {
  if (busy) return;
  if(!snapshot){toast('탐색 세션을 불러오고 있어요. 잠시 후 다시 보내 주세요.');return;}
  const payload=retry ? (input||pendingTurn) : {...input,request_id:crypto.randomUUID()};
  if (!payload || (payload.action==='message'&&!payload.text?.trim())) return;
  const origin=payload.action==='message'&&!retry ? sendOrigin() : null;
  const rec=snapshot.messages.flatMap(m=>m.content.recommendations||[]).find(r=>r.id===payload.recommendation_id);
  const display=payload.action==='message' ? payload.text : payload.action==='wrapup' ? '이번 탐색 정리하기' : `${rec?.title||'선택한 주제'} · ${payload.action==='refine'?'구체화하기':'둘러보기'}`;
  localTurns.set(payload.request_id,{payload,display,failed:false});
  pendingTurn=payload; $('conversationError').hidden=true; setBusy(true);renderMessages();
  if(payload.action==='message'&&!retry){$('chatInput').value='';resizeInput();}
  startThinking(payload.request_id);scrollConversation(true);animateSendBubble(payload.request_id,origin);
  await eventQueue;
  try {
    const r=await api('/api/turn',payload);renderSnapshot(r.snapshot);pendingTurn=null;
    resizeInput();scrollConversation();
  } catch(e) {
    const local=localTurns.get(payload.request_id);if(local)local.failed=true;
    renderMessages();
    $('conversationError').innerHTML=`${esc(e.message)} <button id="retryTurn">같은 입력 다시 시도</button>`;
    $('conversationError').hidden=false;
    $('retryTurn').addEventListener('click',()=>sendTurn(null,true));
  } finally {stopThinking();setBusy(false);renderMessages();$('chatInput').focus();}
}

function emptyPapers(saved=false) {
  return `<div class="paper-empty"><svg viewBox="0 0 40 40" aria-hidden="true"><path d="M11 6h12l7 7v21H11zM23 6v8h7M16 20h9M16 25h9"/></svg><h2>${saved?'아직 담아 둔 논문이 없어요':'주제를 문헌에서 살펴보기'}</h2><p>${saved?'다시 보고 싶은 논문의<br>‘관심 논문에 담기’를 눌러 보세요.':'탐색 중 궁금해진 내용을 검색하고,<br>다시 보고 싶은 논문을 담아 두세요.'}</p></div>`;
}
function currentPapers() { return showingSaved ? snapshot.saved_papers : paperResult?.papers||[]; }
function renderPapers() {
  $('resultsTab').setAttribute('aria-selected',String(!showingSaved));
  $('savedTab').setAttribute('aria-selected',String(showingSaved));
  $('searchControls').hidden=showingSaved;
  const list=currentPapers(), savedIds=new Set(snapshot.saved_papers.map(p=>p.id));
  if (!list.length) { $('paperList').innerHTML=paperResult&&!showingSaved ? '<div class="paper-empty"><h2>검색 결과가 없어요</h2><p>다른 표현을 쓰거나 연도 조건을 줄여 보세요.</p></div>' : emptyPapers(showingSaved);return; }
  $('paperList').innerHTML=list.map((p,i)=>{
    const isExpanded=expanded.has(p.id), long=p.abstract.length>250, saved=savedIds.has(p.id);
    const abs=isExpanded?p.abstract:p.abstract.slice(0,250)+(long?'…':'');
    return `<article class="paper-item" data-paper="${esc(p.id)}"><div class="paper-meta"><span class="year">${esc(p.year||'연도 미상')}</span><span>·</span><span class="source">${esc(p.source||'출처 정보 없음')}</span></div><h2 class="paper-title">${esc(p.title)}</h2><p class="paper-authors">${esc(p.authors.slice(0,4).join(', '))}${p.authors.length>4?' 외':''}</p><p class="paper-abstract">${esc(abs||'제공된 초록이 없습니다.')}</p>${long?`<button class="abstract-toggle" data-abstract="${esc(p.id)}" data-rank="${i+1}" aria-expanded="${isExpanded}">${isExpanded?'초록 접기':'초록 더 보기'}</button>`:''}<div class="paper-actions"><button class="paper-save${saved?' saved':''}" data-save="${esc(p.id)}" data-rank="${i+1}" aria-pressed="${saved}"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5h8v11l-4-3-4 3z"/></svg>${saved?'관심 논문에 담김':'관심 논문에 담기'}</button>${p.url?`<a class="original-link" href="${esc(p.url)}" target="_blank" rel="noopener noreferrer" data-original="${esc(p.id)}" data-rank="${i+1}">${esc(p.link_label||'원문 페이지')} ↗</a>`:''}</div></article>`;
  }).join('');
  if (!showingSaved && paperResult) {
    const max=Math.min(50,Math.ceil(paperResult.total/paperResult.per_page));
    if (max>1) $('paperList').insertAdjacentHTML('beforeend',`<div class="paper-pagination"><button data-page="${searchPage-1}" ${searchPage<=1?'disabled':''}>← 이전</button><span>${searchPage} / ${max}</span><button data-page="${searchPage+1}" ${searchPage>=max?'disabled':''}>다음 →</button></div>`);
  }
  document.querySelectorAll('[data-abstract]').forEach(b=>b.addEventListener('click',()=>toggleAbstract(b.dataset.abstract,Number(b.dataset.rank))));
  document.querySelectorAll('[data-save]').forEach(b=>b.addEventListener('click',()=>savePaper(b.dataset.save,Number(b.dataset.rank))));
  document.querySelectorAll('[data-original]').forEach(a=>a.addEventListener('click',()=>{logPaper('paper_open',a.dataset.original,Number(a.dataset.rank)).catch(()=>{});}));
  document.querySelectorAll('[data-page]').forEach(b=>b.addEventListener('click',()=>searchPapers(Number(b.dataset.page))));
}
function logPaper(type,id,rank) {return logAction(type,{paper_id:id,rank,page:searchPage,query:paperResult?.query||'',source:showingSaved?'saved_collection':'search_panel'});}
function toggleAbstract(id,rank) {
  const opened=expanded.has(id);opened?expanded.delete(id):expanded.add(id);renderPapers();
  logPaper(opened?'abstract_collapse':'abstract_expand',id,rank).catch(()=>{});
}
async function savePaper(id,rank) {
  const saved=snapshot.saved_papers.some(p=>p.id===id);
  document.querySelectorAll('[data-save]').forEach(b=>b.disabled=true);
  try {await logPaper(saved?'paper_unsave':'paper_save',id,rank);renderPapers();toast(saved?'관심 논문에서 뺐어요':'관심 논문에 담았어요');}
  catch(_) {renderPapers();}
}
async function searchPapers(page=1) {
  if (searching) return;
  const query=$('paperQuery').value.trim(); if (!query) {$('paperQuery').focus();return;}
  if(!$('yearFilterForm').checkValidity()){$('yearFilterDetails').open=true;$('yearFilterForm').reportValidity();return;}
  const from=$('paperYearFrom').value,to=$('paperYearTo').value;
  if(from&&to&&Number(from)>Number(to)){$('yearFilterDetails').open=true;$('yearError').textContent='시작 연도는 종료 연도보다 늦을 수 없어요.';$('yearError').hidden=false;return;}
  $('yearError').hidden=true;
  const origin=searchOrigin?.query===query ? searchOrigin : {source:'manual_panel',recommendation_id:''};
  searching=true;$('newSession').disabled=true;showingSaved=false;searchPage=page;
  document.querySelectorAll('#searchControls input,#searchControls button,#searchControls select,[data-search-rec]').forEach(b=>b.disabled=true);
  $('resultsTab').setAttribute('aria-selected','true');$('savedTab').setAttribute('aria-selected','false');$('searchControls').hidden=false;
  $('paperList').innerHTML='<div class="paper-loading" role="status"><span class="spinner"></span>문헌을 찾고 있어요</div>';
  try {
    const r=await api('/api/papers/search',{query,page,sort:$('paperSort').value,year_from:from,year_to:to,source:origin.source,recommendation_id:origin.recommendation_id});
    paperResult=r.result;renderSnapshot(r.snapshot,false);$('resultCount').textContent=`${r.result.total.toLocaleString()}건`;updateYearSummary(from,to);renderPapers();$('paperList').scrollTop=0;
  } catch(e) {$('paperList').innerHTML=`<div class="paper-error">${esc(e.message)}<br><button id="retrySearch">다시 검색</button></div>`;$('retrySearch').addEventListener('click',()=>searchPapers(page));}
  finally {searching=false;$('newSession').disabled=busy;document.querySelectorAll('#searchControls input,#searchControls button,#searchControls select,[data-search-rec]').forEach(b=>b.disabled=false);}
}
function updateYearSummary(from='',to='') {
  $('yearFilterSummary').textContent=from&&to ? (String(from)===String(to)?`${from}년`:`${from}–${to}년`) : from?`${from}년 이후`:to?`${to}년까지`:'전체 기간';
}
function searchRecommendation(id) {
  if(searching)return;
  const rec=snapshot.messages.flatMap(m=>m.content.recommendations||[]).find(r=>r.id===id);
  if(!rec?.search_query)return;
  searchOrigin={source:'direction_keyword',recommendation_id:id,query:rec.search_query};
  $('paperQuery').value=rec.search_query;setPanelOpen('papers',true);
  $('searchOrigin').textContent=`추천 주제에서 검색 · ${rec.title}`;$('searchOrigin').hidden=false;
  setMobile('papers');searchPapers();
}
function showSupportPaper(id) {
  const papers=snapshot.messages.flatMap(m=>m.content.support?.papers||[]);
  const p=papers.find(p=>p.id===id);if(!p)return;
  paperResult={papers:[p],query:'',total:1,page:1,per_page:10};showingSaved=false;searchPage=1;
  setPanelOpen('papers',true);setMobile('papers');renderPapers();
}
function syncPanelControls() {
  const papersOpen=!$('workspace').classList.contains('papers-collapsed');
  const stateOpen=!$('workspace').classList.contains('state-collapsed');
  const paperLabel=`논문 검색 패널 ${papersOpen?'접기':'열기'}`;
  $('togglePapers').setAttribute('aria-expanded',String(papersOpen));
  $('togglePapers').setAttribute('aria-label',paperLabel);$('togglePapers').title=paperLabel;
  if($('expandState')){
    $('expandState').hidden=stateOpen;
    $('expandState').setAttribute('aria-expanded',String(stateOpen));
  }
}
function setPanelOpen(name,open) {
  if(BASELINE&&name==='state')return;
  $('workspace').classList.toggle(`${name}-collapsed`,!open);syncPanelControls();
}
function setMobile(name) {
  if(BASELINE&&name==='state')return;
  if(name==='papers'||name==='state')setPanelOpen(name,true);
  $('workspace').dataset.mobile=name;
  document.querySelectorAll('[data-mobile]').forEach(b=>{if(b.tagName==='BUTTON')b.classList.toggle('active',b.dataset.mobile===name);});
}
function resizeInput() {const input=$('chatInput');input.style.height='auto';input.style.height=Math.min(140,Math.max(43,input.scrollHeight))+'px';}
function settings() {$('settingsDialog').showModal();}

$('chatForm').addEventListener('submit',e=>{e.preventDefault();sendTurn({action:'message',text:$('chatInput').value.trim()});});
$('chatInput').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('chatForm').requestSubmit();}});
$('chatInput').addEventListener('input',resizeInput);
document.querySelectorAll('[data-starter]').forEach(b=>b.addEventListener('click',()=>{$('chatInput').value=b.dataset.starter;resizeInput();$('chatInput').focus();}));
$('paperSearchForm').addEventListener('submit',e=>{e.preventDefault();searchPapers();});
$('paperSort').addEventListener('change',()=>{if($('paperQuery').value.trim())searchPapers();});
$('yearFilterForm').addEventListener('submit',e=>{e.preventDefault();searchPapers();});
$('resetYears').addEventListener('click',()=>{$('paperYearFrom').value='';$('paperYearTo').value='';$('yearError').hidden=true;if($('paperQuery').value.trim())searchPapers();else updateYearSummary();});
$('paperQuery').addEventListener('input',()=>{searchOrigin=null;$('searchOrigin').hidden=true;});
['paperYearFrom','paperYearTo'].forEach(id=>$(id).max=String(new Date().getFullYear()));
$('savedTab').addEventListener('click',()=>{showingSaved=true;renderPapers();});
$('resultsTab').addEventListener('click',()=>{showingSaved=false;renderPapers();});
$('collapseState')?.addEventListener('click',()=>{setPanelOpen('state',false);setMobile('agent');});
$('expandState')?.addEventListener('click',()=>{setPanelOpen('state',true);setMobile('state');});
$('closePapers').addEventListener('click',()=>{setPanelOpen('papers',false);setMobile('agent');});
$('togglePapers').addEventListener('click',()=>{
  const open=$('workspace').classList.contains('papers-collapsed');
  setPanelOpen('papers',open);setMobile(open?'papers':'agent');
});
document.querySelectorAll('button[data-mobile]').forEach(b=>b.addEventListener('click',()=>setMobile(b.dataset.mobile)));
['settingsButton','previewSettings'].forEach(id=>$(id).addEventListener('click',settings));
['closeSettings','doneSettings'].forEach(id=>$(id).addEventListener('click',()=>$('settingsDialog').close()));
$('newSession').addEventListener('click',async()=>{
  if (busy||searching) return;await eventQueue;
  finishSendMotion();
  try {
    const s=await api('/api/session/new',{});selections.clear();expanded.clear();localTurns.clear();paperResult=null;showingSaved=false;pendingTurn=null;allHistory=false;
    searchOrigin=null;$('searchOrigin').hidden=true;$('paperYearFrom').value='';$('paperYearTo').value='';$('yearError').hidden=true;updateYearSummary();$('yearFilterDetails').open=false;
    $('conversationError').hidden=true;$('chatInput').value='';$('paperQuery').value='';$('resultCount').textContent='';
    renderSnapshot(s);renderPapers();resizeInput();setMobile('agent');toast('새 탐색을 시작했어요. 이전 기록은 시스템 폴더에 보관됩니다.');
  } catch(e) {toast(e.message);}
});

async function boot() {
  syncPanelControls();
  try {
    status=await api('/api/status');
    $('connectionStatus').textContent=status.mode==='gemini'?'Gemini 모드':'API 미연결 · 미리보기';
    $('connectionStatus').classList.toggle('live',status.mode==='gemini');
    $('previewNotice').hidden=status.mode==='gemini';
    $('settingsModel').textContent=status.model;
    if($('settingsMode'))$('settingsMode').textContent=status.mode==='gemini'?'Gemini 기반 추정':'규칙·템플릿 미리보기';
    const initial=await api('/api/workspace');
    paperResult=initial.last_search;searchPage=paperResult?.page||1;
    if(paperResult){$('paperQuery').value=paperResult.query;$('paperSort').value=paperResult.sort||'relevance';$('paperYearFrom').value=paperResult.year_from||'';$('paperYearTo').value=paperResult.year_to||'';updateYearSummary(paperResult.year_from,paperResult.year_to);$('resultCount').textContent=`${paperResult.total.toLocaleString()}건`;
      if(paperResult.source==='direction_keyword'){searchOrigin={source:paperResult.source,recommendation_id:paperResult.recommendation_id,query:paperResult.query};$('searchOrigin').textContent='추천 주제의 검색 키워드로 검색';$('searchOrigin').hidden=false;}}
    renderSnapshot(initial);renderPapers();
    if (snapshot.messages.length) scrollConversation();
  } catch(e) {$('connectionStatus').textContent='서버 연결 확인 필요';toast(e.message);}
}
boot();
