"""User-log regressions and counterexamples for explicit, pending, and inferred support."""
import copy
import tempfile
import unittest
from unittest.mock import Mock

from app import create_app
from core.behavior import topic_steps, topic_key, behavior_signals, support_issues
from core.config import Settings
from core.difficulty import difficulty_view
from core.policy import select_support
from core.presentation import enrich_state
from core.state import empty_state, validate_state, preview_state


def ev(i, kind, **payload):
    return {"id": i, "type": kind, "payload": payload}


def assertion(i, value, kind):
    return {"type": kind, "status": "expressed", "evidence_ids": [i],
            "evidence_quotes": [{"event_id": i, "quote": value}]}


def conceptual_trace():
    return [ev(1, "user_input", text="사용자 임베딩이 뭐야?"), ev(2, "agent_response", support_mode="concept"),
            ev(3, "user_input", text="다른 예시로 설명해줘"), ev(4, "agent_response", support_mode="concept"),
            ev(5, "user_input", text="다시 더 쉽게 설명해줘")]


class DifficultyMeaningTests(unittest.TestCase):
    def test_vague_experiences_keep_uncategorized_type(self):
        for value in ["계속 빙빙 도는 느낌이야", "추천을 따라가는데 뭘 하고 있는지 모르겠어", "아직 막막해"]:
            with self.subTest(value=value):
                st, _ = validate_state(empty_state(), [ev(1, "user_input", text=value)])
                self.assertEqual(st["difficulty_status"], "expressed")
                self.assertEqual(st["difficulties"][0]["type"], "unspecified")
                self.assertEqual(st["difficulties"][0]["evidence_quotes"][0]["quote"], value)
                self.assertEqual(select_support(st)["mode"], "orient")

    def test_experience_cues_do_not_require_the_word_difficult(self):
        for value, kind in [("후보 차이가 잘 안 와닿아", "selection"), ("설명이 머리에 안 들어와", "concept"),
                            ("연구 질문을 어떻게 잡을지 감이 안 와", "formulation")]:
            st, _ = validate_state(dict(empty_state(), difficulties=[assertion(1, value, kind)]), [ev(1, "user_input", text=value)])
            self.assertEqual(st["difficulties"][0]["type"], kind)
            self.assertEqual(st["difficulty_status"], "expressed")

    def test_requests_negations_hypotheticals_and_other_people_are_not_experiences(self):
        for value in ["임베딩이 뭐야?", "후보를 비교해줘", "다른 주제는 없어?", "어려운 개념을 설명해줘",
                      "헷갈리지 않아", "모르겠다는 건 아니야", "빙빙 도는 건 아니야", "막막하면 어떻게 해?",
                      "다른 사용자가 어려워하는 상황을 연구하고 싶어"]:
            with self.subTest(value=value):
                raw = dict(empty_state(), difficulties=[assertion(1, value, "concept")])
                st, _ = validate_state(raw, [ev(1, "user_input", text=value)])
                self.assertEqual(st["difficulties"], [])

    def test_technical_connection_is_concept_and_personal_relevance_is_connection(self):
        technical = "이 벡터를 어떻게 모델 입력에 연결하는지 모르겠어"
        st, warnings = validate_state(dict(empty_state(), support_request="connection", support_request_evidence_ids=[1],
            difficulties=[assertion(1, technical, "connection")]), [ev(1, "user_input", text=technical)])
        self.assertEqual(st["difficulties"][0]["type"], "concept")
        self.assertEqual(st["support_request"], "concept")
        self.assertIn("difficulty_type_reinterpreted", warnings)
        value = "이 후보가 내 관심과 어떻게 연결되는지 모르겠어"
        st, _ = validate_state(dict(empty_state(), difficulties=[assertion(1, value, "connection")]), [ev(1, "user_input", text=value)])
        self.assertEqual(st["difficulties"][0]["type"], "connection")

    def test_pending_history_stays_visible_adjusts_browse_and_keeps_original_immutable(self):
        value = "이 벡터를 어떻게 모델 입력에 연결하는지 모르겠어"
        first = ev(1, "user_input", text=value)
        old = dict(empty_state(), current_direction="사용자 임베딩", difficulty_history=[dict(assertion(1, value, "connection"),
            id="old-connection", lifecycle="active", topic="사용자 임베딩", first_event_id=1, last_event_id=1, support_provided=True)])
        original = copy.deepcopy(old)
        events = [first, ev(2, "topic_browse", title="임베딩 반영", recommendation_id="r")]
        st, _ = validate_state(dict(empty_state(), current_direction="임베딩 반영", topic_relation="same"), events, old)
        st = enrich_state(st, events, old)
        self.assertEqual(old, original)
        self.assertEqual(st["pending_difficulties"][0]["id"], "old-connection")
        self.assertEqual(st["pending_difficulties"][0]["type"], "concept")
        self.assertEqual(st["difficulty_history"][0]["original_type"], "connection")
        self.assertEqual(difficulty_view(st), "unconfirmed")
        self.assertIn("해소 여부", st["chat_summary"])
        plan = select_support(st, "browse")
        self.assertEqual(plan["mode"], "browse")
        self.assertEqual(plan["support_adjustment"]["source"], "pending")
        self.assertEqual(plan["support_adjustment"]["type"], "concept")

    def test_unspecified_difficulty_can_resolve_and_topic_change_removes_pending(self):
        first = ev(1, "user_input", text="계속 빙빙 도는 느낌이야")
        previous = preview_state([first])
        key = previous["difficulty_history"][0]["id"]
        resolved = "이제 탐색 흐름이 정리됐어"
        raw = dict(empty_state(), difficulty_updates=[{"difficulty_id": key, "outcome": "resolved",
               "evidence_quotes": [{"event_id": 2, "quote": resolved}]}])
        st, _ = validate_state(raw, [first, ev(2, "user_input", text=resolved)], previous)
        self.assertEqual(st["difficulty_history"][0]["lifecycle"], "resolved")
        self.assertEqual(st["pending_difficulties"], [])
        self.assertEqual(difficulty_view(st), "clear")
        changed, _ = validate_state(dict(empty_state(), topic_relation="changed", topic_evidence_ids=[2]),
            [first, ev(2, "user_input", text="다른 주제로 바꿀래")], previous)
        self.assertEqual(changed["pending_difficulties"], [])

    def test_new_behavior_does_not_overwrite_previous_expressed_evidence(self):
        first = ev(1, "user_input", text="개념이 아직 이해가 안 돼")
        previous = preview_state([first])
        trace = [first] + [dict(e, id=e["id"] + 1) for e in conceptual_trace()]
        st, _ = validate_state(empty_state(), trace, previous)
        self.assertEqual(st["difficulty_history"][0]["status"], "expressed")
        self.assertEqual(st["difficulty_history"][0]["evidence_quotes"][0]["quote"], first["payload"]["text"])
        self.assertEqual(st["difficulty_history"][0]["lifecycle"], "unconfirmed")
        self.assertEqual(difficulty_view(st), "unconfirmed")
        self.assertTrue(st["pending_difficulties"][0]["recent_signals"])


class BehaviorEvidenceTests(unittest.TestCase):
    def test_selection_execution_pairs_are_one_step_not_revisits(self):
        events = [ev(1, "topic_select", title="A 방향", recommendation_id="a"), ev(2, "topic_browse", title="A 방향", recommendation_id="a"),
                  ev(3, "topic_select", title="B 방향", recommendation_id="b"), ev(4, "topic_browse", title="B 방향", recommendation_id="b")]
        self.assertEqual(len(topic_steps(events)), 2)
        self.assertEqual(behavior_signals(events), [])

    def test_new_ids_for_same_normalized_title_are_the_same_topic(self):
        self.assertEqual(topic_key({"title": "사용자 임베딩", "recommendation_id": "old"}),
                         topic_key({"title": "「사용자  임베딩」", "recommendation_id": "new"}))
        events = [ev(i, "topic_browse", title="A 방향" if i % 2 else "B 방향", recommendation_id=str(i)) for i in range(1, 5)]
        self.assertIn("candidate_revisits", [s["pattern"] for s in behavior_signals(events)])
        st, _ = validate_state(empty_state(), events)
        self.assertEqual(st["difficulties"], [])

    def test_multiple_families_infer_possible_without_a_difficulty_utterance(self):
        trace = conceptual_trace()
        observations = [e for e in trace if e["type"] == "user_input"]
        st, _ = validate_state(empty_state(), observations, trace=trace)
        self.assertEqual(st["difficulty_status"], "possible")
        self.assertEqual(st["difficulties"][0]["type"], "concept")
        self.assertEqual(st["difficulties"][0]["source"], "behavior")
        self.assertEqual(st["difficulties"][0]["evidence_quotes"], [])
        self.assertEqual({s["family"] for s in st["difficulties"][0]["signals"]}, {"dialogue", "repair"})
        raw = dict(empty_state(), difficulties=[{"type": "concept", "status": "expressed", "evidence_ids": [1, 3, 5], "evidence_quotes": []}])
        guarded, _ = validate_state(raw, observations, trace=trace)
        self.assertEqual(guarded["difficulty_status"], "possible")
        raw = dict(empty_state(), clarified_aspects=[{"kind": "perspective", "evidence_quotes": [{"event_id": 5, "quote": "다시 더 쉽게 설명해줘"}]}])
        request_only, _ = validate_state(raw, observations, trace=trace)
        self.assertEqual(request_only["difficulty_status"], "possible")

    def test_navigation_and_distinct_paper_review_combine_as_possible_selection_support(self):
        trace = [ev(i, "topic_browse", title="A 방향" if i % 2 else "B 방향", recommendation_id=str(i)) for i in range(1, 5)]
        trace += [ev(5 + i, "paper_search_submit", query=f"academic query {i}") for i in range(3)]
        trace += [ev(8, "abstract_expand", paper_id="W1"), ev(9, "paper_save", paper_id="W2"),
                  ev(10, "topic_browse", title="A 방향", recommendation_id="new-A")]
        st, _ = validate_state(empty_state(), trace)
        self.assertEqual(st["difficulty_status"], "possible")
        self.assertEqual(st["difficulties"][0]["type"], "selection")

    def test_refinement_and_repeated_requests_combine_without_expressed_difficulty(self):
        trace = [ev(1, "topic_refine", title="A 방향", recommendation_id="a"), ev(2, "agent_response", support_mode="formulate"),
                 ev(3, "user_input", text="같은 주제 범위를 구체화해줘"), ev(4, "agent_response", support_mode="formulate"),
                 ev(5, "topic_refine", title="A 방향", recommendation_id="a2"), ev(6, "agent_response", support_mode="formulate"),
                 ev(7, "user_input", text="범위를 다시 구체화해줘"), ev(8, "agent_response", support_mode="formulate"),
                 ev(9, "topic_refine", title="A 방향", recommendation_id="a3")]
        st, _ = validate_state(empty_state(), trace)
        self.assertEqual(st["difficulty_status"], "possible")
        self.assertEqual(st["difficulties"][0]["type"], "formulation")
        trace[6]["payload"]["text"] = "학술 탐색 상황으로 범위를 구체화해줘"
        raw = dict(empty_state(), clarified_aspects=[{"kind": "scope", "evidence_quotes": [{"event_id": 7, "quote": "학술 탐색 상황"}]}])
        progressing, _ = validate_state(raw, trace)
        self.assertEqual(progressing["difficulties"], [])

    def test_unanswered_error_retries_do_not_make_repeated_explanation_support(self):
        trace = [e for e in conceptual_trace() if e["type"] != "agent_response"]
        trace += [ev(6, "model_error", message="HTTP 503")]
        st, _ = validate_state(empty_state(), trace)
        self.assertEqual(st["difficulties"], [])
        self.assertEqual(behavior_signals(trace), [])

    def test_one_request_or_ordinary_concept_questions_do_not_infer_difficulty(self):
        trace = conceptual_trace()
        trace[2]["payload"]["text"] = "임베딩 예시를 설명해줘"
        trace[4]["payload"]["text"] = "개념을 설명해줘"
        st, _ = validate_state(empty_state(), trace)
        self.assertEqual(st["difficulties"], [])
        st, _ = validate_state(empty_state(), trace[:1])
        self.assertEqual(st["difficulties"], [])

    def test_positive_followup_and_topic_switch_do_not_reactivate_old_patterns(self):
        trace = conceptual_trace()
        for value in ["이제 개념이 이해됐어", "다른 주제로 바꿀래"]:
            st, _ = validate_state(empty_state(), trace + [ev(6, "user_input", text=value)])
            self.assertEqual(st["difficulties"], [])

    def test_system_repetition_is_separate_and_comparisons_can_keep_candidates(self):
        recs = [{"title": x, "description": x + " 설명"} for x in ["A", "B", "C"]]
        trace = [ev(1, "user_input", text="LLM"), ev(2, "agent_response", support_mode="directions", offered_options=recs),
                 ev(3, "topic_browse", title="A", recommendation_id="a"), ev(4, "agent_response", support_mode="browse", offered_options=recs)]
        st, _ = validate_state(empty_state(), trace)
        self.assertEqual(st["difficulties"], [])
        self.assertEqual(st["support_issues"][0]["type"], "repeated_candidates")
        trace[-1]["payload"]["support_mode"] = "compare"
        self.assertEqual(support_issues(trace), [])


class SupportPipelineTests(unittest.TestCase):
    def test_current_browse_keeps_pending_adjustment_and_existing_two_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Settings(data_dir=directory, api_key="fake-only", mode="auto"))
            client = app.test_client();ws = app.extensions["workspace"];store = app.extensions["store"]
            sid = client.get("/api/workspace").get_json()["session"]["id"]
            value = "이 벡터를 어떻게 모델 입력에 연결하는지 모르겠어"
            e = store.event(sid, "user_input", {"text": value}, "seed")
            prior = preview_state(store.events(sid))
            prior["response_status"] = "completed"
            store.put_estimate(sid, "prior", e["id"], prior, select_support(prior), {})
            store.add_message(sid, "prior:assistant", "assistant", {"recommendations": [{"id": "r", "title": "사용자 임베딩 반영", "description": "반영 원리", "search_query": "user embedding"}]})
            contexts = []
            def generate(instruction, context, schema="response"):
                contexts.append((schema, context))
                if schema == "estimate":
                    return dict(empty_state(), current_direction="사용자 임베딩 반영", topic_relation="same")
                return {"assistance": {"title": "주제 둘러보기", "body": "임베딩을 입력에 반영하는 연결 원리를 살펴볼 수 있어요. 예를 들어 상황을 나타낸 벡터를 입력 정보로 제공해 볼 수 있어요.", "paper_ids": []},
                    "recommendations": [{"title": f"새 관점 {i}", "description": f"다른 측면 {i}", "search_query": f"user embedding aspect {i}", "relation": "perspective"} for i in range(3)],
                    "next_step": "반영 원리가 쓰이는 상황을 살펴보세요.", "next_step_reason": "현재 관심과 연결해 이해할 수 있어요."}
            ws.llm.generate = generate
            result = client.post("/api/turn", json={"request_id": "browse", "action": "browse", "recommendation_id": "r"}, headers={"X-Workspace-Request": "1"})
            self.assertEqual(result.status_code, 200)
            self.assertEqual([s for s, c in contexts], ["estimate", "response"])
            self.assertEqual(contexts[-1][1]["support_plan"]["mode"], "browse")
            self.assertEqual(contexts[-1][1]["support_plan"]["support_adjustment"]["source"], "pending")
            data = result.get_json()
            self.assertEqual(data["response"]["difficulty_status"], "unconfirmed")
            self.assertEqual(data["snapshot"]["messages"][-1]["content"]["difficulty_status"], "unconfirmed")
            self.assertEqual(len(data["response"]["recommendations"]), 3)
            self.assertTrue(data["response"]["recommendations"][0]["parent_topic_key"])
