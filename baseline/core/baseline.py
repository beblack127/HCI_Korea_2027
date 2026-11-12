"""Local study control: raw conversation support, isolated from shadow estimates.

The shadow path is a write-only analysis sink. Only explicit requests/actions and
conversation content enter generation. Never pass an estimate or derived policy
back into a participant response, query, progress payload, or workspace snapshot.
"""
import copy
import hashlib
import json
import re
import time
from .service import Workspace, clean_response, conversation_context
from .storage import Store
from .state import text, validate_state, preview_state, behavior_signals, OBSERVATIONS
from .presentation import enrich_state
from .policy import select_support
from .readiness import assess_readiness
from .behavior import topic_key, support_issues
from .browse import browse_response_issues
from .llm import ModelError
from .search import SearchError

BASELINE_POLICY_VERSION = "baseline-11.11-v3"
SOURCE_COMMIT = "c033cb4307d05a1f72df25b1b5bd8e4510d57ad7"
# Explicit-request routing only, matching the proposed request detector.
LITERATURE_REQUEST = r"(?:논문|문헌).{0,15}(?:추천|찾|보여)|관련\s*논문"
DETAIL_REQUEST = r"자세히|자세하게|상세히|상세하게|꼼꼼히|단계별로"


def baseline_plan(action, latest_text):
    literature = action == "message" and bool(re.search(LITERATURE_REQUEST, latest_text))
    mode = {"browse": "browse", "refine": "formulate"}.get(action) or ("literature" if literature else "chat")
    return {"version": BASELINE_POLICY_VERSION, "condition": "baseline", "mode": mode,
            "breadth": "broad" if action == "browse" else "specific" if action == "refine" else "",
            "retrieve_literature": literature,
            "recommendation_action": "generate", "recommendation_count": 3,
            "detail_requested": bool(re.search(DETAIL_REQUEST, latest_text)) if action == "message" else False,
            "assistance_required": action in {"browse", "refine"} or literature,
            "assistance_optional": True, "next_step_enabled": False,
            "concept_difficulty_pending": False, "commitment_requested": True}


def response_messages(messages):
    # Same eight-message window/compaction. Drop even blank state-only keys.
    result = conversation_context(messages)
    for message in result:
        if message["role"] == "assistant":
            message["content"].pop("state_summary", None)
            message["content"].pop("recap", None)
    return result


class ClientSafeStore(Store):
    def snapshot(self, sid):
        snap = super().snapshot(sid)
        snap["estimate"] = None
        for message in snap["messages"]:
            if message["role"] == "assistant":
                for key in ("difficulty_status", "difficulty_types", "wrapup_offer", "recap"):
                    message["content"].pop(key, None)
                message["content"].update(state_summary="", next_step="", next_step_reason="")
        snap["events"] = [event for event in snap["events"]
                          if event["type"] not in {"state_estimated", "shadow_estimate_error"}]
        for event in snap["events"]:
            if event["type"] == "agent_response":
                event["payload"].pop("provenance", None)
        return snap

    def export(self, sid):
        # Researcher-only use: retain original estimates/messages/events, not the UI projection.
        result = super().export(sid)
        result.update(Store.snapshot(self, sid))
        result["condition"] = "baseline"
        return result


class BaselineWorkspace(Workspace):
    def provenance(self):
        names = ("estimate", "respond_baseline", "query")
        pieces = [self.prompt(n) for n in names]
        criteria = (self.settings.root / "config/state_criteria.json").read_text(encoding="utf-8")
        version_hash = hashlib.sha256("\n".join(pieces + [criteria]).encode()).hexdigest()[:16]
        versions = self.settings.data_dir / "prompt_versions"
        versions.mkdir(exist_ok=True)
        version_file = versions / (version_hash + ".json")
        if not version_file.exists():
            version_file.write_text(json.dumps({"criteria": json.loads(criteria),
                "prompts": dict(zip(names, pieces))}, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"mode": "gemini" if self.settings.live else "preview", "model": self.settings.model if self.settings.live else "rule-preview",
                "api_style": self.settings.api_style, "thinking_level": self.settings.thinking_level,
                "criteria_version": json.loads(criteria)["version"], "policy_version": BASELINE_POLICY_VERSION,
                "prompt_hash": version_hash, "condition": "baseline", "source_commit": SOURCE_COMMIT,
                "shadow_estimate": self.settings.shadow_estimate}

    def _turn(self, sid, payload, request_id):
        with self._lock(sid):
            cached = self.store.cached_turn(sid, request_id)
            if cached:
                return dict(cached, snapshot=self.store.snapshot(sid))
            message = text(payload.get("text"), 4000)
            action = payload.get("action", "message")
            candidate = None
            if action not in {"message", "browse", "refine"}:
                raise ValueError("지원하지 않는 탐색 행동입니다.")
            if action in {"browse", "refine"}:
                rid = payload.get("recommendation_id")
                recs = [r for m in self.store.messages(sid) if m["role"] == "assistant"
                        for r in m["content"].get("recommendations", [])]
                candidate = next((r for r in recs if r["id"] == rid), None)
                if not candidate:
                    raise ValueError("선택한 후보를 찾을 수 없습니다. 화면을 새로고침해 주세요.")
                self.store.event(sid, "topic_browse" if action == "browse" else "topic_refine",
                    {"recommendation_id": rid, "title": candidate["title"], "description": candidate["description"],
                     "search_query": candidate.get("search_query", ""), "topic_key": topic_key(candidate),
                     "parent_topic_key": candidate.get("parent_topic_key", ""), "relation": candidate.get("relation", "")}, request_id + ":action")
                display = candidate["title"] + (" · 둘러보기" if action == "browse" else " · 구체화하기")
            else:
                if not message:
                    raise ValueError("탐색할 주제나 질문을 입력해 주세요.")
                self.store.event(sid, "user_input", {"text": message}, request_id + ":input")
                display = message
            self.store.set_title(sid, display)
            self.store.add_message(sid, request_id + ":user", "user", {"text": display, "action": action, "request_id": request_id})
            observations = self.store.events(sid)
            through = max(e["id"] for e in observations if e["type"] in OBSERVATIONS)
            prior = self.store.latest_estimate(sid, exclude_request_id=request_id)
            # The entire session remains persisted. The prompt window is explicit and bounded.
            # agent_response contains raw estimates/provenance and is NOT an observation.
            # Exclude it from the prompt rather than duplicating every past model output.
            observed_events = [e for e in observations if e["type"] in OBSERVATIONS]
            selected = observed_events[-160:]
            initial_speech = next((e for e in observations if e["type"] == "user_input"), None)
            if initial_speech and initial_speech not in selected:
                selected = [initial_speech] + selected
            messages = self.store.messages(sid)
            available = next((m["content"]["recommendations"] for m in reversed(messages)
                              if m["role"] == "assistant" and m["content"].get("recommendations")), [])
            context = {"events": selected, "prior_estimate": prior["state"] if prior else None,
                       "behavior_signals": behavior_signals(observations), "support_issues": support_issues(observations),
                       "messages": conversation_context(messages), "available_directions": available, "latest_action": action,
                       "latest_input": display, "selected_direction": candidate,
                       "context_window": {"total_events": len(observations), "observed_events": len(observed_events),
                                          "included_events": len(selected), "message_window": 8}}
            paper_ids = list(dict.fromkeys(e["payload"]["paper_id"] for e in reversed(selected)
                                          if e["payload"].get("paper_id")))[:12]
            context["observed_papers"] = [dict(paper, abstract=paper.get("abstract", "")[:4000])
                                          for pid in paper_ids if (paper := self.store.paper(sid, pid))]
            self.set_progress(sid, request_id, "estimating")
            self._shadow_estimate(sid, request_id, through, observations, selected, context, prior, action)
            plan = baseline_plan(action, message if action == "message" else "")
            return self._respond(sid, request_id, through, plan, context, action, display, candidate)

    def _shadow_estimate(self, sid, request_id, through, observations, selected, context, prior, action):
        if self.settings.shadow_estimate == "off":
            return
        try:
            prov = self.provenance()
            reusable = self.store.estimate(sid, request_id)
            if (reusable and reusable["through_event_id"] == through
                    and reusable["provenance"].get("prompt_hash") == prov["prompt_hash"]):
                return
            warnings, raw = [], None
            prior_state = prior["state"] if prior else None
            if self.settings.live:
                criteria = json.loads((self.settings.root / "config/state_criteria.json").read_text(encoding="utf-8"))
                raw = self.llm.generate(self.prompt("estimate"), dict(context, criteria=criteria), schema="estimate")
                state, warnings = validate_state(raw, selected, prior_state, trace=observations)
            else:
                state = preview_state(observations, prior_state)
            state = enrich_state(state, observations, prior_state)
            state["readiness"] = assess_readiness(state, observations, prior_state)
            has_candidates = bool(context["available_directions"])
            initial = len([e for e in selected if e["type"] == "user_input"]) == 1 and action == "message" and not has_candidates
            counterfactual = select_support(state, action, has_candidates=has_candidates, initial=initial)
            state["response_status"] = "shadow"
            plan_record = dict(counterfactual, shadow=True, used_for_response=False)
            prov.update(shadow=True, used_for_response=False, validation_warnings=warnings,
                        raw_estimate=raw, context_window=context["context_window"])
            self.store.put_estimate(sid, request_id, through, state, plan_record, prov)
            self.store.event(sid, "state_estimated", {"request_id": request_id, "through_event_id": through, "shadow": True},
                             request_id + ":estimate")
        except Exception as exc:
            self.store.event(sid, "shadow_estimate_error", {"request_id": request_id, "message": str(exc)[:300],
                             "code": getattr(exc, "code", "")}, request_id + ":shadow-error")
        # Deliberately no return value: analysis never feeds the response path.

    def _respond(self, sid, request_id, through, plan, context, action, display, candidate):
        provenance = self.provenance()
        messages = response_messages(self.store.messages(sid))
        papers, retrieval_error = [], ""
        if plan["retrieve_literature"]:
            self.set_progress(sid, request_id, "querying")
            if self.settings.live:
                query_result = self.llm.generate(self.prompt("query"), {"latest_input": display,
                    "messages": messages[-4:]}, schema="query")
                query = text(query_result.get("query"), 250)
            else:
                query = display
            try:
                self.set_progress(sid, request_id, "retrieving")
                result = self.search.search(query)
                papers = result["papers"][:3]
                self.store.put_papers(sid, papers)
                self.store.event(sid, "support_retrieval", {"query": query, "paper_ids": [p["id"] for p in papers], "total": result["total"]})
            except SearchError as exc:
                retrieval_error = str(exc)
                self.store.event(sid, "support_retrieval_error", {"message": retrieval_error})
        self.set_progress(sid, request_id, "generating")
        response_context = {"latest_action": action, "latest_input": display, "selected_direction": candidate,
            "available_directions": context["available_directions"], "messages": messages,
            "supplied_papers": papers, "retrieval_error": retrieval_error,
            "request": {"breadth": plan["breadth"], "detail_requested": plan["detail_requested"],
                        "literature_requested": plan["retrieve_literature"]}}
        if self.settings.live:
            checks = []
            for attempt in range(2 if plan["mode"] == "browse" else 1):
                raw_response = self.llm.generate(self.prompt("respond_baseline"), response_context, schema="baseline_response")
                try:
                    response = clean_response(raw_response, papers, plan)
                except ModelError as exc:
                    if plan["mode"] != "browse":
                        raise ModelError(str(exc).replace("추정된 상태는 보관되어 있으니", "입력은 보관되어 있으니"), exc.code) from exc
                    issues = ["invalid_response_content"]
                else:
                    issues = browse_response_issues(response, context["available_directions"]) if plan["mode"] == "browse" else []
                if plan["mode"] == "browse":
                    checks.append(issues)
                    provenance["browse_validation"] = {"attempts": attempt + 1, "issues_by_attempt": checks,
                        "status": "passed" if not issues else "repairing" if attempt == 0 else "failed"}
                if not issues:
                    break
                if attempt == 1:
                    raise ModelError("둘러보기 설명과 새로운 후보를 충분히 구성하지 못했습니다. 입력은 보관되어 있으니 다시 시도해 주세요.")
                response_context = dict(response_context, response_validation={
                    "issues": issues, "previous_proposal": raw_response,
                    "instruction": "선택한 주제의 문제와 상황 예시를 짧게 설명하고, 그 주제와 연결된 서로 다른 측면 3개를 새로 제안하세요. 직전 후보의 제목이나 설명을 그대로 재사용하지 마세요. 연구 문제의 확정이나 이해를 가정하지 마세요."})
        else:
            response = baseline_preview(plan, context, papers)
        response.update(state_summary="", mode="gemini" if self.settings.live else "preview",
                        difficulty_status="unknown", difficulty_types=[], support_mode=plan["mode"],
                        retrieval_error=retrieval_error, condition="baseline")
        if candidate:
            for rec in response["recommendations"]:
                rec.update(parent_topic_key=topic_key(candidate), parent_recommendation_id=candidate["id"])
        self.set_progress(sid, request_id, "saving")
        with self.progress_guard:
            progress = self.progress[(sid, request_id)]
            timings = dict(progress["timings"])
            elapsed = round(time.monotonic() - progress["started"], 2)
        response["elapsed_seconds"] = elapsed
        provenance.update(phase_seconds=timings,
                          context_characters={"estimate": len(json.dumps(context, ensure_ascii=False)),
                                              "response": len(json.dumps(response_context, ensure_ascii=False))})
        # No estimate update: baseline never marks an inferred difficulty as supported/resolved.
        self.store.add_message(sid, request_id + ":assistant", "assistant", response)
        self.store.event(sid, "agent_response", {"request_id": request_id, "state_summary": "",
            "offered_options": response["recommendations"], "support_mode": plan["mode"],
            "assistance_excerpt": response.get("assistance", {}).get("body", ""),
            "estimate_through_event_id": through, "provenance": dict(provenance, response_plan=plan)}, request_id + ":response")
        result = {"response": response, "request_id": request_id}
        self.store.finish_turn(sid, request_id, result)
        return dict(result, snapshot=self.store.snapshot(sid))


def baseline_preview(plan, context, papers):
    """Layout demonstration only; never fabricates literature or estimates."""
    root = text((context.get("selected_direction") or {}).get("title"), 80) or text(context["latest_input"], 80)
    labels = ("문제와 한계", "사용 맥락", "지원 방법")
    if plan["breadth"] == "specific":
        labels = ("다룰 문제 정리", "연구 범위 비교", "접근 방법 검토")
    raw = {"assistance": {"title": "답변 예시", "body": "아직 Gemini가 연결되지 않아 동작 확인용 예시를 표시합니다. 실제 설명과 주제 추천은 API 연결 후 생성됩니다.",
                           "paper_ids": [p["id"] for p in papers[:3]]},
           "recommendations": [{"title": f"{root}: {label}", "description": f"선택한 주제의 {label}에 해당하는 측면을 살펴봅니다.",
                "rationale": "탐색을 이어가는 후보 예시입니다.", "search_query": root, "relation": "continuation"} for label in labels]}
    return clean_response(raw, papers, plan)
