import copy
import hashlib
import json
import re
import threading
import time
import uuid
from .llm import ModelError, GeminiClient
from .policy import select_support, default_next_step, POLICY_VERSION
from .search import OpenAlex, SearchError
from .state import preview_state, validate_state, text, behavior_signals, OBSERVATIONS
from .presentation import compact, enrich_state, RELATION_LABELS
from .browse import browse_response_issues
from .readiness import assess_readiness, offer_wrapup, session_recap
from .behavior import topic_key, support_issues
from .difficulty import difficulty_view, pending_difficulties


def conversation_context(messages):
    """Keep conversational referents without resending old provenance and paper abstracts."""
    compact = []
    for m in messages[-8:]:
        c = m["content"]
        if m["role"] == "user":
            content = {"text": c.get("text", ""), "action": c.get("action", "message")}
        else:
            support = c.get("support") or {}
            content = {"answer": text(c.get("answer"), 2000), "state_summary": c.get("state_summary", ""),
                       "recommendations": c.get("recommendations", []),
                       "support": {"title": support.get("title", ""), "body": text(support.get("body"), 1800),
                                   "papers": [{"id": p["id"], "title": p["title"]} for p in support.get("papers", [])]}}
            if c.get("recap"):
                content["recap"] = c["recap"]
        compact.append({"role": m["role"], "content": content})
    return compact


def clean_response(raw, papers, plan=None):
    plan = plan or {"recommendation_count": 3, "assistance_required": True, "mode": "concept"}
    assistance = raw.get("assistance") or {}
    if not isinstance(assistance, dict):
        assistance = {}
    legacy = raw.get("support") or {}
    if not isinstance(legacy, dict):
        legacy = {}
    full_body = text(assistance.get("body") or raw.get("answer") or legacy.get("body"), 2500)
    body = compact(full_body, sentences=2, limit=320)
    if not plan.get("assistance_required"):
        body = ""
    supplied = {p["id"]: p for p in papers}
    verified_papers = [supplied[pid] for pid in assistance.get("paper_ids", legacy.get("paper_ids", []))
                       if isinstance(pid, str) and pid in supplied][:3]
    result = {"answer": body, "recommendations": [], "response_format": "11.10-v3",
              "assistance": {"title": text(assistance.get("title") or legacy.get("title"), 100) if body else "",
                             "body": body, "papers": verified_papers,
                             "details": full_body if body and full_body != body else "",
                             "expanded": bool(plan.get("detail_requested"))},
              "support": {"title": "", "body": "", "papers": verified_papers},
              "next_step": compact(raw.get("next_step"), limit=140), "next_step_reason": text(raw.get("next_step_reason"), 500)}
    for r in (raw.get("recommendations") or [])[:plan["recommendation_count"]]:
        if isinstance(r, dict) and text(r.get("title")):
            relation = r.get("relation")
            if relation not in RELATION_LABELS:
                relation = "concept" if plan["mode"] == "concept" else "scope" if plan["mode"] == "formulate" else "continuation"
            result["recommendations"].append({"id": str(uuid.uuid4()), "title": text(r["title"], 100),
                "description": compact(r.get("description"), limit=120), "rationale": text(r.get("rationale"), 350),
                "search_query": text(r.get("search_query"), 250), "relation": relation,
                "relation_label": RELATION_LABELS[relation]})
    for r in result["recommendations"]:
        r["topic_key"] = topic_key(r)
    if plan.get("assistance_required") and not body:
        raise ModelError("요청한 설명이 응답에 없습니다. 추정된 상태는 보관되어 있으니 다시 시도해 주세요.")
    if plan["recommendation_count"] and not result["recommendations"]:
        raise ModelError("요청한 탐색 후보가 응답에 없습니다. 추정된 상태는 보관되어 있으니 다시 시도해 주세요.")
    if not result["next_step"] or not result["next_step_reason"]:
        fallback = default_next_step(plan)
        result["next_step"] = result["next_step"] or fallback[0]
        result["next_step_reason"] = result["next_step_reason"] or fallback[1]
    if plan.get("concept_difficulty_pending"):
        result["next_step"], result["next_step_reason"] = default_next_step(plan)
    if not plan.get("commitment_requested") and re.search(r"확정|선택(?:하세요|해\s*보|하여|해서)|결정(?:하세요|해야|해\s*보)",
                                                          result["next_step"] + result["next_step_reason"]):
        result["next_step"], result["next_step_reason"] = default_next_step(plan)
    result["recommendation_action"] = "generate" if plan["recommendation_count"] else "preserve"
    return result


def preview_response(state, plan, context, papers):
    """Template demonstration explicitly labelled in the workspace, never fake literature."""
    first_input = next((e["payload"].get("text", "") for e in context.get("events", []) if e["type"] == "user_input"), "")
    root = text(first_input, 80) or text(state.get("current_direction"), 80) or "관심 주제"
    if plan["mode"] == "browse":
        root = text((context.get("selected_direction") or {}).get("title"), 80) or root
    raw = {"answer": "아직 Gemini가 연결되지 않아 아래는 탐색 흐름을 확인하기 위한 예시입니다. 실제 주제 추천과 설명은 API 연결 후 생성됩니다.",
        "recommendations": [
            {"title": f"{root}의 문제와 한계", "description": "이 분야에서 무엇이 해결되지 않았는지 살펴봅니다.", "rationale": "연구할 문제를 발견하는 관점의 예시입니다.", "search_query": root},
            {"title": f"{root}의 사용 맥락", "description": "어떤 상황에서 문제가 드러나는지 검토합니다.", "rationale": "연구 범위를 비교하는 관점의 예시입니다.", "search_query": root},
            {"title": f"{root}의 지원 방법", "description": "문제 해결에 쓰인 접근을 비교합니다.", "rationale": "접근 방법을 구체화하는 관점의 예시입니다.", "search_query": root}],
        "support": {"title": "", "body": "", "paper_ids": []},
        "next_step": "후보를 골라 둘러보거나, 다루고 싶은 문제를 키워드로 적어 보세요.",
        "next_step_reason": "연구 문제를 아직 확정하지 않아도 서로 다른 관점을 검토하며 시작할 수 있습니다."}
    if plan["breadth"] == "specific":
        raw["recommendations"] = [
            {"title": f"{root}: 다룰 문제 정리", "description": "선택한 방향에서 해결하고 싶은 문제가 무엇인지 구체화합니다.", "rationale": "구체화하기에 따른 지원 예시입니다.", "search_query": root},
            {"title": f"{root}: 연구 범위 비교", "description": "선택한 방향에서 어떤 범위와 관점을 유지할지 검토합니다.", "rationale": "구체화하기에 따른 지원 예시입니다.", "search_query": root},
            {"title": f"{root}: 접근 방법 검토", "description": "다루려는 문제를 어떤 방식으로 연구할지 살펴봅니다.", "rationale": "구체화하기에 따른 지원 예시입니다.", "search_query": root}]
    if plan["mode"] != "directions":
        raw["support"]["title"] = {"orient": "탐색 흐름 정리", "browse": "주제 둘러보기", "concept": "개념 설명", "connection": "관심과의 연결", "compare": "후보 비교", "formulate": "주제 구체화", "literature": "문헌 검토"}[plan["mode"]]
        raw["support"]["body"] = "해당 요청이 지원 방식 선택에 반영되었습니다. 내용의 실제 생성은 Gemini 연결 후 사용할 수 있습니다."
    if papers:
        raw["support"]["paper_ids"] = [p["id"] for p in papers[:3]]
    return clean_response(raw, papers, plan)


class Workspace:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.llm = GeminiClient(settings)
        self.search = OpenAlex(settings, store)
        self.locks, self.lock_guard = {}, threading.Lock()
        self.progress, self.progress_guard = {}, threading.Lock()

    def set_progress(self, sid, request_id, phase):
        now = time.monotonic()
        with self.progress_guard:
            # Progress is transient and session-scoped. Research records stay in SQLite.
            self.progress = {key: value for key, value in self.progress.items()
                             if now - value["updated"] < 3600}
            previous = self.progress.get((sid, request_id))
            if phase == "queued" and previous and previous.get("phase") in {"completed", "error"}:
                self.progress.pop((sid, request_id))
            value = self.progress.setdefault((sid, request_id), {"started": now, "timings": {}})
            old_phase = value.get("phase")
            if old_phase and old_phase != phase:
                value["timings"][old_phase] = round(now - value["updated"], 3)
            value.update(phase=phase, updated=now)

    def get_progress(self, sid, request_id):
        with self.progress_guard:
            p = self.progress.get((sid, request_id))
            if not p:
                return {"phase": "waiting", "elapsed_seconds": 0}
            end = p["updated"] if p["phase"] in {"completed", "error"} else time.monotonic()
            return {"phase": p["phase"], "elapsed_seconds": round(end - p["started"], 1)}

    def _lock(self, sid):
        with self.lock_guard:
            return self.locks.setdefault(sid, threading.Lock())

    def prompt(self, name):
        return (self.settings.root / "prompts" / (name + ".txt")).read_text(encoding="utf-8")

    def provenance(self):
        pieces = [self.prompt(n) for n in ("estimate", "respond", "query")]
        criteria = (self.settings.root / "config/state_criteria.json").read_text(encoding="utf-8")
        version_hash = hashlib.sha256("\n".join(pieces + [criteria]).encode()).hexdigest()[:16]
        versions = self.settings.data_dir / "prompt_versions"
        versions.mkdir(exist_ok=True)
        version_file = versions / (version_hash + ".json")
        if not version_file.exists():
            version_file.write_text(json.dumps({"criteria": json.loads(criteria),
                "prompts": dict(zip(("estimate", "respond", "query"), pieces))}, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"mode": "gemini" if self.settings.live else "preview", "model": self.settings.model if self.settings.live else "rule-preview",
                "api_style": self.settings.api_style, "thinking_level": self.settings.thinking_level,
                "criteria_version": json.loads(criteria)["version"], "policy_version": POLICY_VERSION,
                "prompt_hash": version_hash}

    def turn(self, sid, payload):
        request_id = str(payload.get("request_id") or "")[:100]
        if not request_id:
            raise ValueError("요청 식별자가 없습니다.")
        self.set_progress(sid, request_id, "queued")
        try:
            result = self._turn(sid, payload, request_id)
        except Exception:
            self.set_progress(sid, request_id, "error")
            raise
        self.set_progress(sid, request_id, "completed")
        return result

    def _turn(self, sid, payload, request_id):
        with self._lock(sid):
            cached = self.store.cached_turn(sid, request_id)
            if cached:
                return dict(cached, snapshot=self.store.snapshot(sid))
            message = text(payload.get("text"), 4000)
            action = payload.get("action", "message")
            if action == "wrapup":
                return self._wrapup(sid, payload, request_id)
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
            warnings, raw_state = [], None
            provenance = self.provenance()
            reusable = self.store.estimate(sid, request_id)
            self.set_progress(sid, request_id, "estimating")
            if reusable and reusable["through_event_id"] == through and reusable["provenance"].get("prompt_hash") == provenance["prompt_hash"]:
                state = reusable["state"]
                raw_state = reusable["provenance"].get("raw_estimate")
                warnings = reusable["provenance"].get("validation_warnings", [])
                provenance["estimate_reused"] = True
            elif self.settings.live:
                criteria = json.loads((self.settings.root / "config/state_criteria.json").read_text(encoding="utf-8"))
                raw_state = self.llm.generate(self.prompt("estimate"), dict(context, criteria=criteria), schema="estimate")
                state, warnings = validate_state(raw_state, selected, prior["state"] if prior else None, trace=observations)
            else:
                state = preview_state(observations, prior["state"] if prior else None)
            state = enrich_state(state, observations, prior["state"] if prior else None)
            state["readiness"] = assess_readiness(state, observations, prior["state"] if prior else None)
            has_candidates = any(m["role"] == "assistant" and m["content"].get("recommendations") for m in self.store.messages(sid))
            initial = len([e for e in selected if e["type"] == "user_input"]) == 1 and action == "message" and not has_candidates
            plan = select_support(state, action, has_candidates=has_candidates, initial=initial)
            state["next_step"], state["next_step_reason"] = default_next_step(plan)
            state["response_status"] = "generating"
            state.pop("response_error", None)
            provenance.update(validation_warnings=warnings, raw_estimate=raw_state, context_window=context["context_window"])
            # State is independently available to progress polling and survives retrieval/generation failures.
            self.store.put_estimate(sid, request_id, through, state, plan, provenance)
            self.store.event(sid, "state_estimated", {"request_id": request_id, "through_event_id": through}, request_id + ":estimate")
            try:
                return self._respond(sid, request_id, through, state, plan, provenance, context, action, display, candidate)
            except Exception as exc:
                state["response_status"] = "error"
                state["response_error"] = str(exc) if isinstance(exc, (ModelError, SearchError)) else "답변 생성을 마치지 못했습니다."
                self.store.put_estimate(sid, request_id, through, state, plan, provenance)
                raise

    def _wrapup(self, sid, payload, request_id):
        prior = self.store.latest_estimate(sid)
        readiness = (prior["state"].get("readiness") or {}) if prior else {}
        signature = payload.get("readiness_signature")
        pending_intent = prior and any(e["id"] > prior["through_event_id"] and e["type"] in {"user_input", "topic_browse", "topic_refine"}
                                       for e in self.store.events(sid))
        if (not signature or signature != readiness.get("signature") or readiness.get("status") != "ready"
                or signature not in readiness.get("offered_signatures", [])
                or signature in readiness.get("confirmed_signatures", [])
                or pending_intent
                or prior["state"].get("response_status") != "completed"):
            raise ValueError("현재 탐색에서 제안된 정리를 확인할 수 없습니다. 최신 탐색 내용을 확인해 주세요.")
        state = copy.deepcopy(prior["state"])
        self.set_progress(sid, request_id, "saving")
        event = self.store.event(sid, "session_wrapup", {"title": state["research_problem"],
            "readiness_signature": signature, "source_request_id": prior["request_id"]}, request_id + ":action")
        recap = session_recap(state)
        state["readiness"]["confirmed_signatures"] = (readiness.get("confirmed_signatures", []) + [signature])[-12:]
        state["readiness"].update(status="confirmed", summary=recap, confirmed_event_id=event["id"],
            message="사용자가 이번 탐색의 잠정 정리를 선택했어요.")
        state.update(response_status="completed", recent_change="이번 탐색에서 드러난 연구 방향을 잠정 정리했어요.",
                     change_evidence_ids=[event["id"]], chat_summary="이번 탐색에서 살펴본 연구 방향을 잠정 정리했어요.",
                     next_step=recap["next_checks"][0], next_step_reason="관련 문헌과 대조하며 현재 연구 방향을 더 검토할 수 있어요.")
        state.pop("response_error", None)
        state["recommendation_context"] = []
        plan = {"version": POLICY_VERSION, "mode": "wrapup", "recommendation_count": 0,
                "recommendation_action": "complete", "retrieve_literature": False}
        provenance = dict(self.provenance(), source_request_id=prior["request_id"], model_calls=0,
                          summary_method="validated_state_projection")
        self.store.put_estimate(sid, request_id, event["id"], state, plan, provenance)
        self.store.add_message(sid, request_id + ":user", "user", {"text": "이번 탐색 정리하기", "action": "wrapup", "request_id": request_id})
        response = {"response_format": "11.10-v3", "state_summary": state["chat_summary"], "support_mode": "wrapup",
                    "mode": "gemini" if self.settings.live else "preview", "difficulty_status": difficulty_view(state),
                    "difficulty_types": [d["type"] for d in (state["difficulties"] or pending_difficulties(state))], "answer": "",
                    "assistance": {"title": "", "body": "", "papers": []}, "recap": recap, "recommendations": [],
                    "recommendation_action": "complete", "next_step": state["next_step"], "next_step_reason": state["next_step_reason"],
                    "elapsed_seconds": self.get_progress(sid, request_id)["elapsed_seconds"]}
        self.store.add_message(sid, request_id + ":assistant", "assistant", response)
        self.store.event(sid, "agent_response", {"request_id": request_id, "support_mode": "wrapup",
            "state_summary": state["chat_summary"], "offered_options": [], "estimate_through_event_id": event["id"],
            "provenance": provenance}, request_id + ":response")
        result = {"response": response, "request_id": request_id}
        self.store.finish_turn(sid, request_id, result)
        return dict(result, snapshot=self.store.snapshot(sid))

    def _respond(self, sid, request_id, through, state, plan, provenance, context, action, display, candidate):
        papers, retrieval_error = [], ""
        if plan["retrieve_literature"]:
            self.set_progress(sid, request_id, "querying")
            if self.settings.live:
                query_result = self.llm.generate(self.prompt("query"), {"state": state, "latest_input": display}, schema="query")
                query = text(query_result.get("query"), 250)
            else:
                # No guessed translations or fabricated works in preview.
                query = state.get("current_direction") or display
            try:
                self.set_progress(sid, request_id, "retrieving")
                sr = self.search.search(query)
                papers = sr["papers"][:3]
                self.store.put_papers(sid, papers)
                self.store.event(sid, "support_retrieval", {"query": query, "paper_ids": [p["id"] for p in papers], "total": sr["total"]})
            except SearchError as exc:
                retrieval_error = str(exc)
                self.store.event(sid, "support_retrieval_error", {"message": retrieval_error})
        if self.settings.live:
            self.set_progress(sid, request_id, "generating")
            response_context = {"estimated_state": state, "support_plan": plan,
                "latest_action": action, "latest_input": display, "selected_direction": candidate,
                "available_directions": context["available_directions"],
                "messages": context["messages"], "supplied_papers": papers, "retrieval_error": retrieval_error}
            if plan["mode"] == "browse":
                checks = []
                for attempt in range(2):
                    # Provider failures propagate normally; only completed but invalid content is repaired.
                    raw_response = self.llm.generate(self.prompt("respond"), response_context)
                    try:
                        response = clean_response(raw_response, papers, plan)
                    except ModelError:
                        issues = ["invalid_response_content"]
                    else:
                        issues = browse_response_issues(response, context["available_directions"])
                    checks.append(issues)
                    provenance["browse_validation"] = {"attempts": attempt + 1, "issues_by_attempt": checks,
                        "status": "passed" if not issues else "repairing" if attempt == 0 else "failed"}
                    if not issues:
                        break
                    if attempt == 1:
                        raise ModelError("둘러보기 설명과 새로운 후보를 충분히 구성하지 못했습니다. 추정된 상태는 보관되어 있으니 다시 시도해 주세요.")
                    response_context = dict(response_context, response_validation={
                        "issues": issues,
                        "previous_proposal": raw_response,
                        "instruction": "선택한 주제의 문제와 상황 예시를 짧게 설명하고, 그 주제와 연결된 서로 다른 측면 3개를 새로 제안하세요. 직전 후보의 제목이나 설명을 그대로 재사용하지 마세요. 연구 문제의 확정이나 이해를 가정하지 마세요."})
            else:
                response = clean_response(self.llm.generate(self.prompt("respond"), response_context), papers, plan)
        else:
            response = preview_response(state, plan, context, papers)
        response.update({"state_summary": state.get("chat_summary", state["summary"]), "mode": "gemini" if self.settings.live else "preview",
                         "difficulty_status": difficulty_view(state),
                         "difficulty_types": [d["type"] for d in (state["difficulties"] or pending_difficulties(state))],
                         "support_mode": plan["mode"], "retrieval_error": retrieval_error})
        wrapup_offer = offer_wrapup(state, plan)
        if wrapup_offer:
            response["wrapup_offer"] = wrapup_offer
        if candidate:
            for r in response["recommendations"]:
                r.update(parent_topic_key=topic_key(candidate), parent_recommendation_id=candidate["id"])
        state["next_step"] = response["next_step"]
        state["next_step_reason"] = response["next_step_reason"]
        state["recommendation_context"] = [{k: r[k] for k in ("id", "title", "relation_label", "rationale")} for r in response["recommendations"]]
        state["response_status"] = "completed"
        for difficulty in state.get("difficulty_history", []):
            support_kind = {"concept": "concept", "connection": "connection", "compare": "selection", "formulate": "formulation", "orient": "unspecified"}.get(plan["mode"])
            adjustment = plan.get("support_adjustment") or {}
            if difficulty["lifecycle"] in {"active", "unconfirmed"} and difficulty["type"] in {support_kind, adjustment.get("type")}:
                difficulty.update(support_provided=True, support_request_id=request_id, support_mode=plan["mode"])
        self.set_progress(sid, request_id, "saving")
        with self.progress_guard:
            progress = self.progress[(sid, request_id)]
            timings = dict(progress["timings"])
            elapsed = round(time.monotonic() - progress["started"], 2)
        response["elapsed_seconds"] = elapsed
        provenance.update(phase_seconds=timings,
                          context_characters={"estimate": len(json.dumps(context, ensure_ascii=False)),
                                              "response": len(json.dumps(response_context, ensure_ascii=False)) if self.settings.live else 0})
        self.store.put_estimate(sid, request_id, through, state, plan, provenance)
        self.store.add_message(sid, request_id + ":assistant", "assistant", response)
        self.store.event(sid, "agent_response", {"request_id": request_id, "state_summary": response["state_summary"],
            "offered_options": response["recommendations"], "support_mode": plan["mode"],
            "assistance_excerpt": response.get("assistance", {}).get("body", ""),
            "estimate_through_event_id": through, "provenance": provenance}, request_id + ":response")
        result = {"response": response, "request_id": request_id}
        self.store.finish_turn(sid, request_id, result)
        return dict(result, snapshot=self.store.snapshot(sid))
