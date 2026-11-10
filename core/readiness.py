"""Evidence-bound readiness to review a session, separate from cognitive stage."""
import hashlib
import json
from .state import text


def _problem_ids(state, sources):
    return sorted({r["event_id"] for r in state.get("problem_evidence", [])
                   if r.get("event_id") in sources and r.get("quote")
                   and r["quote"] in sources[r["event_id"]]["payload"].get("text", "")})


def assess_readiness(state, events, previous=None):
    previous = previous or {}
    boundary = state.get("direction_context_start_event_id", 0)
    scoped = [e for e in events if e["id"] >= boundary]
    sources = {e["id"]: e for e in scoped if e["type"] == "user_input"}
    problem_ids = _problem_ids(state, sources)
    problem = text(state.get("research_problem"), 350)
    aspects = [a for a in state.get("clarified_aspects", [])
               if a.get("kind") in {"perspective", "scope"} and len(a.get("text", "")) >= 4
               and any(i in sources and a["text"] in sources[i]["payload"].get("text", "")
                       for i in a.get("evidence_ids", []))]
    intents = [e for e in scoped if e["type"] in {"user_input", "topic_browse", "topic_refine"}]
    latest = intents[-1]["id"] if intents else 0
    prior_latest = max(previous.get("change_evidence_ids", []) or _problem_ids(previous, sources) or [0])
    continuity = (state.get("topic_relation") == "same" and bool(previous.get("research_problem"))
                  and bool(_problem_ids(previous, sources)) and latest > prior_latest > 0)
    checks = [
        {"key": "problem", "label": "연구할 문제", "met": bool(problem and problem_ids), "evidence_ids": problem_ids},
        {"key": "specificity", "label": "구별되는 관점·범위", "met": bool(aspects),
         "evidence_ids": sorted({i for a in aspects for i in a["evidence_ids"] if i in sources})},
        {"key": "continuity", "label": "같은 방향으로 이어진 탐색", "met": bool(continuity),
         "evidence_ids": sorted({prior_latest, latest} - {0}) if continuity else []}]
    ready = all(c["met"] for c in checks) and state.get("direction_stage") == "forming"
    signature = hashlib.sha256(json.dumps([boundary, problem, sorted((a["kind"], a["text"]) for a in aspects)],
                                         ensure_ascii=False).encode()).hexdigest()[:20] if ready else ""
    offered = list((previous.get("readiness") or {}).get("offered_signatures", []))[-12:]
    confirmed = list((previous.get("readiness") or {}).get("confirmed_signatures", []))[-12:]
    return {"version": "11.11-v2", "status": "ready" if ready else "not_ready", "signature": signature,
            "checks": checks, "offered_signatures": offered, "confirmed_signatures": confirmed,
            "message": ("연구할 문제와 관점·범위가 드러나고 같은 방향으로 탐색이 이어졌어요. 이번 탐색을 잠정 연구 방향으로 정리할 수 있어요."
                        if ready else "현재 기록에서 정리할 문제·관점·탐색의 연속성을 함께 확인하고 있어요.")}


def offer_wrapup(state, plan):
    readiness = state.get("readiness", {})
    if (readiness.get("status") != "ready" or not readiness.get("signature")
            or readiness["signature"] in readiness.get("offered_signatures", [])
            or readiness["signature"] in readiness.get("confirmed_signatures", [])
            or plan["mode"] not in {"directions", "browse", "formulate"}
            or any(d.get("status") == "expressed" for d in state.get("difficulties", []))):
        return None
    readiness["offered_signatures"] = (readiness.get("offered_signatures", []) + [readiness["signature"]])[-12:]
    return {"signature": readiness["signature"], "message": readiness["message"]}


def session_recap(state):
    """Project verified content; do not invent a question, method, or completion claim."""
    return {"title": "이번 탐색의 잠정 연구 방향", "direction": state.get("current_direction", ""),
            "research_problem": state.get("research_problem", ""),
            "aspects": [{"kind": a["kind"], "text": a["text"]} for a in state.get("clarified_aspects", [])
                        if a.get("kind") in {"scope", "perspective"}],
            "reviewed_directions": [{"title": d["title"], "action": d["action"]}
                                    for d in state.get("observed_directions", [])],
            "remaining_questions": state.get("open_questions", state.get("unresolved", []))[:3],
            "next_checks": ["관련 문헌에서 현재 문제와 기존 연구의 차이를 확인해 보세요.",
                            "이 방향에서 활용할 자료와 평가 방법을 검토해 보세요."],
            "note": "이번 세션에서 드러난 내용을 정리했어요. 원하면 입력창에서 탐색을 이어갈 수 있어요."}
