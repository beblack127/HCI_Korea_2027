const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'templates/index.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');
const seed = () => ({session:{id:'qa-session',title:'검증 세션'},events:[],messages:[],saved_papers:[],estimate:null,last_search:null});
const settle = () => new Promise(resolve => setImmediate(resolve));
async function harness() {
  const makeElement = () => {const classes=new Set(),attributes=new Map();return ({
    value:'',hidden:false,textContent:'',innerHTML:'',disabled:false,style:{},dataset:{},listeners:{},scrollHeight:44,
    classList:{add(...names){names.forEach(n=>classes.add(n));},remove(...names){names.forEach(n=>classes.delete(n));},
      contains(name){return classes.has(name);},toggle(name,force){const on=force===undefined?!classes.has(name):force;on?classes.add(name):classes.delete(name);return on;}},
    setAttribute(name,value){attributes.set(name,String(value));},getAttribute(name){return attributes.get(name);},focus(){},checkValidity(){return true;},reportValidity(){return true;},
    addEventListener(event,callback){this.listeners[event]=callback;}
  });};
  const elements = new Map([...html.matchAll(/id="([^"]+)"/g)].map(match=>[match[1],makeElement()]));
  elements.get('paperSort').value='relevance';
  const requests=[], intervals=new Map(), timeouts=new Map();
  let clock=0, nextId=0, turnResolver=null, turnRejecter=null, serverSnapshot=seed(),progressData={};
  const response = data => ({ok:true,json:async()=>data});
  const context=vm.createContext({
    document:{getElementById:id=>{
      if(!elements.has(id)&&[...elements.values()].some(el=>el.innerHTML.includes(`id="${id}"`)))elements.set(id,makeElement());
      return elements.get(id)||null;
    },querySelectorAll:()=>[]},
    Date,Map,Set,Intl,Promise,Number,String,Math,encodeURIComponent,
    crypto:{randomUUID:()=>`qa-${++nextId}`},performance:{now:()=>clock},
    requestAnimationFrame:fn=>fn(),setInterval:fn=>{const id=++nextId;intervals.set(id,fn);return id;},
    clearInterval:id=>intervals.delete(id),setTimeout:fn=>{const id=++nextId;timeouts.set(id,fn);return id;},clearTimeout:id=>timeouts.delete(id),
    fetch:async(url,options)=>{
      const body=options?.body ? JSON.parse(options.body) : null;requests.push({url,body});
      if(url==='/api/status')return response({mode:'preview',model:'qa'});
      if(url==='/api/workspace')return response(seed());
      if(url.startsWith('/api/turn/progress'))return response({phase:'generating',elapsed_seconds:65,...progressData});
      if(url==='/api/turn')return new Promise((resolve,reject)=>{
        turnRejecter=reject;
        turnResolver=(options={})=>{
          if(options.snapshot)serverSnapshot=options.snapshot;
          else if(!serverSnapshot.messages.some(m=>m.request_id===body.request_id))serverSnapshot.messages.push({
            id:++nextId,request_id:body.request_id,role:'user',created_at:new Date().toISOString(),content:{text:body.text||'선택한 주제'}});
          resolve({ok:!options.error,json:async()=>({snapshot:serverSnapshot,error:options.error})});
        };
      });
      if(url==='/api/papers/search')return response({snapshot:seed(),result:{papers:[],total:0,page:1,per_page:10,query:body.query}});
      throw new Error(`Unexpected request: ${url}`);
    }
  });
  vm.runInContext(script+'\n globalThis.qa={duration,startThinking,stopThinking,searchPapers,searchRecommendation,renderSnapshot,sendTurn,updateYearSummary,animateSendBubble,finishSendMotion};',context);
  await settle();requests.length=0;
  return {qa:context.qa,context,elements,requests,intervals,timeouts,advance(ms){clock=ms;for(const tick of intervals.values())tick();},
    finishTurn(options){turnResolver(options);},setProgress(value){progressData=value;},failTransport(){turnRejecter(new Error('test connection failure'));}};
}

test('elapsed counter formats seconds and minutes, stops with the request',async()=>{
  const h=await harness();h.qa.startThinking('timer-test');h.advance(65000);
  assert.equal(h.elements.get('thinkingTime').textContent,'1분 5초 동안 답변 생성 중');
  assert.equal(h.elements.get('thinkingNote').hidden,true);
  h.advance(91000);assert.equal(h.elements.get('thinkingNote').hidden,false);
  h.qa.stopThinking();assert.equal(h.intervals.size,0);assert.equal(h.timeouts.size,0);
});

test('search keyword button searches papers without selecting or refining a direction',async()=>{
  const h=await harness(), snap=seed();
  snap.messages=[{id:1,role:'assistant',created_at:new Date().toISOString(),content:{
    state_summary:'방향 탐색 중',answer:'다음 관점을 비교하세요.',recommendations:[{
      id:'candidate-1',title:'추천과 주도성',description:'선택의 변화',search_query:'recommendation explanation user agency'}]}}];
  h.qa.renderSnapshot(snap);
  assert.match(h.elements.get('messages').innerHTML,/recommendation explanation user agency/);
  assert.match(h.elements.get('messages').innerHTML,/data-search-rec="candidate-1"/);
  h.elements.get('paperYearFrom').value='2021';h.elements.get('paperYearTo').value='2023';
  h.qa.searchRecommendation('candidate-1');await settle();
  assert.equal(h.requests.length,1);assert.equal(h.requests[0].url,'/api/papers/search');
  assert.equal(h.requests[0].body.query,'recommendation explanation user agency');
  assert.equal(h.requests[0].body.source,'direction_keyword');
  assert.equal(h.requests[0].body.recommendation_id,'candidate-1');
  assert.equal(h.requests[0].body.year_to,'2023');
});

test('reversed custom year range is rejected before making an API request',async()=>{
  const h=await harness();h.elements.get('paperQuery').value='LLM';
  h.elements.get('paperYearFrom').value='2025';h.elements.get('paperYearTo').value='2021';
  await h.qa.searchPapers();assert.equal(h.requests.length,0);assert.equal(h.elements.get('yearError').hidden,false);
});

test('successful response removes timer and enables input again',async()=>{
  const h=await harness();const pending=h.qa.sendTurn({action:'message',text:'LLM 학술 탐색'});await settle();
  assert.equal(h.elements.get('thinking').hidden,false);assert.equal(h.elements.get('sendButton').disabled,true);
  h.advance(64000);assert.match(h.elements.get('thinkingTime').textContent,/1분 4초/);
  h.finishTurn();await pending;
  assert.equal(h.elements.get('thinking').hidden,true);assert.equal(h.elements.get('chatInput').disabled,false);
  assert.equal(h.intervals.size,0);assert.equal(h.timeouts.size,0);
});

test('closing the year controls preserves the applied range for the next search',async()=>{
  const h=await harness();h.elements.get('paperQuery').value='LLM';
  h.elements.get('paperYearFrom').value='2021';h.elements.get('paperYearTo').value='2023';
  h.elements.get('yearFilterDetails').open=false;
  await h.qa.searchPapers();
  assert.equal(h.requests[0].body.year_from,'2021');assert.equal(h.requests[0].body.year_to,'2023');
  assert.equal(h.elements.get('yearFilterSummary').textContent,'2021–2023년');
  assert.equal(h.elements.get('yearFilterDetails').open,false);
});

test('forming direction and an expressed formulation difficulty are both visible with the user evidence',async()=>{
  const h=await harness(),snap=seed();
  snap.events=[{id:7,type:'user_input',payload:{text:'여기서 연구 주제를 어떻게 구체화해야될지 모르겠어.'},created_at:new Date().toISOString()}];
  snap.estimate={state:{direction_stage:'forming',current_direction:'대화 맥락과 의도',summary:'방향을 구체화하면서 어려움을 표현했습니다.',
    difficulties:[{type:'formulation',status:'expressed',description:'연구 질문과 범위를 정하는 데 어려움을 표현함',evidence_ids:[7]}],difficulty_status:'expressed'},
    through_event_id:7,created_at:new Date().toISOString()};
  h.qa.renderSnapshot(snap);
  assert.equal(h.elements.get('stageLabel').textContent,'방향 형성');
  assert.equal(h.elements.get('difficultyBadge').hidden,false);
  assert.match(h.elements.get('difficultyBadge').textContent,/주제 구체화/);
  assert.equal(h.elements.get('difficultySection').hidden,false);
  assert.match(h.elements.get('difficultyList').innerHTML,/여기서 연구 주제를 어떻게 구체화해야될지 모르겠어/);
});

test('a missing difficulty estimate is displayed as unknown rather than no difficulty',async()=>{
  const h=await harness(),snap=seed();
  snap.estimate={state:{direction_stage:'exploring',difficulties:[],difficulty_status:'unknown'},through_event_id:0,created_at:new Date().toISOString()};
  h.qa.renderSnapshot(snap);
  assert.equal(h.elements.get('difficultyStatus').textContent,'판단 유보');
  assert.equal(h.elements.get('difficultyBadge').hidden,true);
  assert.match(h.elements.get('difficultyList').innerHTML,/판단하기 어려워요/);
});

test('question appears immediately and clears the composer before any server reply',async()=>{
  const h=await harness();h.elements.get('chatInput').value='질문 <script> 문장';
  const pending=h.qa.sendTurn({action:'message',text:'질문 <script> 문장'});
  assert.match(h.elements.get('messages').innerHTML,/message user local-message/);
  assert.match(h.elements.get('messages').innerHTML,/질문 &lt;script&gt; 문장/);
  assert.equal(h.elements.get('chatInput').value,'');
  assert.equal(h.elements.get('welcome').hidden,true);
  await settle();h.finishTurn();await pending;
  assert.equal((h.elements.get('messages').innerHTML.match(/질문 &lt;script&gt; 문장/g)||[]).length,1);
  assert.doesNotMatch(h.elements.get('messages').innerHTML,/local-message/);
});

test('a background snapshot acknowledges an in-flight bubble without duplicating it',async()=>{
  const h=await harness();const pending=h.qa.sendTurn({action:'message',text:'즉시 표시 확인'});await settle();
  const requestId=h.requests.find(r=>r.url==='/api/turn').body.request_id;
  const snap=seed();snap.messages=[{id:8,request_id:requestId,role:'user',created_at:new Date().toISOString(),content:{text:'즉시 표시 확인'}}];
  h.qa.renderSnapshot(snap,false);
  assert.equal((h.elements.get('messages').innerHTML.match(/즉시 표시 확인/g)||[]).length,1);
  assert.doesNotMatch(h.elements.get('messages').innerHTML,/local-message/);
  h.finishTurn({snapshot:snap});await pending;
  assert.equal((h.elements.get('messages').innerHTML.match(/즉시 표시 확인/g)||[]).length,1);
});

test('model error and retry reuse the existing question bubble',async()=>{
  const h=await harness();const first=h.qa.sendTurn({action:'message',text:'재시도할 질문'});await settle();
  const firstId=h.requests.find(r=>r.url==='/api/turn').body.request_id;
  h.finishTurn({error:'Gemini 일시 오류'});await first;
  assert.equal((h.elements.get('messages').innerHTML.match(/재시도할 질문/g)||[]).length,1);
  const retry=h.qa.sendTurn(null,true);await settle();
  const turnRequests=h.requests.filter(r=>r.url==='/api/turn');
  assert.equal(turnRequests[1].body.request_id,firstId);
  assert.equal((h.elements.get('messages').innerHTML.match(/재시도할 질문/g)||[]).length,1);
  h.finishTurn();await retry;
  assert.equal((h.elements.get('messages').innerHTML.match(/재시도할 질문/g)||[]).length,1);
});

test('network failure preserves the question and retry does not discard a new draft',async()=>{
  const h=await harness();const first=h.qa.sendTurn({action:'message',text:'보관할 질문'});await settle();
  h.failTransport();await first;
  assert.match(h.elements.get('messages').innerHTML,/보관할 질문/);
  assert.match(h.elements.get('messages').innerHTML,/전송 확인 필요/);
  h.elements.get('chatInput').value='다음 질문 초안';
  const retry=h.qa.sendTurn(null,true);await settle();h.finishTurn();await retry;
  assert.equal(h.elements.get('chatInput').value,'다음 질문 초안');
  assert.equal((h.elements.get('messages').innerHTML.match(/보관할 질문/g)||[]).length,1);
});

test('two intentional identical questions remain separate requests',async()=>{
  const h=await harness();const first=h.qa.sendTurn({action:'message',text:'같은 질문'});await settle();h.finishTurn();await first;
  const second=h.qa.sendTurn({action:'message',text:'같은 질문'});await settle();h.finishTurn();await second;
  assert.equal((h.elements.get('messages').innerHTML.match(/같은 질문/g)||[]).length,2);
  const requests=h.requests.filter(r=>r.url==='/api/turn');
  assert.notEqual(requests[0].body.request_id,requests[1].body.request_id);
});

test('each answer keeps its own difficulty tone rather than using the latest state',async()=>{
  const h=await harness(),snap=seed();
  snap.messages=[{id:1,role:'assistant',created_at:new Date().toISOString(),content:{answer:'이전 답변',state_summary:'이전에는 구체화가 어려움',
      difficulty_status:'expressed',recommendations:[]}},
    {id:2,role:'assistant',created_at:new Date().toISOString(),content:{answer:'다음 답변',state_summary:'새로운 관점 검토',
      difficulty_status:'unknown',recommendations:[]}}];
  h.qa.renderSnapshot(snap);
  assert.match(h.elements.get('messages').innerHTML,/data-difficulty="expressed">이전에는 구체화가 어려움/);
  assert.match(h.elements.get('messages').innerHTML,/data-difficulty="unknown">새로운 관점 검토/);
});

function motionFixture(h,{reduced=false,fail=false}={}) {
  const classes=new Set();let appended=0,removed=0,frames=null,complete,reject;
  const finished=new Promise((resolve,fail)=>{complete=resolve;reject=fail;});
  const animation={finished,cancel(){reject(new Error('cancelled'));}};
  const floating={classList:{add(){}},setAttribute(){},remove(){removed++;},animate(values){
    if(fail)throw new Error('unsupported animation');frames=values;return animation;
  }};
  const bubble={classList:{add:name=>classes.add(name),remove:name=>classes.delete(name)},animate(){},
    cloneNode:()=>floating,getBoundingClientRect:()=>({left:300,top:200,width:240,height:54})};
  h.elements.get('messages').querySelectorAll=()=>[{dataset:{clientRequest:'motion-test'},querySelector:()=>bubble}];
  h.elements.get('conversation').clientHeight=600;
  h.context.document.createElement=()=>({});
  h.context.document.body={appendChild(){appended++;}};
  h.context.matchMedia=()=>({matches:reduced});
  h.context.getComputedStyle=()=>({backgroundColor:'#eaf0e4'});
  return {classes,complete,get appended(){return appended;},get removed(){return removed;},get frames(){return frames;}};
}

test('send motion travels upward and always reveals the real bubble on completion',async()=>{
  const h=await harness(),motion=motionFixture(h);
  h.qa.animateSendBubble('motion-test',{left:100,top:700,width:500,height:70});
  assert.equal(motion.appended,1);assert.equal(motion.classes.has('motion-target'),true);
  assert.ok(parseFloat(motion.frames[0].top)>parseFloat(motion.frames.at(-1).top));
  motion.complete();await settle();
  assert.equal(motion.removed,1);assert.equal(motion.classes.has('motion-target'),false);
});

test('a new render cancels the flight and does not leave an invisible question',async()=>{
  const h=await harness(),motion=motionFixture(h);
  h.qa.animateSendBubble('motion-test',{left:100,top:700,width:500,height:70});
  h.qa.renderSnapshot(seed());await settle();
  assert.equal(motion.removed,1);assert.equal(motion.classes.has('motion-target'),false);
});

test('reduced motion and unsupported animations leave the question visible',async()=>{
  for(const options of [{reduced:true},{fail:true}]) {
    const h=await harness(),motion=motionFixture(h,options);
    h.qa.animateSendBubble('motion-test',{left:100,top:700,width:500,height:70});await settle();
    assert.equal(motion.classes.has('motion-target'),false);
    assert.equal(motion.appended,motion.removed);
  }
});

test('concept answer keeps old candidates, displays one explanation and the next action',async()=>{
  const h=await harness(),snap=seed();
  snap.messages=[{id:1,role:'assistant',created_at:new Date().toISOString(),content:{state_summary:'연구 방향 탐색',answer:'',
    recommendations:[{id:'old',title:'기존 연구 방향',description:'현재 후보',search_query:'user intent'}]}},
    {id:2,role:'assistant',created_at:new Date().toISOString(),content:{state_summary:'개념 확인 중',answer:'한 번만 제공할 설명',
      assistance:{title:'개념 설명',body:'한 번만 제공할 설명',papers:[]},recommendations:[],recommendation_action:'preserve',
      next_step:'기존 후보가 다루는 정보를 비교하세요.',next_step_reason:'연구 문제의 차이를 확인할 수 있어요.'}}];
  h.qa.renderSnapshot(snap);
  const output=h.elements.get('messages').innerHTML;
  assert.equal((output.match(/한 번만 제공할 설명/g)||[]).length,1);
  assert.equal((output.match(/class="rec-heading"/g)||[]).length,1);
  assert.match(output,/data-previous-candidates="1"/);
  assert.match(output,/다음 탐색 제안/);
  assert.match(output,/기존 후보가 다루는 정보를 비교하세요/);
  assert.doesNotMatch(output,/0개 후보/);
});

test('first concept question has no empty candidate box or selection controls',async()=>{
  const h=await harness(),snap=seed();
  snap.messages=[{id:1,role:'assistant',created_at:new Date().toISOString(),content:{state_summary:'개념 확인',
    assistance:{title:'개념 설명',body:'짧은 설명',papers:[]},recommendations:[],recommendation_action:'preserve',next_step:'이 설명으로 관심을 살펴보세요.'}}];
  h.qa.renderSnapshot(snap);
  assert.doesNotMatch(h.elements.get('messages').innerHTML,/rec-heading|rec-actions|previous-candidates/);
  assert.match(h.elements.get('messages').innerHTML,/짧은 설명/);
  assert.match(h.elements.get('messages').innerHTML,/다음 탐색 제안/);
});

test('an explanation request is not shown as difficulty and unresolved history stays separate',async()=>{
  const h=await harness(),snap=seed();
  snap.estimate={state:{direction_stage:'exploring',support_request:'concept',difficulties:[],difficulty_status:'unknown',
    difficulty_history:[{type:'concept',topic:'이전 관심',lifecycle:'unconfirmed',support_provided:true}]},
    created_at:new Date().toISOString(),through_event_id:0};
  h.qa.renderSnapshot(snap);
  assert.equal(h.elements.get('difficultyBadge').hidden,true);
  assert.equal(h.elements.get('statePane').dataset.difficulty,'unknown');
  assert.match(h.elements.get('difficultyList').innerHTML,/해소되었는지는 아직 확인되지/);
  assert.match(h.elements.get('difficultyHistoryList').innerHTML,/후속 확인 전/);
  assert.doesNotMatch(h.elements.get('difficultyHistoryList').innerHTML,/해결 표현 확인/);
});

test('progress updates state before response completion without duplicating the user bubble',async()=>{
  const h=await harness();
  const pending=h.qa.sendTurn({action:'message',text:'개념이 이해가 안 돼'});await settle();
  const requestId=h.requests.find(r=>r.url==='/api/turn').body.request_id;
  h.setProgress({estimate:{request_id:requestId,state:{direction_stage:'exploring',summary:'개념 이해의 어려움을 표현했어요.',
    response_status:'generating',difficulty_status:'expressed',difficulties:[{type:'concept',status:'expressed',description:'직접 표현',
      evidence_ids:[99],evidence_quotes:[{event_id:99,quote:'개념이 이해가 안 돼'}]}]},through_event_id:99,created_at:new Date().toISOString()},
    evidence_events:[{id:99,type:'user_input',payload:{text:'개념이 이해가 안 돼'},created_at:new Date().toISOString()}]});
  const poll=[...h.timeouts.values()][0];await poll();
  assert.match(h.elements.get('stateUpdated').textContent,/상태 추정 완료, 답변 생성 중/);
  assert.equal(h.elements.get('statePane').dataset.difficulty,'expressed');
  assert.match(h.elements.get('difficultyList').innerHTML,/개념이 이해가 안 돼/);
  assert.equal((h.elements.get('messages').innerHTML.match(/개념이 이해가 안 돼/g)||[]).length,1);
  h.finishTurn();await pending;
});

test('compact chat omits detailed reasons while state panel shows changes questions and rationale',async()=>{
  const h=await harness(),snap=seed();
  snap.estimate={state:{direction_stage:'exploring',summary:'패널에서만 보는 상세 탐색 상태',chat_summary:'짧은 상태',difficulty_status:'unknown',difficulties:[],
    clarified_aspects:[{kind:'perspective',text:'대화 중 목표 변경'}],recent_change:'개념을 확인하는 질문이 추가됐어요.',
    open_questions:['어떤 상황의 목표 변경을 살펴볼지'],observed_directions:[{title:'선택한 검토 방향',action:'둘러보기'}],
    flow_summary:'후보를 둘러본 뒤 초록 펼치기 활동을 했어요.',next_step:'상황을 비교해 보세요.',next_step_reason:'상태 패널에서만 확인할 이유',
    recommendation_context:[{id:'r',title:'목표 변경',relation_label:'관점 더하기',rationale:'현재 관심과 연결한 후보 근거'}]},
    created_at:new Date().toISOString(),through_event_id:0};
  snap.messages=[{id:1,role:'assistant',created_at:new Date().toISOString(),content:{response_format:'11.10-v3',state_summary:'짧은 상태',
    assistance:{title:'대화에서 생략할 제목',body:'짧은 설명',papers:[]},next_step:'상황을 비교해 보세요.',next_step_reason:'상태 패널에서만 확인할 이유',
    recommendations:[{id:'r',title:'목표 변경',description:'실패 상황을 살펴봐요.',relation_label:'관점 더하기',search_query:'intent change'}]}}];
  h.qa.renderSnapshot(snap);
  const output=h.elements.get('messages').innerHTML;
  assert.match(output,/짧은 상태/);assert.match(output,/짧은 설명/);assert.match(output,/상황을 비교해 보세요/);
  assert.doesNotMatch(output,/상태 패널에서만 확인할 이유|대화에서 생략할 제목|패널에서만 보는 상세/);
  assert.equal(h.elements.get('stateSummary').textContent,'패널에서만 보는 상세 탐색 상태');
  assert.equal(h.elements.get('nextReason').textContent,'상태 패널에서만 확인할 이유');
  assert.match(h.elements.get('recentChange').textContent,/질문이 추가/);
  assert.match(h.elements.get('clarifiedAspects').innerHTML,/대화 중 목표 변경/);
  assert.match(h.elements.get('openQuestions').innerHTML,/어떤 상황/);
  assert.match(h.elements.get('observedDirections').innerHTML,/선택한 검토 방향/);
  assert.match(h.elements.get('recommendationReasons').innerHTML,/현재 관심과 연결한 후보 근거/);
  assert.match(output,/관점 더하기/);
});

test('selected candidate connection follows actual selection and ignores an older topic',async()=>{
  const h=await harness(),snap=seed();
  snap.messages=[{id:1,role:'assistant',created_at:new Date().toISOString(),content:{recommendations:[{
    id:'chosen',title:'현재 선택한 후보',rationale:'이 후보를 제안한 이유'}]}}];
  snap.events=[{id:2,type:'topic_select',payload:{recommendation_id:'chosen'},created_at:new Date().toISOString()}];
  snap.estimate={state:{direction_context_start_event_id:0,difficulties:[]},through_event_id:2,created_at:new Date().toISOString()};
  h.qa.renderSnapshot(snap);
  assert.equal(h.elements.get('selectedConnection').hidden,false);
  assert.match(h.elements.get('selectedConnection').innerHTML,/이 후보를 제안한 이유/);
  snap.estimate.state.direction_context_start_event_id=3;h.qa.renderSnapshot(snap);
  assert.equal(h.elements.get('selectedConnection').hidden,true);
});

test('complete explanation can be expanded without making the default response long',async()=>{
  const h=await harness(),snap=seed();
  snap.messages=[{id:1,role:'assistant',created_at:new Date().toISOString(),content:{response_format:'11.10-v3',state_summary:'개념 확인',
    assistance:{body:'기본 두 문장.',details:'기본 두 문장. 추가로 요청한 세부 설명.',expanded:false,papers:[]},
    next_step:'현재 개념을 살펴보세요.',recommendations:[]}}];
  h.qa.renderSnapshot(snap);
  assert.match(h.elements.get('messages').innerHTML,/<details class="help-details" >/);
  assert.match(h.elements.get('messages').innerHTML,/help-preview message-answer/);
  assert.match(h.elements.get('messages').innerHTML,/자세한 설명 보기/);
  snap.messages[0].content.assistance.expanded=true;h.qa.renderSnapshot(snap);
  assert.match(h.elements.get('messages').innerHTML,/<details class="help-details" open>/);
  assert.doesNotMatch(html,/<details[^>]*id="evidenceSection"[^>]*\sopen/);
});

test('ready session exposes one wrapup action and hides stale or confirmed offers',async()=>{
  const h=await harness(),snap=seed();
  snap.estimate={through_event_id:2,created_at:new Date().toISOString(),state:{readiness:{status:'ready',signature:'current',message:'문제와 범위를 정리할 수 있어요.',checks:[{label:'연구할 문제',met:true}]},difficulties:[]}};
  snap.messages=[{id:1,role:'assistant',created_at:new Date().toISOString(),content:{wrapup_offer:{signature:'current',message:'이번 탐색을 잠정 정리할 수 있어요.'},recommendations:[]}}];
  h.qa.renderSnapshot(snap);
  assert.equal((h.elements.get('messages').innerHTML.match(/data-wrapup=/g)||[]).length,1);
  assert.doesNotMatch(h.elements.get('messages').innerHTML,/차이 정리하기|계속 둘러보기/);
  assert.equal(h.elements.get('readinessSection').hidden,false);
  assert.match(h.elements.get('readinessChecks').innerHTML,/연구할 문제 · 기록 확인/);
  snap.events=[{id:3,type:'user_input',payload:{text:'다른 주제로 바꿀래'}}];h.qa.renderSnapshot(snap);
  assert.doesNotMatch(h.elements.get('messages').innerHTML,/data-wrapup=/);
  snap.events=[];snap.estimate.state.readiness.status='confirmed';h.qa.renderSnapshot(snap);
  assert.doesNotMatch(h.elements.get('messages').innerHTML,/data-wrapup=/);
  snap.estimate.state.readiness.status='ready';snap.estimate.state.readiness.confirmed_signatures=['current'];h.qa.renderSnapshot(snap);
  assert.doesNotMatch(h.elements.get('messages').innerHTML,/data-wrapup=/);
});

test('wrapup sends explicit signature and immediately displays the correct user bubble',async()=>{
  const h=await harness();
  const pending=h.qa.sendTurn({action:'wrapup',readiness_signature:'current'});await settle();
  assert.match(h.elements.get('messages').innerHTML,/이번 탐색 정리하기/);
  assert.doesNotMatch(h.elements.get('messages').innerHTML,/선택한 주제 · 둘러보기/);
  assert.equal(h.requests.find(r=>r.url==='/api/turn').body.readiness_signature,'current');
  h.finishTurn();await pending;
});

test('wrapup recap escapes content and offers no fresh candidates or old-candidate fallback',async()=>{
  const h=await harness(),snap=seed();
  snap.messages=[{id:1,role:'assistant',created_at:new Date().toISOString(),content:{recommendations:[{id:'r',title:'이전 방향'}]}},
    {id:2,role:'assistant',created_at:new Date().toISOString(),content:{response_format:'11.10-v3',recommendations:[],recommendation_action:'complete',
      recap:{title:'잠정 연구 방향',research_problem:'<script>unsafe</script>',aspects:[{text:'학술 탐색'}],reviewed_directions:[],remaining_questions:['비교할 문구'],next_checks:['문헌과 대조'],note:'탐색을 이어갈 수 있어요.'}}}];
  h.qa.renderSnapshot(snap);
  const last=h.elements.get('messages').innerHTML.split('data-message-id="2"')[1];
  assert.match(last,/&lt;script&gt;unsafe&lt;\/script&gt;/);
  assert.match(last,/아직 확인할 점/);
  assert.doesNotMatch(last,/class="recommendations"|data-previous-candidates/);
});

test('pending expressed difficulty remains visible without claiming current difficulty',async()=>{
  const h=await harness(),snap=seed();
  snap.events=[{id:1,type:'user_input',payload:{text:'임베딩 반영 원리를 모르겠어'}}];
  const pending={id:'d',type:'concept',status:'expressed',lifecycle:'unconfirmed',evidence_ids:[1],
    evidence_quotes:[{event_id:1,quote:'임베딩 반영 원리를 모르겠어'}]};
  snap.estimate={through_event_id:1,state:{difficulties:[],pending_difficulties:[pending],difficulty_history:[pending]}};
  snap.messages=[{id:1,role:'assistant',content:{difficulty_status:'unconfirmed',state_summary:'해소 여부는 아직 확인되지 않았어요.',recommendations:[]}}];
  h.qa.renderSnapshot(snap);
  assert.equal(h.elements.get('statePane').dataset.difficulty,'unconfirmed');
  assert.equal(h.elements.get('difficultyBadge').hidden,false);
  assert.match(h.elements.get('difficultyBadge').textContent,/후속 확인 전/);
  assert.match(h.elements.get('difficultyList').innerHTML,/임베딩 반영 원리/);
  assert.match(h.elements.get('messages').innerHTML,/data-difficulty="unconfirmed"/);
  assert.doesNotMatch(h.elements.get('difficultyList').innerHTML,/현재도 어렵/);
});

test('unspecified experience displays an acknowledged difficulty without forcing a type',async()=>{
  const h=await harness(),snap=seed();
  snap.estimate={through_event_id:1,state:{difficulties:[{id:'u',type:'unspecified',status:'expressed',
    description:'탐색에서 어려움을 표현했어요. 어떤 유형인지는 아직 판단하지 않았어요.',evidence_ids:[1]}]}};
  snap.events=[{id:1,type:'user_input',payload:{text:'계속 빙빙 도는 느낌이야'}}];
  h.qa.renderSnapshot(snap);
  assert.match(h.elements.get('difficultyBadge').textContent,/유형 판단 유보/);
  assert.match(h.elements.get('difficultyList').innerHTML,/빙빙 도는 느낌/);
  assert.equal(h.elements.get('statePane').dataset.difficulty,'expressed');
});

test('behavioral support and system repetition show evidence without extra choices',async()=>{
  const h=await harness(),snap=seed();
  snap.estimate={through_event_id:3,state:{difficulties:[{type:'concept',status:'possible',evidence_ids:[1,2,3],
    description:'설명 지원이 필요할 가능성이 있어요.',signals:[{observation:'다른 설명 방식을 요청했어요.'}]}],
    support_issues:[{observation:'같은 후보 목록이 제공됐어요. 시스템 추천의 반복으로 기록했어요.'}]}};
  snap.messages=[{id:1,role:'assistant',content:{difficulty_status:'possible',recommendations:[{id:'r',title:'현재 관심',description:'설명'}]}}];
  h.qa.renderSnapshot(snap);
  assert.match(h.elements.get('difficultyList').innerHTML,/다른 설명 방식/);
  assert.match(h.elements.get('supportIssueList').innerHTML,/시스템 추천의 반복/);
  const output=h.elements.get('messages').innerHTML;
  assert.match(output,/data-action="browse"/);assert.match(output,/data-action="refine"/);
  assert.doesNotMatch(output,/data-action="(?:compare|orient|help)"|차이 정리하기|계속 둘러보기/);
});
