"""Grounded detailed panels and compact chat are projections of the same state."""
import tempfile
import unittest
from app import create_app
from core.config import Settings
from core.state import empty_state, validate_state
from core.presentation import enrich_state, compact
from core.policy import select_support
from core.service import clean_response

def event(i,kind='user_input',**payload):
    return {'id':i,'type':kind,'payload':payload}

class StatePresentationTests(unittest.TestCase):
    def test_aspects_need_exact_user_quotes_not_paper_actions_or_invented_quotes(self):
        events=[event(1,text='사용자의 목표 변경에 관심이 있어'),event(2,'paper_save',title='사용자 의도')]
        raw=dict(empty_state(),clarified_aspects=[
            {'kind':'perspective','evidence_quotes':[{'event_id':1,'quote':'목표 변경'}]},
            {'kind':'scope','evidence_quotes':[{'event_id':1,'quote':'대학생 30명'}]},
            {'kind':'problem','evidence_quotes':[{'event_id':2,'quote':'사용자 의도'}]}])
        state,_=validate_state(raw,events)
        self.assertEqual(len(state['clarified_aspects']),1)
        self.assertEqual(state['clarified_aspects'][0]['text'],'목표 변경')
    def test_refine_choice_and_paper_activity_are_described_without_claiming_formation(self):
        events=[event(1,text='LLM 사용자 의도'),event(2,'topic_refine',title='목표 변경',recommendation_id='r'),
                event(3,'paper_search_submit',query='intent change'),event(4,'abstract_expand',title='논문')]
        state=enrich_state(dict(empty_state(),direction_stage='exploring',current_direction='목표 변경'),events,empty_state())
        self.assertEqual(state['direction_stage'],'exploring')
        self.assertIn('선택',state['recent_change'])
        self.assertIn('논문 검색·초록 펼치기',state['flow_summary'])
        self.assertNotIn('이해했',state['flow_summary'])
        self.assertNotIn('확정',state['flow_summary'])
        self.assertEqual(state['observed_directions'][0]['title'],'목표 변경')
    def test_system_candidates_are_not_user_observed_directions(self):
        state=enrich_state(empty_state(),[event(1,text='LLM')])
        self.assertEqual(state['observed_directions'],[])
        self.assertTrue(state['open_questions'])
        self.assertEqual(state['clarified_aspects'][0]['text'],'LLM')
    def test_prior_aspects_are_retained_only_with_original_utterances_and_current_context(self):
        previous=dict(empty_state(),clarified_aspects=[{'kind':'scope','text':'다중 턴 대화','evidence_ids':[1]}])
        events=[event(1,text='다중 턴 대화 안에서 보고 싶어'),event(2,text='개념이 뭐야?')]
        retained=enrich_state(dict(empty_state()),events,previous)
        self.assertEqual(retained['clarified_aspects'][0]['text'],'다중 턴 대화')
        changed=enrich_state(dict(empty_state(),topic_relation='changed'),events,previous)
        self.assertFalse(any(a['text']=='다중 턴 대화' for a in changed['clarified_aspects']))
        self.assertEqual(changed['direction_context_start_event_id'],2)
    def test_changes_do_not_repeat_model_invented_progress(self):
        state=enrich_state(dict(empty_state(),support_request='concept',recent_change='사용자가 완전히 이해함'),
                           [event(1,text='LLM'),event(2,text='그게 뭐야?')],empty_state())
        self.assertIn('질문이 추가',state['recent_change'])
        self.assertNotIn('완전히 이해',state['recent_change'])
    def test_topic_change_does_not_relabel_an_old_problem_or_quote_as_current(self):
        source='추천 설명 방식이 선택 주도성에 미치는 영향을 연구하고 싶어'
        events=[event(1,text=source),event(2,text='다른 주제로 바꿔서 로봇 상호작용을 살펴볼래')]
        raw=dict(empty_state(),current_direction='로봇 상호작용',direction_stage='forming',research_problem='추천 설명 방식과 선택 주도성의 영향',
                 problem_evidence=[{'event_id':1,'quote':source}],topic_relation='changed',topic_evidence_ids=[2],
                 clarified_aspects=[{'kind':'perspective','evidence_quotes':[{'event_id':1,'quote':'선택 주도성'}]}])
        state,_=validate_state(raw,events,empty_state())
        state=enrich_state(state,events,empty_state())
        self.assertEqual(state['research_problem'],'')
        self.assertEqual(state['direction_stage'],'unknown')
        self.assertFalse(any(a['text']=='선택 주도성' for a in state['clarified_aspects']))
    def test_compact_chat_does_not_hide_uncertainty(self):
        state=enrich_state(dict(empty_state(),current_direction='LLM 의도',direction_stage='unknown'),[event(1,text='LLM 의도')])
        self.assertIn('판단 유보',state['chat_summary'])
        self.assertLessEqual(len(state['chat_summary']),130)
        self.assertEqual(state['direction_stage'],'unknown')

class ResponsePresentationTests(unittest.TestCase):
    def raw(self):
        return {'assistance':{'title':'설명','body':'첫 번째 설명이에요. 두 번째 예시예요. 세 번째 세부 설명이에요.','paper_ids':[]},
                'recommendations':[{'title':'목표 변경','description':'대화 중 목표 변경을 살펴봐요. 두 번째 설명은 숨겨요.',
                                    'rationale':'현재 대화의 목표와 연결돼요.','search_query':'intent change','relation':'perspective'}],
                'next_step':'목표가 바뀌는 상황을 비교해 보세요. 다른 행동도 하세요.',
                'next_step_reason':'현재 관심 안에서 살펴보는 관점을 구분할 수 있어요.'}
    def test_short_body_keeps_full_explanation_available_without_losing_content(self):
        raw=self.raw();value=clean_response(raw,[],select_support(dict(empty_state(),support_request='concept')))
        self.assertEqual(value['assistance']['body'],'첫 번째 설명이에요. 두 번째 예시예요.')
        self.assertEqual(value['assistance']['details'],raw['assistance']['body'])
        self.assertFalse(value['assistance']['expanded'])
        self.assertEqual(value['next_step'],'목표가 바뀌는 상황을 비교해 보세요.')
        self.assertEqual(value['recommendations'][0]['description'],'대화 중 목표 변경을 살펴봐요.')
        self.assertEqual(value['recommendations'][0]['relation_label'],'관점 더하기')
        self.assertTrue(value['next_step_reason'])
    def test_explicit_detail_request_can_open_complete_explanation(self):
        events=[event(1,text='대화 상태 추적을 자세히 설명해줘')]
        state,_=validate_state(empty_state(),events)
        value=clean_response(self.raw(),[],select_support(state))
        self.assertTrue(value['assistance']['expanded'])
    def test_shortening_marks_excerpt_instead_of_destroying_the_full_text(self):
        value='아주 긴 설명 ' * 100
        result=compact(value,limit=100)
        self.assertLessEqual(len(result),100)
        self.assertTrue(result.endswith('…'))

class PipelinePresentationTests(unittest.TestCase):
    def test_compact_chat_and_detailed_panel_require_only_existing_two_model_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            app=create_app(Settings(data_dir=directory,api_key='fake-only',mode='auto'))
            client=app.test_client();workspace=app.extensions['workspace'];calls=[]
            def generate(instruction,context,schema='response'):
                calls.append(schema)
                if schema=='estimate':
                    i=context['events'][-1]['id']
                    return dict(empty_state(),current_direction='목표 변경',direction_stage='exploring',support_request='concept',
                        support_request_evidence_ids=[i],unresolved=['어떤 상황의 목표 변경을 살펴볼지'],
                        clarified_aspects=[{'kind':'interest','evidence_quotes':[{'event_id':i,'quote':'목표 변경'}]}])
                return ResponsePresentationTests().raw()
            workspace.llm.generate=generate
            r=client.post('/api/turn',json={'request_id':'compact','text':'목표 변경 개념이 뭐야?'},headers={'X-Workspace-Request':'1'})
            self.assertEqual(r.status_code,200)
            body=r.get_json();state=body['snapshot']['estimate']['state']
            self.assertEqual(calls,['estimate','response'])
            self.assertEqual(body['response']['state_summary'],state['chat_summary'])
            self.assertTrue(state['clarified_aspects'])
            self.assertTrue(state['open_questions'])
            self.assertTrue(state['recommendation_context'][0]['rationale'])
            self.assertEqual(state['next_step_reason'],body['response']['next_step_reason'])
