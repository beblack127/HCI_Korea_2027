import copy
import tempfile
import unittest
from unittest.mock import Mock

from app import create_app
from core.config import Settings
from core.llm import ModelError
from core.policy import select_support
from core.presentation import enrich_state
from core.readiness import assess_readiness, offer_wrapup, session_recap
from core.state import empty_state, validate_state

HEADERS = {"X-Workspace-Request": "1"}
FIRST = "추천 설명 방식이 연구자의 선택 주도성에 미치는 영향을 연구하고 싶어. 학술 탐색 상황에서 살펴볼래."
FOLLOW = "그 방향에서 안내 문구가 제시되는 상황을 살펴볼래."


def event(i, value):
    return {"id": i, "type": "user_input", "payload": {"text": value}}


def raw_state(first_id, latest_id):
    return dict(empty_state(), current_direction="추천 설명과 선택 주도성", direction_stage="forming",
        research_problem="추천 설명 방식이 연구자의 선택 주도성에 미치는 영향",
        problem_evidence=[{"event_id": first_id, "quote": FIRST}],
        clarified_aspects=[{"kind": "scope", "evidence_quotes": [{"event_id": first_id, "quote": "학술 탐색 상황"}]}],
        topic_relation="same", topic_evidence_ids=[latest_id], unresolved=["어떤 안내 문구를 비교할지"])


def ready_state(events):
    users = [e for e in events if e["type"] == "user_input"]
    first = users[0]["id"]
    prior, _ = validate_state(raw_state(first, first), [users[0]])
    prior = enrich_state(prior, [users[0]])
    state, _ = validate_state(raw_state(first, users[-1]["id"]), events, prior)
    state = enrich_state(state, events, prior)
    state["readiness"] = assess_readiness(state, events, prior)
    return state, prior


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.events = [event(1, FIRST), event(2, FOLLOW)]
        self.state, self.prior = ready_state(self.events)

    def test_grounded_problem_scope_and_followup_allow_review_without_confirming(self):
        r = self.state["readiness"]
        self.assertEqual(r["status"], "ready")
        self.assertTrue(all(c["met"] for c in r["checks"]))
        self.assertEqual(self.state["direction_stage"], "forming")
        self.assertNotIn("summary", r)

    def test_initial_specific_utterance_does_not_alone_establish_continuity(self):
        r = assess_readiness(self.prior, self.events[:1])
        self.assertEqual(r["status"], "not_ready")
        self.assertTrue(r["checks"][0]["met"])
        self.assertFalse(r["checks"][2]["met"])

    def test_clicks_or_many_paper_actions_do_not_replace_a_grounded_problem(self):
        events = [{"id": i, "type": "paper_save", "payload": {"title": "논문"}} for i in range(1, 50)]
        r = assess_readiness(dict(empty_state(), direction_stage="forming", research_problem="모델이 만든 문제"), events)
        self.assertEqual(r["status"], "not_ready")

    def test_invented_scope_quotes_and_uncertain_continuity_do_not_qualify(self):
        state = copy.deepcopy(self.state)
        state["clarified_aspects"] = [{"kind": "scope", "text": "임의로 만든 범위", "evidence_ids": [1]}]
        self.assertEqual(assess_readiness(state, self.events, self.prior)["status"], "not_ready")
        state = dict(self.state, topic_relation="uncertain")
        self.assertEqual(assess_readiness(state, self.events, self.prior)["status"], "not_ready")

    def test_topic_boundary_rejects_prior_problem_and_scope(self):
        state = dict(self.state, direction_context_start_event_id=2, topic_relation="changed")
        self.assertEqual(assess_readiness(state, self.events, self.prior)["status"], "not_ready")

    def test_offer_is_not_repeated_for_the_same_problem_and_scope(self):
        plan = select_support(self.state, "refine")
        first = offer_wrapup(self.state, plan)
        self.assertTrue(first)
        self.assertIsNone(offer_wrapup(self.state, plan))
        later = dict(self.state, change_evidence_ids=[3])
        later["readiness"] = assess_readiness(later, self.events + [event(3, "계속 살펴볼래")], self.state)
        self.assertIsNone(offer_wrapup(later, plan))

    def test_direct_difficulty_and_concept_request_defer_automatic_offer(self):
        plan = select_support(self.state, "refine")
        blocked = copy.deepcopy(self.state)
        blocked["difficulties"] = [{"status": "expressed", "type": "formulation"}]
        self.assertIsNone(offer_wrapup(blocked, plan))
        self.assertIsNone(offer_wrapup(self.state, dict(plan, mode="concept")))
        self.assertEqual(self.state["readiness"]["offered_signatures"], [])
        self.assertIsNotNone(offer_wrapup(self.state, dict(plan, mode="browse")))

    def test_recap_projects_verified_content_without_inventing_a_research_question(self):
        recap = session_recap(self.state)
        self.assertEqual(recap["research_problem"], self.state["research_problem"])
        self.assertEqual(recap["aspects"][0]["text"], "학술 탐색 상황")
        self.assertEqual(recap["remaining_questions"], ["어떤 안내 문구를 비교할지"])
        self.assertNotIn("research_question", recap)


class WrapupPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(Settings(data_dir=self.temp.name, api_key="fake-only", mode="auto"))
        self.app.testing = True
        self.client = self.app.test_client()
        self.ws = self.app.extensions["workspace"]
        self.store = self.app.extensions["store"]
        self.sid = self.client.get("/api/workspace").get_json()["session"]["id"]
        self.first = self.store.event(self.sid, "user_input", {"text": FIRST}, "seed:1")
        self.follow = self.store.event(self.sid, "user_input", {"text": FOLLOW}, "seed:2")
        self.state, self.prior = ready_state(self.store.events(self.sid))
        self.plan = select_support(self.state, "refine")
        self.offer = offer_wrapup(self.state, self.plan)
        self.state["response_status"] = "completed"
        self.store.put_estimate(self.sid, "seed", self.follow["id"], self.state, self.plan, {})
        self.store.add_message(self.sid, "seed:assistant", "assistant", {"wrapup_offer": self.offer})
        self.ws.llm.generate = Mock(side_effect=AssertionError("wrapup must not call LLM"))
        self.ws.search.search = Mock(side_effect=AssertionError("wrapup must not retrieve papers"))

    def tearDown(self):
        self.temp.cleanup()

    def wrapup(self, **changes):
        payload = dict(request_id="confirm", action="wrapup", readiness_signature=self.offer["signature"])
        payload.update(changes)
        return self.client.post("/api/turn", json=payload, headers=HEADERS)

    def test_explicit_confirmation_saves_recap_without_model_calls_or_new_candidates(self):
        result = self.wrapup()
        self.assertEqual(result.status_code, 200)
        data = result.get_json()
        self.assertEqual(data["response"]["recommendations"], [])
        self.assertEqual(data["response"]["recommendation_action"], "complete")
        self.assertEqual(data["snapshot"]["estimate"]["state"]["readiness"]["status"], "confirmed")
        self.assertEqual(data["snapshot"]["estimate"]["state"]["difficulty_history"], self.state["difficulty_history"])
        self.assertEqual(data["snapshot"]["estimate"]["provenance"]["model_calls"], 0)
        self.assertEqual(self.store.estimate(self.sid, "seed")["state"]["readiness"]["status"], "ready")
        self.ws.llm.generate.assert_not_called()
        self.ws.search.search.assert_not_called()
        self.assertEqual(self.client.get("/api/workspace").get_json()["estimate"]["state"]["readiness"]["status"], "confirmed")
        export = self.store.export(self.sid)
        self.assertEqual(len(export["estimate_history"]), 2)

    def test_same_request_is_idempotent_and_new_confirmation_is_rejected(self):
        self.assertEqual(self.wrapup().status_code, 200)
        self.assertEqual(self.wrapup().status_code, 200)
        self.assertEqual(self.wrapup(request_id="second-confirm").status_code, 400)
        self.assertEqual(sum(e["type"] == "session_wrapup" for e in self.store.events(self.sid)), 1)

    def test_forged_signature_cross_session_and_unoffered_state_are_rejected(self):
        self.assertEqual(self.wrapup(readiness_signature="other").status_code, 400)
        other = self.app.test_client()
        self.assertEqual(other.post("/api/turn", json={"request_id": "other", "action": "wrapup",
            "readiness_signature": self.offer["signature"]}, headers=HEADERS).status_code, 400)
        self.state["readiness"]["offered_signatures"] = []
        self.store.put_estimate(self.sid, "seed", self.follow["id"], self.state, self.plan, {})
        self.assertEqual(self.wrapup().status_code, 400)

    def test_unestimated_new_intent_invalidates_the_old_offer(self):
        self.store.event(self.sid, "user_input", {"text": "다른 주제로 바꿀래"}, "unestimated")
        self.assertEqual(self.wrapup().status_code, 400)
        self.assertFalse(any(e["type"] == "session_wrapup" for e in self.store.events(self.sid)))

    def test_session_can_continue_after_wrapup_without_repeating_the_offer(self):
        self.assertEqual(self.wrapup().status_code, 200)
        calls = []
        def generate(instruction, context, schema="response"):
            calls.append(schema)
            if schema == "estimate":
                return raw_state(self.first["id"], context["events"][-1]["id"])
            self.assertTrue(any(m["content"].get("recap") for m in context["messages"] if m["role"] == "assistant"))
            return {"assistance": {"title": "", "body": "", "paper_ids": []},
                    "recommendations": [{"title": f"관점 {i}", "description": "현재 문제의 다른 관점", "search_query": "academic exploration", "rationale": "문제 연결", "relation": "perspective"} for i in range(3)],
                    "next_step": "현재 관점의 차이를 살펴보세요.", "next_step_reason": "연구 문제를 이어서 검토할 수 있어요."}
        self.ws.llm.generate = generate
        result = self.client.post("/api/turn", json={"request_id": "continue", "text": "이 방향에서 계속 탐색할래"}, headers=HEADERS)
        self.assertEqual(result.status_code, 200)
        data = result.get_json()
        self.assertEqual(calls, ["estimate", "response"])
        self.assertEqual(len(data["response"]["recommendations"]), 3)
        self.assertNotIn("wrapup_offer", data["response"])
        self.assertEqual(data["snapshot"]["estimate"]["state"]["readiness"]["status"], "ready")
        self.assertEqual(self.wrapup(request_id="repeat-after-continuing").status_code, 400)

    def test_offer_is_committed_only_after_successful_normal_generation(self):
        self.state["readiness"]["offered_signatures"] = []
        self.store.put_estimate(self.sid, "seed", self.follow["id"], self.state, self.plan, {})
        def generate(instruction, context, schema="response"):
            if schema == "estimate":
                return raw_state(self.first["id"], context["events"][-1]["id"])
            raise ModelError("일시적인 실패")
        self.ws.llm.generate = generate
        payload = {"request_id": "failed", "text": "이 연구 문제를 구체화해줘"}
        result = self.client.post("/api/turn", json=payload, headers=HEADERS)
        self.assertEqual(result.status_code, 502)
        self.assertEqual(result.get_json()["snapshot"]["estimate"]["state"]["readiness"]["offered_signatures"], [])
        self.ws.llm.generate = Mock(return_value={"assistance": {"title": "주제 구체화", "body": "설명 문구의 차이에 따른 행동을 살펴볼 수 있어요.", "paper_ids": []},
            "recommendations": [{"title": f"관점 {i}", "description": "연구 문제 검토", "search_query": "research exploration", "relation": "scope"} for i in range(3)],
            "next_step": "문구에 따른 차이를 살펴보세요.", "next_step_reason": "현재 문제의 범위를 검토할 수 있어요."})
        retried = self.client.post("/api/turn", json=payload, headers=HEADERS)
        self.assertEqual(retried.status_code, 200)
        self.assertIn("wrapup_offer", retried.get_json()["response"])
        self.assertEqual(self.ws.llm.generate.call_count, 1)
