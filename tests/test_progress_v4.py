"""Contrast cases for progress, topic repair, grounded phenomena and repetition."""
import copy
import tempfile
import unittest
from unittest.mock import Mock
from app import create_app
from core.config import Settings
from core.state import empty_state, validate_state
from core.presentation import enrich_state
from core.policy import select_support
from core.browse import completed_response_issues
from core.progress import grounded_span
from test_browse import OLD,NEW,reply

H={'X-Workspace-Request':'1'}
def event(i,kind='user_input',**payload):return {'id':i,'type':kind,'payload':payload}
def estimate(raw,events,previous=None):
    state,warnings=validate_state(raw,events,previous,trace=events)
    return enrich_state(state,events,previous),warnings


class ProgressTests(unittest.TestCase):
    def test_refine_is_observed_progress_without_establishing_a_research_problem(self):
        events=[event(1,text='모호한 입력의 의도 해석'),event(2,'topic_refine',title='되묻기 시점',recommendation_id='a')]
        state,_=estimate(dict(empty_state(),direction_stage='forming',current_direction='이전의 아주 긴 포괄 관심'),events)
        self.assertEqual(state['direction_stage'],'unknown')
        self.assertEqual(state['research_problem'],'')
        self.assertEqual(state['exploration_progress']['status'],'narrowing')
        self.assertEqual(state['exploration_progress']['problem_status'],'unconfirmed')
        self.assertIn('되묻기 시점',state['chat_summary'])
        self.assertNotIn('판단 유보',state['chat_summary'])
        self.assertEqual(select_support(state)['breadth'],'specific')
        self.assertEqual(select_support(state,'browse')['breadth'],'broad')

    def test_different_descendant_choices_show_the_latest_focus_not_the_old_prefix(self):
        events=[event(1,text='의도 해석'),event(2,'topic_refine',title='되묻기',recommendation_id='a'),
                event(3,'topic_browse',title='추가 질문의 비용',recommendation_id='b',parent_topic_key='a'),
                event(4,'topic_refine',title='확신도 임계값',recommendation_id='c',parent_topic_key='b')]
        state,_=estimate(dict(empty_state(),current_direction='오래된 넓은 연구 맥락 ' * 20),events)
        self.assertIn('확신도 임계값',state['chat_summary'])
        self.assertNotIn('오래된',state['chat_summary'])
        self.assertEqual(state['difficulty_status'],'unknown')

    def test_paper_save_and_single_click_are_not_comprehension_or_problem(self):
        events=[event(1,text='의도 해석'),event(2,'topic_select',title='질문 비용'),event(3,'paper_save',paper_id='W1')]
        state,_=estimate(empty_state(),events)
        self.assertEqual(state['exploration_progress']['status'],'reviewing')
        self.assertEqual(state['research_problem'],'')
        self.assertEqual(state['difficulty_status'],'unknown')

    def test_phenomenon_does_not_require_causal_vocabulary(self):
        quote='전문 상담의 중의적 표현에서 언어모델이 맥락을 해석하는 정도를 살펴보는 연구'
        raw=dict(empty_state(),direction_stage='forming',research_problem=quote,
                 problem_evidence=[{'event_id':1,'quote':quote}],
                 problem_basis={'kind':'phenomenon','subject':'언어모델','focus':'맥락을 해석하는 정도','context':'전문 상담의 중의적 표현','event_id':1})
        state,warnings=estimate(raw,[event(1,text=quote)])
        self.assertEqual(state['direction_stage'],'forming')
        self.assertEqual(state['research_problem'],quote)
        self.assertNotIn('forming_without_grounded_problem',warnings)

    def test_semantic_roles_need_actual_distinct_source_spans(self):
        quote='언어모델의 사용자 의도 파악에 관심이 있어'
        for basis in ({'kind':'phenomenon','subject':'언어모델','focus':'맥락을 해석하는 정도','context':'전문 상담의 중의적 표현','event_id':1},
                      {'kind':'phenomenon','subject':quote,'focus':quote,'event_id':1},
                      {'kind':'phenomenon','subject':'언어모델','focus':'사용자 의도 파악','event_id':99}):
            with self.subTest(basis=basis):
                state,_=estimate(dict(empty_state(),direction_stage='forming',research_problem=quote,
                    problem_evidence=[{'event_id':1,'quote':quote}],problem_basis=basis),[event(1,text=quote)])
                self.assertEqual(state['research_problem'],'')

    def test_inflected_role_phrases_are_projected_back_to_literal_source(self):
        quote='전문 상담의 모호한 표현에서 언어모델이 맥락을 해석하는 정도를 살펴보고 싶어'
        raw=dict(empty_state(),direction_stage='forming',research_problem='전문 상담의 모호한 표현에서 언어모델의 맥락 해석 정도',
                 problem_evidence=[{'event_id':1,'quote':quote}],
                 problem_basis={'kind':'phenomenon','subject':'언어모델','focus':'맥락 해석 정도','context':'전문 상담 상황','event_id':1},
                 focus_update={'operation':'refine','previous_focus':'','current_focus':'언어모델의 맥락 해석 정도',
                               'evidence_quotes':[{'event_id':1,'quote':quote}]})
        state,_=estimate(raw,[event(1,text=quote)])
        self.assertEqual(state['direction_stage'],'forming')
        self.assertIn(state['problem_basis']['focus'],quote)
        self.assertIn(state['focus_update']['current_focus'],quote)
        self.assertEqual(state['exploration_progress']['status'],'narrowing')

    def test_alignment_does_not_introduce_unobserved_content_words(self):
        quote='전문 상담의 모호한 표현에서 언어모델이 맥락을 해석하는 정도'
        self.assertEqual(grounded_span('의료 상담에서의 환자 의도 해석',quote),'')
        self.assertEqual(grounded_span('초등학생 상담 상황',quote,context=True),'')
        self.assertIn(grounded_span('LLM의 맥락 해석','LLM이 맥락을 해석하는 정도'),'LLM이 맥락을 해석하는 정도')

    def test_card_text_and_generic_help_cannot_become_grounded_problem(self):
        for kind,quote in [('topic_refine','언어모델이 모호한 표현을 해석하는 정도'),('user_input','이 연구 주제를 어떻게 구체화할지 모르겠어')]:
            state,_=estimate(dict(empty_state(),direction_stage='forming',research_problem=quote,
                 problem_evidence=[{'event_id':1,'quote':quote}],
                 problem_basis={'kind':'phenomenon','subject':quote[:4],'focus':quote[5:],'event_id':1}),[event(1,kind,text=quote,title=quote)])
            self.assertEqual(state['research_problem'],'')

    def correction(self):
        before='도메인 맥락과 사용자의 개념 이해도를 살펴보고 싶어'
        after='지금 추천 주제들은 사용자의 이해도만 반영하는거 아니야? 모호한 표현에 대한 모델의 의도 해석이 궁금해'
        events=[event(1,text=before),event(2,text=after)]
        previous=enrich_state(dict(empty_state(),clarified_aspects=[{'kind':'perspective','text':before,'evidence_ids':[1]}]),events[:1])
        return events,previous

    def test_same_topic_can_contain_a_focus_replacement_without_difficulty(self):
        events,previous=self.correction()
        raw=dict(empty_state(),topic_relation='same',current_direction='사용자 이해도',
                 clarified_aspects=[{'kind':'perspective','evidence_quotes':[{'event_id':2,'quote':'지금 추천 주제들은 사용자의 이해도만 반영하는거 아니야?'}]}])
        state,_=estimate(raw,events,previous)
        self.assertEqual(state['topic_relation'],'same')
        self.assertEqual(state['focus_update']['operation'],'replace')
        self.assertIn('모호한 표현',state['recent_change'])
        self.assertEqual(state['exploration_progress']['status'],'revising')
        self.assertEqual(state['difficulty_status'],'unknown')
        self.assertTrue(select_support(state)['assistance_required'])
        self.assertFalse(any('이해도' in a['text'] for a in state['clarified_aspects']))
        self.assertTrue(state['superseded_aspects'])

    def test_explicit_contrast_is_not_kept_as_an_addition(self):
        events,previous=self.correction()
        state,_=estimate(dict(empty_state(),focus_update={'operation':'add','previous_focus':'사용자의 이해도',
            'current_focus':'모호한 표현에 대한 모델의 의도 해석',
            'evidence_quotes':[{'event_id':2,'quote':events[1]['payload']['text']}]}),events,previous)
        self.assertEqual(state['focus_update']['operation'],'replace')
        self.assertFalse(any('이해도' in a['text'] for a in state['clarified_aspects']))

    def test_retired_focus_does_not_return_after_a_later_choice_or_legacy_session(self):
        events,previous=self.correction()
        events += [event(3,'topic_refine',title='되묻기 설계',recommendation_id='a')]
        state,_=estimate(empty_state(),events,previous)
        self.assertEqual(state['exploration_progress']['status'],'narrowing')
        self.assertTrue(state['focus_history'])
        self.assertFalse(any('이해도' in a['text'] for a in state['clarified_aspects']))

    def test_invented_focus_update_is_rejected(self):
        raw=dict(empty_state(),focus_update={'operation':'replace','previous_focus':'의도 해석',
            'current_focus':'의료 로봇', 'evidence_quotes':[{'event_id':1,'quote':'의도 해석을 보고 싶어'}]})
        state,_=estimate(raw,[event(1,text='의도 해석을 보고 싶어')])
        self.assertIsNone(state['focus_update'])

    def test_positive_focus_quote_is_used_instead_of_the_rejected_clause(self):
        quote='이해도 측정이 아니라 상담에서 모델이 모호한 문장을 해석하는 정도를 살펴보고 싶어'
        raw=dict(empty_state(),direction_stage='forming',research_problem='상담에서 모델이 모호한 문장을 해석하는 정도',
                 problem_evidence=[{'event_id':1,'quote':quote}],
                 problem_basis={'kind':'phenomenon','subject':'모델','focus':'모호한 문장을 해석하는 정도','context':'상담에서','event_id':1})
        state,_=estimate(raw,[event(1,text=quote)])
        self.assertEqual(state['direction_stage'],'forming')
        self.assertIn('모호한 문장',state['research_problem'])


class ResponseQualityTests(unittest.TestCase):
    def test_refine_and_direction_repetition_is_detected_but_explanations_can_reuse(self):
        raw=reply(OLD)
        for mode in ('directions','formulate'):
            self.assertIn('repeated_candidate_set',completed_response_issues(raw,OLD,mode))
        for mode in ('concept','compare','connection','literature'):
            self.assertEqual(completed_response_issues(raw,OLD,mode),[])

    def test_new_titles_cannot_hide_reused_descriptions(self):
        raw=reply(copy.deepcopy(OLD))
        for i,r in enumerate(raw['recommendations']):r['title']=f'다른 제목 {i}'
        self.assertIn('repeated_descriptions',completed_response_issues(raw,OLD,'formulate'))

    def test_stable_titles_with_new_information_are_allowed_outside_browse(self):
        raw=reply(copy.deepcopy(OLD))
        for i,r in enumerate(raw['recommendations']):r['description']=f'새로운 하위 조건 {i}에서의 문제'
        self.assertEqual(completed_response_issues(raw,OLD,'formulate'),[])

    def test_refine_must_not_return_unchanged_selected_parent(self):
        raw=reply([OLD[0],*NEW[:2]])
        self.assertIn('selected_parent_repeated',completed_response_issues(raw,OLD,'formulate',OLD[0]))

    def test_explicit_request_to_show_the_same_list_is_respected(self):
        self.assertEqual(completed_response_issues(reply(OLD),OLD,'directions',latest_input='기존 후보 목록을 그대로 보여줘'),[])

    def test_refine_repetition_repaired_in_same_turn_without_second_estimate(self):
        with tempfile.TemporaryDirectory() as d:
            app=create_app(Settings(data_dir=d,mode='auto',api_key='fake-only'));client=app.test_client()
            ws=app.extensions['workspace'];store=app.extensions['store']
            sid=client.get('/api/workspace').get_json()['session']['id']
            store.add_message(sid,'seed:assistant','assistant',{'recommendations':OLD})
            calls=[];responses=iter([reply(OLD),reply(NEW)])
            def generate(instruction,context,schema='response'):
                calls.append((schema,copy.deepcopy(context)))
                return empty_state() if schema=='estimate' else next(responses)
            ws.llm.generate=generate
            result=client.post('/api/turn',json={'request_id':'refine','action':'refine','recommendation_id':OLD[0]['id']},headers=H)
            self.assertEqual(result.status_code,200)
            self.assertEqual([s for s,c in calls],['estimate','response','response'])
            state=result.get_json()['snapshot']['estimate']['state']
            self.assertEqual(state['response_quality']['attempts'],2)
            self.assertEqual(state['response_quality']['status'],'passed')
            self.assertEqual(state['support_issues'],[])
            self.assertTrue(any(e['type']=='response_validation' for e in store.events(sid)))
            self.assertIn('response_validation',calls[-1][1])

    def test_correction_gets_a_direct_answer_without_creating_difficulty(self):
        with tempfile.TemporaryDirectory() as d:
            app=create_app(Settings(data_dir=d,mode='auto',api_key='fake-only'));client=app.test_client()
            ws=app.extensions['workspace'];calls=[]
            def generate(instruction,context,schema='response'):
                calls.append((schema,copy.deepcopy(context)))
                return empty_state() if schema=='estimate' else reply()
            ws.llm.generate=generate
            result=client.post('/api/turn',json={'request_id':'correct','text':'추천은 이해도만 반영하는거 아니야? 모호한 표현의 의도 해석이 궁금해'},headers=H)
            self.assertEqual(result.status_code,200)
            data=result.get_json()
            self.assertTrue(data['response']['assistance']['body'])
            self.assertEqual(data['snapshot']['estimate']['state']['difficulty_status'],'unknown')
            plan=next(c['support_plan'] for s,c in calls if s=='response')
            self.assertTrue(plan['correction_requested'])
            self.assertTrue(plan['assistance_required'])
