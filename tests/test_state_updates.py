"""Contrast cases for grounded state, optional candidates and failure persistence."""
import tempfile
import unittest
from unittest.mock import Mock
from app import create_app
from core.config import Settings
from core.llm import ModelError
from core.policy import select_support
from core.service import clean_response
from core.state import empty_state, validate_state, preview_state, behavior_signals

HEADERS={'X-Workspace-Request':'1'}
def event(i,value,kind='user_input',**payload):
    return {'id':i,'type':kind,'payload':dict({'text':value} if kind=='user_input' else {'title':value},**payload)}
def difficulty(i,value,kind='concept',status='expressed'):
    return {'type':kind,'status':status,'description':'모델의 해석','evidence_ids':[i],
            'evidence_quotes':[{'event_id':i,'quote':value}]}
def response(candidates=True):
    return {'assistance':{'title':'개념 설명','body':'여러 턴에 걸쳐 필요한 대화 정보를 갱신하는 과정이에요.','paper_ids':[]},
            'recommendations':[{'title':'추천과 선택 주도성','description':'설명 방식의 영향을 비교합니다.',
                'rationale':'현재 관심과 연결됩니다.','search_query':'recommendation explanation user agency'}] if candidates else [],
            'next_step':'기존 후보가 추적하려는 정보를 비교해 보세요.','next_step_reason':'연구할 문제의 차이를 확인할 수 있어요.'}

class GroundingTests(unittest.TestCase):
    def test_help_requests_do_not_become_difficulties_even_if_model_claims_them(self):
        for value,kind in [('대화 상태 추적이 뭐야?','concept'),('예시로 설명해줘','concept'),('A와 B를 비교해줘','selection'),
                           ('왜 이걸 추천했어?','connection'),('다른 주제는 없어?','selection')]:
            with self.subTest(value=value):
                state,warnings=validate_state(dict(empty_state(),difficulties=[difficulty(1,value,kind)],summary='매우 어려워함'),[event(1,value)])
                self.assertEqual(state['difficulties'],[])
                self.assertEqual(state['difficulty_status'],'unknown')
                self.assertNotIn('매우 어려워',state['summary'])
                self.assertIn('difficulty_without_supported_meaning',warnings)
    def test_each_explicit_difficulty_requires_grounded_quote(self):
        cases={'concept':'설명을 봐도 개념을 이해하기 어려워','connection':'이 후보가 내 관심과 어떻게 연결되는지 모르겠어',
               'selection':'A와 B 중 뭘 고를지 모르겠어','formulation':'이걸 연구 문제로 어떻게 구체화할지 모르겠어'}
        for kind,value in cases.items():
            with self.subTest(kind=kind):
                state,_=validate_state(dict(empty_state(),difficulties=[difficulty(1,value,kind)]),[event(1,value)])
                self.assertEqual(state['difficulties'][0]['type'],kind)
                self.assertEqual(state['difficulty_status'],'expressed')
                self.assertEqual(state['difficulties'][0]['evidence_quotes'][0]['quote'],value)
    def test_fabricated_quote_is_rejected_even_with_real_event(self):
        state,_=validate_state(dict(empty_state(),difficulties=[difficulty(1,'개념을 이해하기 어려워')]),[event(1,'LLM')])
        self.assertEqual(state['difficulties'],[])
    def test_cherry_picked_negation_is_rejected(self):
        state,_=validate_state(dict(empty_state(),difficulties=[difficulty(1,'이해가 안')]),[event(1,'이해가 안 되는 건 아니야. 예시가 궁금해')])
        self.assertEqual(state['difficulties'],[])
    def test_keywords_are_not_inflated_into_research_problems(self):
        state,_=validate_state(dict(empty_state(),direction_stage='forming',research_problem='LLM의 다중 턴 의도 변화가 검색 정확도에 미치는 영향',
            problem_evidence=[{'event_id':1,'quote':'LLM 사용자 의도'}]),[event(1,'LLM 사용자 의도')])
        self.assertEqual(state['direction_stage'],'unknown')
        self.assertEqual(state['research_problem'],'')
    def test_real_problem_and_unrelated_invention_are_distinguished(self):
        value='추천 설명 방식이 사용자의 선택 주도성에 미치는 영향을 연구하고 싶어'
        base=dict(empty_state(),direction_stage='forming',problem_evidence=[{'event_id':1,'quote':value}])
        supported,_=validate_state(dict(base,research_problem='추천 설명 방식과 선택 주도성의 영향'),[event(1,value)])
        invented,_=validate_state(dict(base,research_problem='해양 선박 충돌 예측 모델의 성능 차이'),[event(1,value)])
        self.assertEqual(supported['direction_stage'],'forming')
        self.assertEqual(invented['direction_stage'],'unknown')
    def test_observation_is_reconstructed_from_actual_log(self):
        raw=dict(empty_state(),evidence=[{'event_id':1,'observation':'원문을 완전히 이해하고 방향을 확정함','interpretation':'관심 가능성'}])
        state,_=validate_state(raw,[event(1,'실제 논문','paper_open')])
        self.assertEqual(state['evidence'][0]['observation'],'원문 링크 열기: “실제 논문”')
    def test_normal_browsing_alone_does_not_prove_difficulty(self):
        events=[event(i,'주제','topic_select',recommendation_id='A' if i%2 else 'B') for i in range(1,5)]
        events.append(event(5,'논문','paper_save'))
        self.assertTrue(behavior_signals(events))
        state,_=validate_state(dict(empty_state(),difficulties=[{'type':'selection','status':'expressed','description':'막힘',
            'evidence_ids':[1,2,3,4,5],'evidence_quotes':[]}]),events)
        self.assertEqual(state['difficulties'],[])
        self.assertEqual(behavior_signals([event(1,'논문','paper_save')]),[])
    def test_combined_behavior_and_ambiguous_context_only_allow_possible(self):
        events=[event(1,'아직 막막해')]+[event(i,'주제','topic_select',recommendation_id='A' if i%2 else 'B') for i in range(2,6)]
        raw=dict(empty_state(),difficulties=[dict(difficulty(1,'아직 막막해','selection','possible'),evidence_ids=[1,2,3,4,5])])
        state,_=validate_state(raw,events)
        self.assertEqual(state['difficulty_status'],'possible')
        self.assertIn('확정하지',state['difficulties'][0]['description'])

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.value='설명을 봐도 개념을 이해하기 어려워'
        self.first=event(1,self.value)
        self.previous=preview_state([self.first])
        self.key=self.previous['difficulty_history'][0]['id']
    def update(self,content):
        raw=dict(empty_state(),difficulty_status='clear',difficulty_updates=[{'difficulty_id':self.key,'outcome':'resolved',
            'evidence_quotes':[{'event_id':2,'quote':content}]}])
        return validate_state(raw,[self.first,event(2,content)],self.previous)[0]
    def test_support_or_thanks_does_not_resolve_difficulty(self):
        self.previous['difficulty_history'][0]['support_provided']=True
        state=self.update('고마워')
        self.assertEqual(state['difficulty_status'],'unknown')
        self.assertEqual(state['difficulty_history'][0]['lifecycle'],'unconfirmed')
        self.assertTrue(state['difficulty_history'][0]['support_provided'])
    def test_future_and_unresolved_statements_are_not_resolution(self):
        for content in ['이제 이해될까?','이제 이해가 될 것 같아','아직 이해가 안 돼']:
            self.assertEqual(self.update(content)['difficulty_history'][0]['lifecycle'],'unconfirmed')
    def test_old_evidence_is_not_fresh_confirmation(self):
        state,_=validate_state(dict(empty_state(),difficulties=[difficulty(1,self.value)]),[self.first,event(2,'논문','paper_save')],self.previous)
        self.assertEqual(state['difficulties'],[])
        self.assertEqual(state['difficulty_history'][0]['lifecycle'],'unconfirmed')
    def test_explicit_followup_resolves_matching_difficulty(self):
        state=self.update('이제 개념이 이해됐어')
        self.assertEqual(state['difficulty_history'][0]['lifecycle'],'resolved')
        self.assertEqual(state['difficulty_status'],'clear')
        self.assertEqual(state['difficulty_history'][0]['followup_evidence'][0]['event_id'],2)
    def test_changed_topic_is_out_of_scope_not_resolved(self):
        value='다른 주제로 바꿔서 로봇 상호작용을 살펴볼래'
        state,_=validate_state(dict(empty_state(),current_direction='로봇 상호작용',topic_relation='changed',topic_evidence_ids=[2]),
            [self.first,event(2,value)],self.previous)
        self.assertEqual(state['difficulty_history'][0]['lifecycle'],'out_of_scope')
        self.assertNotEqual(state['difficulty_status'],'clear')

class CandidatePolicyTests(unittest.TestCase):
    def test_every_support_mode_keeps_recommendations_available(self):
        for mode in ['concept','compare','connection','literature','directions','formulate']:
            plan=select_support(dict(empty_state(),support_request=mode),has_candidates=True)
            self.assertEqual(plan['recommendation_count'],3)
            self.assertEqual(plan['recommendation_action'],'generate')
        self.assertEqual(select_support(dict(empty_state(),support_request='concept'),initial=True)['recommendation_count'],3)
    def test_initial_keyword_new_direction_browse_and_refine_generate_candidates(self):
        for state,action,initial in [(empty_state(),'message',True),(dict(empty_state(),recommendation_intent='generate'),'message',False),
                                    (empty_state(),'browse',False),(empty_state(),'refine',False)]:
            self.assertEqual(select_support(state,action,initial=initial)['recommendation_count'],3)
    def test_mixed_concept_and_research_request_is_not_lost(self):
        value='대화 상태 추적이 뭔지 설명해주고 이걸로 어떤 연구를 할 수 있는지 추천해줘'
        state,_=validate_state(dict(empty_state(),support_request='concept',support_request_evidence_ids=[1]),[event(1,value)])
        plan=select_support(state)
        self.assertEqual(plan['mode'],'concept')
        self.assertEqual(plan['recommendation_count'],3)
    def test_concept_answer_combines_help_recommendations_and_next_step_without_duplicate_body(self):
        plan=select_support(dict(empty_state(),support_request='concept'),has_candidates=True)
        value=clean_response(response(),[],plan)
        self.assertTrue(value['recommendations'])
        self.assertEqual(plan['recommendation_focus'],'current_concept')
        self.assertEqual(value['support']['body'],'')
        self.assertTrue(value['next_step'])
        self.assertTrue(value['next_step_reason'])
    def test_concept_answer_cannot_silently_drop_all_keywords(self):
        plan=select_support(dict(empty_state(),support_request='concept'))
        with self.assertRaisesRegex(ModelError,'탐색 후보'):
            clean_response(response(False),[],plan)

class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.settings=Settings(data_dir=self.temp.name,api_key='fake-only',mode='auto')
        self.app=create_app(self.settings);self.app.testing=True
        self.client=self.app.test_client();self.ws=self.app.extensions['workspace'];self.store=self.app.extensions['store']
        self.sid=self.client.get('/api/workspace').get_json()['session']['id']
    def tearDown(self): self.temp.cleanup()
    def test_state_visible_during_generation_survives_failure_and_reuses_estimate_on_retry(self):
        calls=[]
        def generate(instruction,context,schema='response'):
            calls.append(schema)
            if schema=='estimate':
                e=context['events'][-1]
                return dict(empty_state(),current_direction='대화 상태 추적',support_request='concept',support_request_evidence_ids=[e['id']])
            p=self.client.get('/api/turn/progress?request_id=retry').get_json()
            self.assertEqual(p['phase'],'generating')
            self.assertEqual(p['estimate']['state']['response_status'],'generating')
            self.assertIsNotNone(self.store.latest_estimate(self.sid))
            self.assertNotIn('estimate',self.app.test_client().get('/api/turn/progress?request_id=retry').get_json())
            raise ModelError('답변 생성 시간 초과','response_timeout')
        self.ws.llm.generate=generate
        payload={'request_id':'retry','action':'message','text':'대화 상태 추적이 뭐야?'}
        first=self.client.post('/api/turn',json=payload,headers=HEADERS)
        self.assertEqual(first.status_code,502)
        self.assertEqual(first.get_json()['snapshot']['estimate']['state']['response_status'],'error')
        self.assertEqual(calls,['estimate','response'])
        self.ws.llm.generate=Mock(return_value=response())
        second=self.client.post('/api/turn',json=payload,headers=HEADERS)
        self.assertEqual(second.status_code,200)
        self.assertEqual(self.ws.llm.generate.call_count,1)
        self.assertTrue(second.get_json()['response']['recommendations'])
        self.assertEqual(second.get_json()['snapshot']['estimate']['state']['response_status'],'completed')
        self.assertEqual(len(self.store.export(self.sid)['estimate_history']),1)
        self.assertEqual(len([e for e in self.store.events(self.sid) if e['type']=='user_input']),1)
    def test_support_completion_is_not_resolution(self):
        value='관심을 연구 문제로 어떻게 구체화할지 모르겠어'
        def generate(instruction,context,schema='response'):
            if schema=='estimate':
                i=context['events'][-1]['id']
                return dict(empty_state(),current_direction='연구 주제',support_request='formulate',support_request_evidence_ids=[i],
                    difficulties=[difficulty(i,value,'formulation')])
            return response()
        self.ws.llm.generate=generate
        result=self.client.post('/api/turn',json={'request_id':'support','text':value},headers=HEADERS).get_json()
        history=result['snapshot']['estimate']['state']['difficulty_history'][0]
        self.assertEqual(history['lifecycle'],'active')
        self.assertTrue(history['support_provided'])
        self.assertEqual(result['snapshot']['estimate']['state']['difficulty_status'],'expressed')

class ContextRegressionTests(unittest.TestCase):
    def test_partial_resolution_keeps_a_different_current_difficulty(self):
        previous=preview_state([event(1,'개념을 이해하기 어려워')])
        value='이제 개념이 이해됐어. 그런데 주제를 어떻게 구체화할지 모르겠어'
        raw=dict(empty_state(),difficulties=[difficulty(2,value,'formulation')],difficulty_updates=[{
            'difficulty_id':previous['difficulty_history'][0]['id'],'outcome':'resolved',
            'evidence_quotes':[{'event_id':2,'quote':'이제 개념이 이해됐어'}]}])
        state,_=validate_state(raw,[event(1,'개념을 이해하기 어려워'),event(2,value)],previous)
        self.assertEqual(state['difficulty_history'][0]['lifecycle'],'resolved')
        self.assertEqual(state['difficulties'][0]['type'],'formulation')
        self.assertEqual(state['difficulty_status'],'expressed')
    def test_comparison_keeps_old_candidates_in_context_even_after_many_explanation_turns(self):
        with tempfile.TemporaryDirectory() as directory:
            settings=Settings(data_dir=directory,api_key='fake-only',mode='auto')
            app=create_app(settings);client=app.test_client();ws=app.extensions['workspace'];store=app.extensions['store']
            sid=client.get('/api/workspace').get_json()['session']['id']
            candidate={'id':'remember-me','title':'이전 후보','description':'연구 문제','search_query':'user intent'}
            store.add_message(sid,'first:assistant','assistant',{'recommendations':[candidate]})
            for i in range(10):
                store.add_message(sid,f'explain{i}:assistant','assistant',{'answer':'설명','recommendations':[]})
            captured={}
            def generate(instruction,context,schema='response'):
                captured[schema]=context
                if schema=='estimate':
                    i=context['events'][-1]['id']
                    return dict(empty_state(),support_request='compare',support_request_evidence_ids=[i])
                return response()
            ws.llm.generate=generate
            result=client.post('/api/turn',json={'request_id':'compare','text':'이전 후보를 비교해줘'},headers=HEADERS)
            self.assertEqual(result.status_code,200)
            self.assertEqual(captured['response']['available_directions'][0]['id'],'remember-me')
            self.assertTrue(result.get_json()['response']['recommendations'])
            self.assertEqual(result.get_json()['snapshot']['estimate']['plan']['recommendation_focus'],'existing_candidates')

class ResponseSafetyTests(unittest.TestCase):
    def test_help_does_not_justify_advancing_past_an_unresolved_concept(self):
        previous=preview_state([event(1,'개념을 이해하기 어려워')])
        previous['support_request']='concept'
        plan=select_support(previous,has_candidates=True)
        raw=response()
        raw.update(next_step='이제 후보를 결정하세요.',next_step_reason='개념을 이해했으므로 다음 단계입니다.')
        value=clean_response(raw,[],plan)
        self.assertNotIn('결정',value['next_step'])
        self.assertNotIn('이해했으므로',value['next_step_reason'])
        self.assertIn('예시',value['next_step'])
    def test_displayed_research_problem_does_not_add_a_model_invented_purpose(self):
        source='추천 설명 방식이 연구자의 선택 주도성에 미치는 영향을 연구하고 싶어.'
        raw=dict(empty_state(),direction_stage='forming',research_problem='추천 설명 방식과 연구자 선택 주도성의 영향을 분석하여 연구 자율성을 증진하는 도구 개발',
            problem_evidence=[{'event_id':1,'quote':source}])
        state,_=validate_state(raw,[event(1,source)])
        self.assertEqual(state['direction_stage'],'forming')
        self.assertNotIn('도구 개발',state['research_problem'])
        self.assertIn(state['research_problem'],source)

class AgencyTests(unittest.TestCase):
    def test_narrowing_request_does_not_force_finalization(self):
        state=preview_state([event(1,'연구 방향을 구체화해줘')])
        plan=select_support(state)
        raw=response()
        raw.update(next_step='후보를 선택하여 연구 질문을 확정하세요.',next_step_reason='독립 변수와 종속 변수를 확정해야 합니다.')
        value=clean_response(raw,[],plan)
        self.assertNotIn('확정',value['next_step']+value['next_step_reason'])
        self.assertIn('범위',value['next_step'])
