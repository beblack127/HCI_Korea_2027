"""Researcher-controlled support policy; the model cannot override this table."""
from .difficulty import pending_difficulties


POLICY_VERSION = "11.11-v4"


def select_support(state, latest_action="", has_candidates=False, initial=False):
    request = state.get("support_request", "directions")
    if latest_action in {"browse", "refine"}:
        mode = "formulate" if latest_action == "refine" else "browse"
        reason, evidence_ids = "이번에 선택한 둘러보기·구체화하기를 우선합니다.", []
    elif request != "directions":
        mode, reason = request, "사용자가 이번 입력에서 요청한 지원을 우선합니다."
        evidence_ids = state.get("support_request_evidence_ids", [])
    elif state.get("difficulties"):
        d = sorted(state["difficulties"], key=lambda x: x.get("status") != "expressed")[0]
        mode = {"concept": "concept", "connection": "connection", "selection": "compare", "formulation": "formulate", "unspecified": "orient"}[d["type"]]
        reason, evidence_ids = d["description"], d["evidence_ids"]
    else:
        mode, evidence_ids = "directions", []
        reason = "현재 연구 문제 안의 관점과 범위를 구체화합니다." if state.get("direction_stage") == "forming" else "구분되는 연구 문제의 관점을 살펴볼 수 있도록 제안합니다."
    narrowing = (state.get("exploration_progress") or {}).get("status") == "narrowing"
    breadth = "specific" if state.get("direction_stage") == "forming" or narrowing else "broad"
    if latest_action == "browse":
        breadth = "broad"
    elif latest_action == "refine":
        breadth = "specific"
    # Always offer a way to continue. The request changes the focus, not visibility.
    focus = {"browse": "selected_direction", "concept": "current_concept", "connection": "current_connection", "compare": "existing_candidates",
             "literature": "current_literature", "formulate": "research_problem"}.get(mode, "direction_options")
    concept_pending = mode == "concept" and state.get("recommendation_intent") != "generate" and any(
        h.get("type") == "concept" and h.get("status") == "expressed" and h.get("lifecycle") in {"active", "unconfirmed"}
        for h in state.get("difficulty_history", []))
    targets = sorted(state.get("difficulties", []), key=lambda d: d.get("status") != "expressed") or list(reversed(pending_difficulties(state)))
    target = targets[0] if targets else None
    adjustment = ({"type": target["type"], "source": "pending" if target.get("lifecycle") == "unconfirmed" else target.get("source", "utterance"),
                   "status": target.get("status"), "evidence_ids": target.get("evidence_ids", []),
                   "evidence_quotes": target.get("evidence_quotes", []), "signals": target.get("recent_signals", target.get("signals", []))} if target else None)
    return {"version": POLICY_VERSION, "mode": mode, "reason": reason, "evidence_ids": evidence_ids,
            "breadth": breadth, "retrieve_literature": mode == "literature",
            "recommendation_action": "generate", "recommendation_focus": focus, "has_candidates": has_candidates,
            "recommendation_count": 3,
            "concept_difficulty_pending": concept_pending,
            "commitment_requested": state.get("commitment_requested", False),
            "detail_requested": state.get("detail_requested", False),
            "support_adjustment": adjustment, "system_support_issues": state.get("support_issues", []),
            "focus_correction": state.get("focus_update") if state.get("correction_requested") else None,
            "correction_requested": bool(state.get("correction_requested")),
            "assistance_required": mode != "directions" or bool(state.get("correction_requested")) or bool(adjustment and state.get("recommendation_intent") != "generate")}


def default_next_step(plan):
    if plan.get("concept_difficulty_pending"):
        return ("이번 설명의 예시에서 이해하기 어려웠던 개념이 어떻게 쓰이는지 살펴보세요.",
                "이해하기 어려웠던 부분을 중심으로 현재 개념과 관심의 연결을 확인할 수 있습니다.")
    steps = {"orient": ("지금까지 살펴본 방향에서 남은 검토 지점을 확인해 보세요.", "새 주제를 늘리기 전에 현재 탐색의 흐름을 정리할 수 있어요."),
             "browse": ("선택한 주제에서 각 후보가 다루는 문제와 상황의 차이를 살펴보세요.", "주제를 확정하지 않고도 어떤 측면을 더 알아볼지 검토할 수 있습니다."),
             "concept": ("이 설명을 기준으로 현재 관심에서 확인하려는 개념을 살펴보세요.", "이해를 확인한 뒤 기존 탐색을 이어갈 수 있습니다."),
             "connection": ("현재 관심과 후보가 다루는 문제의 연결을 확인해 보세요.", "관련성을 기준으로 기존 방향을 검토할 수 있습니다."),
             "compare": ("후보가 다루는 연구 문제의 차이를 비교해 보세요.", "후보 사이의 차이를 확인하며 선택 기준을 정할 수 있습니다."),
             "formulate": ("현재 관심에서 다루고 싶은 연구 문제의 범위를 살펴보세요.", "기술 방법보다 무엇을 연구할지 구분하는 데 도움이 됩니다."),
             "literature": ("제공된 문헌의 초록에서 현재 관심과 연결되는 문제를 살펴보세요.", "초록에서 확인한 범위 안에서 연구 방향을 검토할 수 있습니다.")}
    return steps.get(plan["mode"], ("후보들이 어떤 연구 문제를 다루는지 비교해 보세요.", "서로 다른 관점을 구분하며 다음 탐색 방향을 정할 수 있습니다."))
