"""Different views of one validated state, without another model inference."""
import re
from .difficulty import LABELS, pending_difficulties
from .behavior import topic_steps
from .progress import enrich_progress

RELATION_LABELS = {"problem": "문제 살펴보기", "perspective": "관점 더하기", "scope": "범위 좁히기",
                   "concept": "개념 연결", "continuation": "현재 방향 이어가기"}


def compact(value, sentences=1, limit=150):
    value = str(value or "").strip()
    # Preserve the complete version elsewhere when a display excerpt is shortened.
    parts = re.split(r"(?<=[.!?。？！])\s+|\n+", value)
    excerpt = " ".join(p.strip() for p in parts[:sentences] if p.strip())
    if len(excerpt) > limit:
        excerpt = excerpt[:limit - 1].rstrip() + "…"
    return excerpt


def chat_summary(state):
    topic = compact(state.get("research_problem") or state.get("current_direction"), limit=70)
    active = state.get("difficulties") or []
    pending = pending_difficulties(state)
    progress = state.get("exploration_progress") or {}
    expressed = next((d for d in active if d.get("status") == "expressed"), None)
    labels = LABELS
    if progress.get("status") in {"reviewing", "narrowing", "revising"}:
        focus = compact(progress.get("focus"), limit=65)
        if progress["status"] == "revising":
            summary = f"최근 정정을 반영해 ‘{focus}’ 쪽으로 검토 관점을 조정하고 있어요."
        elif progress["status"] == "narrowing":
            summary = f"최근 ‘{focus}’ 안의 문제·범위를 좁혀 검토하고 있어요."
        else:
            summary = f"최근 ‘{focus}’와 관련된 관점을 검토하고 있어요."
        if expressed:
            label = labels.get(expressed["type"], "탐색")
            summary += (" 어려움을 표현했어요." if expressed["type"] == "unspecified"
                        else f" {label}의 어려움을 표현했어요.")
        elif active:
            label = labels.get(active[0]["type"], "탐색")
            summary += f" {label} 도움이 필요할 수 있다는 흐름이 보여요."
        elif pending:
            label = labels.get(pending[-1]["type"], "탐색")
            summary += f" 이전 {label} 어려움의 후속 변화를 살펴보고 있어요."
        return summary
    if expressed:
        if expressed["type"] == "unspecified":
            return "탐색에서 어려움을 표현했어요. 어떤 유형인지는 아직 판단하지 않았어요."
        return f"‘{topic}’을 살펴보며 {labels[expressed['type']]}의 어려움을 표현했어요." if topic else f"{labels[expressed['type']]}의 어려움을 표현했어요."
    if active:
        return f"활동 흐름에서 {labels.get(active[0]['type'], '탐색')} 지원이 필요할 가능성이 있어요."
    if pending:
        label = labels.get(pending[-1]["type"], "탐색")
        return f"이전에 표현한 {label} 어려움의 후속 변화를 살펴보고 있어요."
    if state.get("direction_stage") == "forming" and topic:
        return f"‘{topic}’에 관한 연구 문제의 범위를 구체화하고 있어요."
    if topic:
        suffix = "구체화 정도는 아직 분명하지 않아요" if state.get("direction_stage") == "unknown" else "관련 관점을 살펴보고 있어요"
        return f"현재 관심은 ‘{topic}’이며, {suffix}."
    return "현재 기록만으로는 탐색 방향과 구체화 정도를 판단하기 어려워요."


def enrich_state(state, events, previous=None):
    """Changes and flow below describe recorded actions, not understanding or commitment."""
    previous = previous or {}
    intents = [e for e in events if e["type"] in {"user_input", "topic_browse", "topic_refine"}]
    latest = intents[-1] if intents else None
    boundary = previous.get("direction_context_start_event_id", 0)
    if latest and state.get("topic_relation") == "changed":
        boundary = latest["id"]
    state["direction_context_start_event_id"] = boundary
    scoped = [e for e in events if e["id"] >= boundary]
    state["display_version"] = "11.12-v1"
    choices = [e for e in scoped if e["type"] in {"topic_select", "topic_browse", "topic_refine"}]
    seen, directions = set(), []
    choice_labels = {"topic_select": "후보 선택", "topic_browse": "둘러보기", "topic_refine": "구체화하기"}
    for step in reversed(topic_steps(scoped)):
        key = step["key"]
        if key and key not in seen:
            seen.add(key)
            directions.append({"title": step["title"], "action": choice_labels[step["action"]],
                               "event_id": step["id"], "recommendation_id": step["recommendation_id"]})
    state["observed_directions"] = directions[:3]
    sources = {e["id"]: e for e in scoped if e["type"] == "user_input"}
    aspects = [a for a in state.get("clarified_aspects", []) if any(
        i in sources and a.get("text") in sources[i]["payload"].get("text", "") for i in a.get("evidence_ids", []))]
    for aspect in previous.get("clarified_aspects", []):
        if aspect.get("kind") == "problem" and aspect.get("text") != state.get("research_problem"):
            continue
        if any(i in sources and aspect.get("text") in sources[i]["payload"].get("text", "") for i in aspect.get("evidence_ids", [])):
            if not any(a["kind"] == aspect["kind"] and a["text"] == aspect["text"] for a in aspects):
                aspects.append(aspect)
    state["clarified_aspects"] = aspects[:4]
    enrich_progress(state, events, previous)
    if not state.get("clarified_aspects"):
        initial = next((e for e in scoped if e["type"] == "user_input"), None)
        focus_change = (state.get("focus_history") or [None])[-1]
        state["clarified_aspects"] = ([{"kind": "perspective", "text": focus_change["current_focus"],
                                       "evidence_ids": focus_change["evidence_ids"]}] if focus_change else
                                     [{"kind": "interest", "text": initial["payload"].get("text", ""),
                                       "evidence_ids": [initial["id"]]}] if initial else [])
    if state.get("research_problem") and not any(a["kind"] == "problem" for a in state["clarified_aspects"]):
        state["clarified_aspects"].append({"kind": "problem", "text": state["research_problem"],
                                           "evidence_ids": state.get("problem_evidence_ids", [])})
    state["change_evidence_ids"] = [latest["id"]] if latest else []
    update = state.get("focus_update")
    if update:
        target = compact(update["current_focus"], limit=90)
        source = compact(update["previous_focus"], limit=70)
        state["change_evidence_ids"] = update["evidence_ids"]
        state["recent_change"] = (f"‘{source}’보다 ‘{target}’에 관심을 두려는 정정이 드러났어요."
                                  if update["operation"] == "replace" else f"이번 발화에서 ‘{target}’이라는 검토 관점이 {'구체화' if update['operation']=='refine' else '추가'}됐어요.")
    elif not latest:
        state["recent_change"] = "변화를 비교할 기록이 아직 충분하지 않아요."
    elif not previous:
        state["recent_change"] = "관심 주제나 질문을 입력하며 이번 탐색을 시작했어요."
    elif state.get("topic_relation") == "changed":
        state["recent_change"] = "이전 방향에서 다른 연구 방향을 살펴보는 입력·선택이 추가됐어요."
    elif latest["type"] in {"topic_browse", "topic_refine"}:
        title = compact(latest["payload"].get("title"), limit=90)
        state["recent_change"] = (f"‘{title}’ 안의 문제·범위를 더 살펴보려는 선택이 추가됐어요."
                                  if latest["type"] == "topic_refine" else f"‘{title}’과 관련된 관점을 더 둘러보려는 선택이 추가됐어요.")
    elif (state.get("research_problem") and state.get("research_problem") != previous.get("research_problem")
          and latest["id"] in state.get("problem_evidence_ids", [])):
        state["recent_change"] = f"이번 발화에서 ‘{compact(state['research_problem'], limit=100)}’이라는 연구 문제가 드러났어요."
    else:
        changes = {"concept": "현재 관심에서 개념이나 예시를 확인하는 질문이 추가됐어요.",
                   "connection": "현재 관심과 추천된 방향의 연결을 확인하는 질문이 추가됐어요.",
                   "compare": "검토하는 후보들의 차이를 확인하는 질문이 추가됐어요.",
                   "formulate": "현재 관심을 연구 문제·범위로 구체화하는 요청이 추가됐어요.",
                   "literature": "현재 관심을 문헌에서 살펴보려는 요청이 추가됐어요."}
        state["recent_change"] = changes.get(state.get("support_request"), "이번 입력이 추가됐으며, 구체화 정도의 변화는 아직 단정하지 않았어요.")
    anchor = choices[-1] if choices else (intents[-1] if intents else None)
    after = [e for e in scoped if anchor and e["id"] > anchor["id"]]
    activity_names = {"paper_search_submit": "논문 검색", "abstract_expand": "초록 펼치기", "paper_open": "원문 링크 열기", "paper_save": "관심 논문 저장"}
    activities = list(dict.fromkeys(activity_names[e["type"]] for e in after if e["type"] in activity_names))
    if anchor and anchor["type"] in choice_labels:
        phrase = f"‘{compact(anchor['payload'].get('title'), limit=90)}’ 후보에서 {choice_labels[anchor['type']]}를 선택했어요."
    elif anchor:
        phrase = f"‘{compact(anchor['payload'].get('text'), limit=90)}’을 입력하며 탐색을 이어갔어요."
    else:
        phrase = "탐색을 이어갈 기록이 아직 충분하지 않아요."
    if activities:
        phrase += " 이후 " + "·".join(activities) + " 활동이 기록됐어요."
    state["flow_summary"] = phrase
    state["flow_evidence_ids"] = ([anchor["id"]] if anchor else []) + [e["id"] for e in after if e["type"] in activity_names][-5:]
    state["open_questions"] = state.get("unresolved", [])[:3]
    if not state["open_questions"] and not state.get("research_problem"):
        state["open_questions"] = ["현재 관심에서 어떤 현상이나 문제를 살펴볼지"]
    state["chat_summary"] = chat_summary(state)
    progress = state.get("exploration_progress") or {}
    if progress.get("status") in {"reviewing", "narrowing", "revising"}:
        state["summary"] = progress["description"] + (" 연구 문제의 명시 여부와 별개로 관찰한 검토 흐름이에요." if not state.get("research_problem") else " 연구 문제의 최종 확정을 의미하지는 않아요.")
    return state
