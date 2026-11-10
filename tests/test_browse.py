"""Regression cases for the observed empty/repeating browse responses."""
import copy
import tempfile
import unittest
from unittest.mock import Mock

from app import create_app
from core.browse import browse_response_issues
from core.config import Settings
from core.llm import ModelError
from core.policy import select_support
from core.service import clean_response
from core.state import empty_state

HEADERS = {"X-Workspace-Request": "1"}


def candidates(titles):
    return [{"id": f"candidate-{i}", "title": title, "description": f"{title}에서 다룰 문제를 살펴봅니다.",
             "rationale": "현재 선택한 주제와 연결되는 측면입니다.", "search_query": f"adaptive agents aspect {i}",
             "relation": "perspective"} for i, title in enumerate(titles)]


OLD = candidates(["개인화된 추론 성능 강화", "사용자 상태 인식 아키텍처", "도메인 특화 적응 전략"])
NEW = candidates(["상태를 파악할 단서", "상태 변화의 반영", "잘못된 추정의 수정"])


def reply(recs=None, body=None):
    return {"assistance": {"title": "주제 둘러보기", "paper_ids": [],
             "body": body if body is not None else "대화와 선택을 단서로 상태를 추정하고 지원을 조정하는 접근입니다. 예를 들어 개념 설명과 후보 비교가 필요한 상황을 구분해 도울 수 있습니다."},
            "recommendations": copy.deepcopy(NEW if recs is None else recs),
            "next_step": "각 측면이 다루는 문제와 상황의 차이를 살펴보세요.",
            "next_step_reason": "선택한 주제가 무엇을 다루는지 알아볼 수 있습니다."}


class BrowseChecksTests(unittest.TestCase):
    def test_browse_requires_explanation_without_narrowing_or_changing_state(self):
        state = dict(empty_state(), direction_stage="forming", support_request="concept")
        plan = select_support(state, "browse")
        self.assertEqual(plan["mode"], "browse")
        self.assertEqual(plan["recommendation_focus"], "selected_direction")
        self.assertEqual(plan["breadth"], "broad")
        self.assertTrue(plan["assistance_required"])
        self.assertFalse(plan["retrieve_literature"])
        self.assertEqual(state["direction_stage"], "forming")
        self.assertEqual(select_support(state, "refine")["mode"], "formulate")
        with self.assertRaises(ModelError):
            clean_response(reply(body=""), [], plan)

    def test_changed_ids_order_spacing_and_punctuation_do_not_hide_repeated_set(self):
        repeated = copy.deepcopy(OLD[::-1])
        for r in repeated:
            r.update(id="new-id", title=" 「" + r["title"].replace(" ", "  ") + "」 ")
        self.assertIn("repeated_candidate_set", browse_response_issues(reply(repeated), OLD))

    def test_relabelling_without_new_descriptions_is_detected(self):
        relabelled = copy.deepcopy(OLD)
        for i, r in enumerate(relabelled):
            r["title"] = f"새 제목 {i}"
        self.assertIn("repeated_descriptions", browse_response_issues(reply(relabelled), OLD))

    def test_new_facets_pass_but_missing_or_duplicate_candidates_do_not(self):
        self.assertEqual(browse_response_issues(reply(), OLD), [])
        self.assertIn("need_three_candidates", browse_response_issues(reply(NEW[:2]), OLD))
        self.assertIn("duplicate_or_empty_titles", browse_response_issues(reply([NEW[0]] * 3), OLD))
        broken = copy.deepcopy(NEW)
        broken[0]["search_query"] = ""
        self.assertIn("missing_candidate_details", browse_response_issues(reply(broken), OLD))


class BrowsePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(Settings(data_dir=self.temp.name, api_key="fake-only", mode="auto"))
        self.app.testing = True
        self.client = self.app.test_client()
        self.ws = self.app.extensions["workspace"]
        self.store = self.app.extensions["store"]
        self.sid = self.client.get("/api/workspace").get_json()["session"]["id"]
        self.store.event(self.sid, "user_input", {"text": "사용자 적응형, 맞춤형 LLM 에이전트"}, "seed:input")
        self.store.add_message(self.sid, "seed:assistant", "assistant", {"recommendations": OLD})
        self.calls = []
        self.ws.search.search = Mock(side_effect=AssertionError("browse must not retrieve papers"))

    def tearDown(self):
        self.temp.cleanup()

    def use_responses(self, *responses):
        queue = iter(responses)

        def generate(instruction, context, schema="response"):
            self.calls.append((schema, copy.deepcopy(context)))
            if schema == "estimate":
                return dict(empty_state(), current_direction=OLD[1]["title"], direction_stage="exploring")
            value = next(queue)
            if isinstance(value, Exception):
                raise value
            return copy.deepcopy(value)

        self.ws.llm.generate = Mock(side_effect=generate)

    def browse(self):
        return self.client.post("/api/turn", json={"request_id": "browse-test", "action": "browse",
                                "recommendation_id": OLD[1]["id"]}, headers=HEADERS)

    def test_success_adds_explanation_and_facets_with_only_two_model_calls(self):
        self.use_responses(reply())
        result = self.browse()
        self.assertEqual(result.status_code, 200)
        data = result.get_json()
        self.assertEqual([c[0] for c in self.calls], ["estimate", "response"])
        context = self.calls[1][1]
        self.assertEqual(context["selected_direction"]["id"], OLD[1]["id"])
        self.assertNotIn("response_validation", context)
        self.assertEqual(data["response"]["support_mode"], "browse")
        self.assertTrue(data["response"]["assistance"]["body"])
        self.assertEqual(len(data["response"]["recommendations"]), 3)
        state = data["snapshot"]["estimate"]["state"]
        self.assertNotEqual(state["direction_stage"], "forming")
        self.assertFalse(state["research_problem"])
        self.assertEqual(state["observed_directions"][0]["title"], OLD[1]["title"])
        self.assertEqual(state["next_step_reason"], data["response"]["next_step_reason"])
        self.assertFalse(state["difficulties"])
        self.ws.search.search.assert_not_called()

    def test_repeated_set_repairs_response_once_without_reestimating_or_double_logging(self):
        self.use_responses(reply(OLD), reply())
        result = self.browse()
        self.assertEqual(result.status_code, 200)
        self.assertEqual([c[0] for c in self.calls], ["estimate", "response", "response"])
        feedback = self.calls[2][1]["response_validation"]
        self.assertIn("repeated_candidate_set", feedback["issues"])
        data = result.get_json()
        trace = data["snapshot"]["estimate"]["provenance"]["browse_validation"]
        self.assertEqual(trace["attempts"], 2)
        self.assertEqual(trace["status"], "passed")
        self.assertEqual(sum(e["type"] == "topic_browse" for e in data["snapshot"]["events"]), 1)
        self.assertEqual(sum(m["role"] == "user" for m in data["snapshot"]["messages"]), 1)
        before = len(self.calls)
        self.assertEqual(self.browse().status_code, 200)
        self.assertEqual(len(self.calls), before)

    def test_empty_explanation_is_repaired_instead_of_showing_another_empty_browse(self):
        self.use_responses(reply(body=""), reply())
        result = self.browse()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(result.get_json()["response"]["assistance"]["body"])

    def test_persistent_repeat_stops_and_preserves_state_for_manual_retry(self):
        self.use_responses(reply(OLD), reply(OLD))
        result = self.browse()
        self.assertEqual(result.status_code, 502)
        data = result.get_json()
        self.assertIn("둘러보기", data["error"])
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(data["snapshot"]["estimate"]["state"]["response_status"], "error")
        self.assertEqual(data["snapshot"]["estimate"]["provenance"]["browse_validation"]["status"], "failed")
        self.assertEqual(len(data["snapshot"]["messages"]), 2)
        self.use_responses(reply())
        retried = self.browse()
        self.assertEqual(retried.status_code, 200)
        self.assertEqual([c[0] for c in self.calls], ["estimate", "response", "response", "response"])
        self.assertTrue(retried.get_json()["snapshot"]["estimate"]["provenance"]["estimate_reused"])

    def test_provider_failure_is_not_automatically_retried(self):
        self.use_responses(ModelError("Gemini 일시적 과부하", "provider_unavailable"))
        result = self.browse()
        self.assertEqual(result.status_code, 502)
        self.assertEqual([c[0] for c in self.calls], ["estimate", "response"])
        self.assertEqual(result.get_json()["snapshot"]["estimate"]["state"]["response_status"], "error")

    def test_comparison_can_keep_existing_candidates_without_browse_repairs(self):
        def generate(instruction, context, schema="response"):
            self.calls.append(schema)
            if schema == "estimate":
                latest = context["events"][-1]["id"]
                return dict(empty_state(), support_request="compare", support_request_evidence_ids=[latest])
            return reply(OLD)
        self.ws.llm.generate = generate
        result = self.client.post("/api/turn", json={"request_id": "compare-test", "text": "기존 후보 비교해줘"}, headers=HEADERS)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()["response"]["support_mode"], "compare")
        self.assertEqual(self.calls, ["estimate", "response"])

    def test_preview_browse_stays_explicitly_labelled_and_uses_selected_topic(self):
        self.ws.settings.live = False
        result = self.browse()
        self.assertEqual(result.status_code, 200)
        response = result.get_json()["response"]
        self.assertEqual(response["mode"], "preview")
        self.assertIn("아직 Gemini", response["assistance"]["body"])
        self.assertTrue(all(OLD[1]["title"] in r["title"] for r in response["recommendations"]))
