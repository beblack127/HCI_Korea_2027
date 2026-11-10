import json
import tempfile
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

from app import create_app
from core.config import Settings
from core.llm import GeminiClient, ModelError, parse_json
from core.policy import select_support
from core.search import OpenAlex, abstract_from_index, normalize_work, normalize_years
from core.service import clean_response, conversation_context
from core.state import empty_state, preview_state, validate_state
from core.storage import Store

HEADERS = {"X-Workspace-Request": "1"}


class CriteriaTests(unittest.TestCase):
    def test_invented_and_system_evidence_are_rejected(self):
        events = [{"id": 1, "type": "user_input", "payload": {"text": "LLM"}},
                  {"id": 2, "type": "agent_response", "payload": {"answer": "설명"}}]
        raw = dict(empty_state(), evidence=[{"event_id": 99}, {"event_id": 2}],
                   difficulties=[{"type": "concept", "evidence_ids": [2, 99]}])
        state, warnings = validate_state(raw, events)
        self.assertEqual(state["evidence"], [])
        self.assertEqual(state["difficulties"], [])
        self.assertEqual(state["difficulty_status"], "unknown")
        self.assertIn("invalid_evidence_reference", warnings)

    def test_one_refine_or_save_does_not_establish_formation(self):
        events = [{"id": 1, "type": "topic_refine", "payload": {"title": "주제"}},
                  {"id": 2, "type": "paper_save", "payload": {"title": "논문"}}]
        state, _ = validate_state(dict(empty_state(), direction_stage="forming", research_problem="문제", problem_evidence_ids=[1,2]), events)
        self.assertEqual(state["direction_stage"], "unknown")

    def test_explicit_request_overrides_difficulty(self):
        state = dict(empty_state(), support_request="literature", support_request_evidence_ids=[7],
            difficulties=[{"type": "concept", "status": "possible", "description": "가능성", "evidence_ids": [3]}])
        self.assertEqual(select_support(state)["mode"], "literature")

    def test_browse_changes_support_breadth_not_state(self):
        state = dict(empty_state(), direction_stage="forming")
        self.assertEqual(select_support(state, "browse")["breadth"], "broad")
        self.assertEqual(state["direction_stage"], "forming")

    def test_difficulty_can_coexist_with_formation(self):
        content="추천 설명 방식이 선택 주도성에 미치는 영향을 연구하고 싶어. 그런데 개념을 이해하기 어려워."
        events=[{"id":1,"type":"user_input","payload":{"text":content}}]
        quote=[{"event_id":1,"quote":content}]
        state,_=validate_state(dict(empty_state(),direction_stage="forming",research_problem="추천 설명 방식과 선택 주도성의 영향",problem_evidence=quote,
            difficulties=[{"type":"concept","status":"expressed","description":"개념 이해의 어려움","evidence_ids":[1],"evidence_quotes":quote}]),events)
        self.assertEqual(state["direction_stage"], "forming")
        self.assertEqual(state["difficulty_status"], "expressed")

    def test_generated_paper_ids_must_exist(self):
        raw={"answer":"내용", "recommendations":[{"title":"주제"}], "support":{"paper_ids":["ghost","W1"]}}
        value=clean_response(raw,[{"id":"W1","title":"실제 논문"}])
        self.assertEqual([p["id"] for p in value["support"]["papers"]],["W1"])

    def test_absence_of_signals_is_not_clear_difficulty(self):
        state, warnings = validate_state(dict(empty_state(), difficulty_status="clear"), [])
        self.assertEqual(state["difficulty_status"], "unknown")
        self.assertIn("clear_without_user_observation", warnings)

    def test_old_request_does_not_override_new_browse_action(self):
        events=[{"id":1,"type":"user_input","payload":{"text":"논문 추천"}},
                {"id":2,"type":"topic_browse","payload":{"title":"새 방향"}}]
        state,_=validate_state(dict(empty_state(),support_request="literature",support_request_evidence_ids=[1]),events)
        self.assertEqual(state["support_request"],"directions")

    def test_formulation_difficulty_coexists_with_a_forming_direction(self):
        events=[{'id':1,'type':'user_input','payload':{'text':'설명 방식이 사용자의 선택 주도성에 미치는 영향을 다루고 싶어'}},
                {'id':2,'type':'user_input','payload':{'text':'이 방향을 어떻게 연구 주제로 구체화할지 모르겠어'}}]
        state,_=validate_state(dict(empty_state(),direction_stage='forming',research_problem='설명 방식과 선택 주도성',
            problem_evidence=[{'event_id':1,'quote':events[0]['payload']['text']}],
            difficulties=[{'type':'formulation','status':'expressed','description':'주제 구체화가 어려움','evidence_ids':[2],
                          'evidence_quotes':[{'event_id':2,'quote':events[1]['payload']['text']}]}],
            support_request='formulate',support_request_evidence_ids=[2]),events)
        self.assertEqual(state['direction_stage'],'forming')
        self.assertEqual(state['difficulties'][0]['type'],'formulation')
        self.assertEqual(state['difficulty_status'],'expressed')
        self.assertEqual(select_support(state)['mode'],'formulate')

    def test_formulation_preview_does_not_mislabel_the_request_as_concept_or_connection(self):
        events=[{'id':1,'type':'user_input','payload':{'text':'여기서 연구 주제를 어떻게 구체화해야될지 모르겠어.'}}]
        state=preview_state(events)
        self.assertEqual(state['support_request'],'formulate')
        self.assertEqual([d['type'] for d in state['difficulties']],['formulation'])

    def test_generated_difficulty_still_requires_real_user_evidence(self):
        state,warnings=validate_state(dict(empty_state(),difficulties=[{
            'type':'formulation','status':'expressed','description':'막힘','evidence_ids':[99]}]),[])
        self.assertEqual(state['difficulties'],[])
        self.assertIn('difficulty_without_observation',warnings)


class SearchTests(unittest.TestCase):
    def test_year_ranges_allow_single_year_and_either_open_end(self):
        self.assertEqual(normalize_years('2021', '2023'), (2021, 2023))
        self.assertEqual(normalize_years(2022, 2022), (2022, 2022))
        self.assertEqual(normalize_years(None, 2019), (None, 2019))
        self.assertEqual(normalize_years(2023, ''), (2023, None))
        self.assertEqual(normalize_years('', ''), (None, None))

    def test_invalid_years_are_not_silently_ignored(self):
        for start, end in [('2025', '2021'), ('2020.5', ''), ('abc', ''), (True, ''), ('', '1700'), ('', '9999')]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                normalize_years(start, end)

    @patch('core.search.requests.get')
    def test_distinct_year_ranges_use_distinct_cached_results(self, get):
        get.return_value=Mock(ok=True,status_code=200)
        get.return_value.json.return_value={'meta':{'count':0},'results':[]}
        with tempfile.TemporaryDirectory() as directory:
            engine=OpenAlex(Settings(data_dir=directory,api_key='',mode='preview'),Store(directory))
            a=engine.search('conversational search',year_from=2021,year_to=2023)
            self.assertIn('publication_year:2021-2023',get.call_args.kwargs['params']['filter'])
            self.assertFalse(a['cached'])
            self.assertTrue(engine.search('conversational search',year_from=2021,year_to=2023)['cached'])
            self.assertFalse(engine.search('conversational search',year_from=2021,year_to=2024)['cached'])
            self.assertEqual(get.call_count,2)

    def test_abstract_index_is_restored_in_position_order(self):
        self.assertEqual(abstract_from_index({"world":[1],"Hello":[0]}), "Hello world")

    def test_unsafe_source_url_is_not_exposed(self):
        p=normalize_work({"id":"https://openalex.org/W1","primary_location":{"landing_page_url":"javascript:alert(1)"}})
        self.assertEqual(p["url"], "https://openalex.org/W1")
        self.assertEqual(p["link_label"], "논문 상세")

    def test_invalid_json_is_not_used_as_response(self):
        with self.assertRaises(ModelError): parse_json("응답을 생성할 수 없습니다")


class GeminiTests(unittest.TestCase):
    def settings(self):
        return Settings(api_key="test-key-not-real",mode="auto",api_style="interactions",thinking_level="low")

    @patch('core.llm.requests.post')
    def test_native_api_uses_header_and_json_schema(self, post):
        post.return_value=Mock(ok=True,status_code=200)
        post.return_value.json.return_value={'status':'completed','steps':[
            {'type':'thought','summary':[{'type':'text','text':'not returned'}]},
            {'type':'model_output','content':[{'type':'text','text':'{"query":"academic exploration","reason":"test"}'}]}]}
        result=GeminiClient(self.settings()).generate('instruction',{'text':'test'},schema='query')
        args,kwargs=post.call_args
        self.assertEqual(args[0],'https://generativelanguage.googleapis.com/v1/interactions')
        self.assertNotIn('test-key-not-real',args[0])
        self.assertEqual(kwargs['headers']['x-goog-api-key'],'test-key-not-real')
        self.assertEqual(kwargs['json']['response_format']['mime_type'],'application/json')
        self.assertEqual(kwargs['json']['response_format']['schema']['required'],['query','reason'])
        self.assertFalse(kwargs['json']['store'])
        self.assertEqual(kwargs['json']['generation_config']['thinking_level'],'low')
        self.assertEqual(result['query'],'academic exploration')

    @patch('core.llm.requests.post')
    def test_quota_failure_is_actionable_and_has_no_secret(self, post):
        post.return_value=Mock(ok=False,status_code=429)
        with self.assertRaises(ModelError) as caught:
            GeminiClient(self.settings()).generate('instruction',{})
        self.assertIn('할당량',str(caught.exception))
        self.assertNotIn('test-key-not-real',str(caught.exception))

    @patch('core.llm.requests.post')
    def test_temporary_service_failure_is_not_presented_as_a_key_problem(self, post):
        post.return_value=Mock(ok=False,status_code=503)
        with self.assertRaises(ModelError) as caught:
            GeminiClient(self.settings()).generate('instruction',{})
        self.assertEqual(caught.exception.code,'upstream_unavailable')
        self.assertNotIn('API 키',str(caught.exception))

    @patch('core.llm.requests.post')
    def test_auth_failure_has_no_secret(self, post):
        post.return_value=Mock(ok=False,status_code=403)
        with self.assertRaises(ModelError) as caught:
            GeminiClient(self.settings()).generate('instruction',{})
        self.assertIn('API 키',str(caught.exception))
        self.assertNotIn('test-key-not-real',str(caught.exception))

    @patch('core.llm.requests.post')
    def test_incomplete_response_is_not_parsed_as_success(self, post):
        post.return_value=Mock(ok=True,status_code=200)
        post.return_value.json.return_value={'status':'incomplete','steps':[]}
        with self.assertRaises(ModelError):
            GeminiClient(self.settings()).generate('instruction',{})

    def test_settings_no_longer_reads_openai_credentials(self):
        with patch.dict('os.environ',{'OPENAI_API_KEY':'old-test-key','OPENAI_MODEL':'old-model',
                                     'GEMINI_API_KEY':'','GEMINI_MODEL':'gemini-3.7-flash','APP_MODE':'auto'}):
            config=Settings()
            self.assertFalse(config.live)
            self.assertEqual(config.model,'gemini-3.7-flash')

    @patch('core.llm.requests.post')
    def test_generate_content_reads_json_and_ignores_thoughts(self, post):
        post.return_value=Mock(ok=True,status_code=200)
        post.return_value.json.return_value={'candidates':[{'finishReason':'STOP','content':{'parts':[
            {'thought':True,'text':'do not return this'},
            {'text':'{"query":"academic exploration","reason":"test"}'}]}}]}
        settings=Settings(api_key='test-key-not-real',mode='auto',model='gemini-3.1-flash-lite',api_style='generate_content',thinking_level='minimal')
        result=GeminiClient(settings).generate('instruction',{'text':'test'},schema='query')
        args,kwargs=post.call_args
        self.assertTrue(args[0].endswith('/v1beta/models/gemini-3.1-flash-lite:generateContent'))
        self.assertEqual(kwargs['json']['generationConfig']['responseMimeType'],'application/json')
        self.assertEqual(kwargs['json']['generationConfig']['thinkingConfig']['thinkingLevel'],'minimal')
        self.assertEqual(result['query'],'academic exploration')

    @patch('core.llm.requests.post')
    def test_timeout_has_specific_error_code(self, post):
        import requests
        post.side_effect=requests.ReadTimeout('test timeout')
        with self.assertRaises(ModelError) as caught:
            GeminiClient(self.settings()).generate('instruction',{})
        self.assertEqual(caught.exception.code,'response_timeout')

    @patch('core.llm.requests.post')
    def test_truncated_native_response_is_rejected(self, post):
        post.return_value=Mock(ok=True,status_code=200)
        post.return_value.json.return_value={'candidates':[{'finishReason':'MAX_TOKENS','content':{'parts':[{'text':'{"query":'}]}}]}
        settings=Settings(api_key='test-key-not-real',mode='auto',api_style='generate_content')
        with self.assertRaises(ModelError) as caught:
            GeminiClient(settings).generate('instruction',{},schema='query')
        self.assertEqual(caught.exception.code,'incomplete_response')


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.settings=Settings(data_dir=self.temp.name,api_key="",mode="preview")
        self.app=create_app(self.settings)
        self.app.testing=True
        self.client=self.app.test_client()

    def tearDown(self): self.temp.cleanup()

    def post(self,path,value,client=None):
        return (client or self.client).post(path,json=value,headers=HEADERS)

    def turn(self,request_id="a",text="LLM을 활용한 연구 주제 탐색"):
        return self.post('/api/turn',dict(request_id=request_id,action="message",text=text))

    def test_session_isolation_between_browsers(self):
        self.turn()
        other=self.app.test_client()
        a=self.client.get('/api/workspace').get_json()
        b=other.get('/api/workspace').get_json()
        self.assertNotEqual(a["session"]["id"],b["session"]["id"])
        self.assertEqual(b["messages"],[])
        self.assertIsNone(b["estimate"])

    def test_turn_retry_is_idempotent(self):
        self.assertEqual(self.turn().status_code,200)
        again=self.turn().get_json()
        self.assertEqual(len(again["snapshot"]["messages"]),2)
        self.assertEqual(len([e for e in again["snapshot"]["events"] if e["type"]=="user_input"]),1)

    def test_incidental_selection_does_not_run_estimation(self):
        first=self.turn().get_json()
        rid=first["response"]["recommendations"][0]["id"]
        before=first["snapshot"]["estimate"]["created_at"]
        r=self.post('/api/events',{"type":"topic_select","recommendation_id":rid,"client_id":"select1"}).get_json()
        self.assertEqual(before,r["snapshot"]["estimate"]["created_at"])
        follow=self.post('/api/turn',{"request_id":"b","action":"refine","recommendation_id":rid}).get_json()
        self.assertEqual(follow["snapshot"]["estimate"]["state"]["direction_stage"],"exploring")
        self.assertTrue(any(e["type"]=="topic_select" for e in follow["snapshot"]["events"]))

    def test_paper_search_independent_from_model(self):
        workspace=self.app.extensions["workspace"]
        workspace.llm.generate=Mock(side_effect=AssertionError("manual search must not call LLM"))
        workspace.search.search=Mock(return_value={"query":"LLM","papers":[{"id":"W1","title":"논문","abstract":"초록","authors":[]}],"total":1,"page":1,"per_page":10,"cached":False})
        r=self.post('/api/papers/search',{"query":"LLM"}).get_json()
        self.assertIsNone(r["snapshot"]["estimate"])
        workspace.llm.generate.assert_not_called()
        r=self.post('/api/events',{"type":"paper_save","paper_id":"W1","client_id":"save1"}).get_json()
        self.assertEqual(r["snapshot"]["saved_papers"][0]["id"],"W1")
        # Saving persists across app/store reload, not just in a browser Map.
        store=Store(self.temp.name)
        self.assertEqual(len(store.saved(r["snapshot"]["session"]["id"])),1)

    def test_no_other_sessions_paper_can_be_saved(self):
        self.assertEqual(self.post('/api/events',{"type":"paper_save","paper_id":"not-in-session"}).status_code,400)

    def test_bad_cross_origin_api_request_is_rejected(self):
        self.assertEqual(self.client.post('/api/session/new',json={}).status_code,400)
        self.assertEqual(self.client.post('/api/session/new',json={},headers=dict(HEADERS,Origin="https://other.test")).status_code,403)

    def test_export_has_estimates_separate_from_raw_observations(self):
        self.turn()
        r=self.client.get('/api/session/export')
        data=json.loads(r.data)
        self.assertIn('attachment',r.headers['Content-Disposition'])
        self.assertEqual(len(data['estimate_history']),1)
        self.assertEqual(data['estimate_history'][0]['provenance']['mode'],'preview')
        self.assertTrue(data['events'])

    def test_model_failure_does_not_silently_become_preview(self):
        self.app.extensions['workspace'].settings.live=True
        self.app.extensions['workspace'].llm.generate=Mock(side_effect=ModelError('연결 실패'))
        r=self.turn()
        self.assertEqual(r.status_code,502)
        self.assertEqual(len(r.get_json()['snapshot']['messages']),1)
        self.assertIsNone(r.get_json()['snapshot']['estimate'])

    def test_live_pipeline_uses_validated_state_and_policy(self):
        workspace=self.app.extensions['workspace']
        workspace.settings.live=True
        calls=[]
        def generate(instruction,context,schema="response"):
            calls.append(schema)
            if schema=="estimate":
                event=next(e for e in reversed(context['events']) if e['type']=='user_input')
                return dict(empty_state(),direction_stage='forming',current_direction='선택 주도성',research_problem='추천이 선택 주도성에 미치는 영향',
                    problem_evidence=[{'event_id':event['id'],'quote':event['payload']['text']}],summary='선택 주도성에 관한 연구 문제를 구체화하는 것으로 보입니다.',
                    evidence=[{'event_id':event['id'],'observation':event['payload']['text'],'interpretation':'연구 문제를 표현함'}])
            return {'answer':'현재 문제 안에서 관점을 비교해 보세요.',
                'recommendations':[{'title':'설명 방식 비교','description':'추천 설명의 차이','rationale':'현재 문제를 유지','search_query':'recommendation explanation agency'}],
                'support':{'title':'','body':'','paper_ids':[]},'next_step':'설명 방식을 비교해 보세요.','next_step_reason':'같은 문제 안에서 범위를 좁힐 수 있습니다.'}
        workspace.llm.generate=generate
        r=self.turn(text='추천이 선택 주도성에 미치는 영향을 연구하고 싶어').get_json()
        self.assertEqual(calls,['estimate','response'])
        self.assertEqual(r['response']['mode'],'gemini')
        self.assertEqual(r['snapshot']['estimate']['plan']['breadth'],'specific')
        self.assertEqual(r['response']['state_summary'],r['snapshot']['estimate']['state']['chat_summary'])
        self.assertIsNotNone(r['snapshot']['estimate']['provenance']['raw_estimate'])

    def test_search_state_is_restored_without_new_search(self):
        workspace=self.app.extensions['workspace']
        workspace.search.search=Mock(return_value={"query":"LLM","papers":[{"id":"W1","title":"논문","abstract":"초록","authors":[]}],"total":1,"page":1,"per_page":10,"cached":False})
        self.post('/api/papers/search',{'query':'LLM','sort':'latest'})
        restored=self.client.get('/api/workspace').get_json()['last_search']
        self.assertEqual(restored['query'],'LLM')
        self.assertEqual(restored['sort'],'latest')
        self.assertEqual(restored['papers'][0]['id'],'W1')

    def test_custom_years_and_recommendation_origin_survive_reload(self):
        first=self.turn().get_json()
        rec=first['response']['recommendations'][0]
        workspace=self.app.extensions['workspace']
        workspace.search.search=Mock(return_value={'query':rec['search_query'],'papers':[],
            'total':0,'page':1,'per_page':10,'cached':False})
        result=self.post('/api/papers/search',{'query':rec['search_query'],'source':'direction_keyword',
            'recommendation_id':rec['id'],'year_from':'2021','year_to':'2023'})
        self.assertEqual(result.status_code,200)
        workspace.search.search.assert_called_once_with(rec['search_query'],1,'relevance',2021,2023)
        restored=self.client.get('/api/workspace').get_json()['last_search']
        self.assertEqual((restored['year_from'],restored['year_to']),(2021,2023))
        self.assertEqual(restored['source'],'direction_keyword')
        self.assertEqual(restored['recommendation_id'],rec['id'])
        # Looking up a candidate's literature does not itself update the cognitive estimate.
        self.assertEqual(result.get_json()['snapshot']['estimate']['created_at'],first['snapshot']['estimate']['created_at'])

    def test_invalid_year_range_does_not_call_search_or_create_observation(self):
        workspace=self.app.extensions['workspace']
        workspace.search.search=Mock()
        r=self.post('/api/papers/search',{'query':'LLM','year_from':2024,'year_to':2021})
        self.assertEqual(r.status_code,400)
        workspace.search.search.assert_not_called()
        self.assertFalse(any(e['type']=='paper_search_submit' for e in self.client.get('/api/workspace').get_json()['events']))

    def test_cannot_attribute_a_search_to_another_sessions_candidate(self):
        rec=self.turn().get_json()['response']['recommendations'][0]
        other=self.app.test_client()
        result=self.post('/api/papers/search',{'query':rec['search_query'],'source':'direction_keyword',
            'recommendation_id':rec['id']},client=other)
        self.assertEqual(result.status_code,400)

    def test_progress_is_session_scoped_and_failure_is_visible(self):
        self.turn(request_id='qa-progress')
        self.assertEqual(self.client.get('/api/turn/progress?request_id=qa-progress').get_json()['phase'],'completed')
        other=self.app.test_client()
        self.assertEqual(other.get('/api/turn/progress?request_id=qa-progress').get_json()['phase'],'waiting')
        workspace=self.app.extensions['workspace']
        workspace.settings.live=True
        workspace.llm.generate=Mock(side_effect=ModelError('test failure'))
        self.turn(request_id='qa-failure')
        self.assertEqual(self.client.get('/api/turn/progress?request_id=qa-failure').get_json()['phase'],'error')

    def test_retry_elapsed_time_does_not_include_idle_time_after_failure(self):
        workspace=self.app.extensions['workspace']
        with patch('core.service.time.monotonic',return_value=10):
            workspace.set_progress('s','r','queued')
        with patch('core.service.time.monotonic',return_value=13):
            workspace.set_progress('s','r','error')
        with patch('core.service.time.monotonic',return_value=400):
            workspace.set_progress('s','r','queued')
            self.assertEqual(workspace.get_progress('s','r')['elapsed_seconds'],0)

    def test_live_context_retains_evidence_but_excludes_old_model_provenance(self):
        self.turn()
        workspace=self.app.extensions['workspace']
        workspace.settings.live=True
        contexts={}
        def generate(instruction,context,schema='response'):
            contexts[schema]=context
            if schema=='estimate':
                self.assertEqual(workspace.get_progress(context_sid,'compact')['phase'],'estimating')
                event=context['events'][-1]
                return dict(empty_state(),current_direction='추천과 주도성',summary='문제를 구체화하려 합니다.',
                    evidence=[{'event_id':event['id'],'observation':event['payload']['text'],'interpretation':'구체화 요청'}])
            self.assertEqual(workspace.get_progress(context_sid,'compact')['phase'],'generating')
            return {'answer':'설명','recommendations':[{'title':'추천 설명과 선택','search_query':'recommendation explanation user agency'}],
                'support':{},'next_step':'관점 비교','next_step_reason':'후보 차이 확인'}
        context_sid=self.client.get('/api/workspace').get_json()['session']['id']
        workspace.llm.generate=generate
        r=self.turn(request_id='compact',text='여기서 주제를 어떻게 구체화해야 할지 모르겠어').get_json()
        self.assertTrue(all(e['type']!='agent_response' for e in contexts['estimate']['events']))
        self.assertEqual(contexts['estimate']['events'][-1]['payload']['text'],'여기서 주제를 어떻게 구체화해야 할지 모르겠어')
        self.assertNotIn('events',contexts['response'])
        self.assertNotIn('prior_estimate',contexts['response'])
        self.assertEqual(contexts['response']['estimated_state']['chat_summary'],r['response']['state_summary'])
        self.assertIn('phase_seconds',r['snapshot']['estimate']['provenance'])
        self.assertTrue(any(e['type']=='agent_response' for e in r['snapshot']['events']))

    def test_long_paper_abstracts_are_not_repeated_in_conversation_context(self):
        compact=conversation_context([{'role':'assistant','content':{'answer':'개념 설명','support':{
            'body':'상세 설명','papers':[{'id':'W1','title':'논문','abstract':'X'*18000}]},
            'recommendations':[{'id':'r1','title':'다음 후보','search_query':'academic exploration'}]}}])
        self.assertEqual(compact[0]['content']['support']['papers'],[{'id':'W1','title':'논문'}])
        self.assertEqual(compact[0]['content']['recommendations'][0]['id'],'r1')

    def test_history_difficulty_uses_each_answers_estimate_without_rewriting_saved_messages(self):
        store=self.app.extensions['store']
        sid=store.create_session()
        legacy={'answer':'이전 답변','state_summary':'구체화가 어려움','recommendations':[]}
        store.add_message(sid,'old:assistant','assistant',legacy)
        store.put_estimate(sid,'old',1,dict(empty_state(),difficulty_status='expressed',difficulties=[{
            'type':'formulation','status':'expressed','description':'구체화 요청','evidence_ids':[1]}]),{}, {})
        store.add_message(sid,'new:assistant','assistant',dict(legacy,answer='새 답변'))
        store.put_estimate(sid,'new',2,empty_state(),{}, {})
        messages=store.messages(sid)
        self.assertEqual([m['request_id'] for m in messages],['old','new'])
        self.assertEqual(messages[0]['content']['difficulty_status'],'expressed')
        self.assertEqual(messages[0]['content']['difficulty_types'],['formulation'])
        self.assertEqual(messages[1]['content']['difficulty_status'],'unknown')
        with store.connect() as con:
            original=json.loads(con.execute('SELECT payload FROM messages WHERE session_id=? AND message_key=?',
                (sid,'old:assistant')).fetchone()[0])
        self.assertNotIn('difficulty_status',original)

    def test_message_request_ids_and_difficulty_metadata_are_session_scoped(self):
        first=self.turn(request_id='same-id',text='여기서 연구 주제를 어떻게 구체화해야될지 모르겠어.').get_json()
        other=self.app.test_client()
        second=self.post('/api/turn',{'request_id':'same-id','action':'message','text':'새로운 분야 탐색'},client=other).get_json()
        self.assertEqual(first['snapshot']['messages'][0]['request_id'],'same-id')
        self.assertEqual(second['snapshot']['messages'][0]['request_id'],'same-id')
        self.assertEqual(first['response']['difficulty_status'],'expressed')
        self.assertEqual(second['snapshot']['messages'][-1]['content']['difficulty_status'],'unknown')
        restored=self.client.get('/api/workspace').get_json()
        self.assertEqual(restored['messages'][-1]['content']['difficulty_types'],['formulation'])


if __name__ == '__main__': unittest.main()
