"""Evidence-bound projections, not a measurement of hidden cognition."""
import copy
import re
from .behavior import behavior_signals, support_bundles, support_issues, request_type
from .difficulty import LABELS as DIFFICULTY_LABELS, negative_experience, difficulty_kind, guess_kind, technical_connection, pending_difficulties
from .progress import validated_focus_update, correction_request, grounded_span

OBSERVATIONS = {"user_input", "topic_select", "topic_browse", "topic_refine", "paper_search_submit",
                "abstract_expand", "abstract_collapse", "paper_open", "paper_save", "paper_unsave", "session_wrapup"}
STAGE_LABELS = {"exploring": "방향 탐색", "forming": "방향 형성", "unknown": "판단 유보"}
ACTION_LABELS = {"topic_select": "후보 선택", "topic_browse": "둘러보기 선택", "topic_refine": "구체화하기 선택",
                 "paper_search_submit": "논문 검색", "abstract_expand": "초록 펼치기", "abstract_collapse": "초록 접기",
                 "paper_open": "원문 링크 열기", "paper_save": "관심 논문에 담기", "paper_unsave": "관심 논문에서 빼기", "session_wrapup": "탐색 정리 선택"}


def empty_state():
    return {"direction_stage": "unknown", "current_direction": "", "research_problem": "",
            "problem_evidence_ids": [], "problem_evidence": [],
            "summary": "아직 탐색 상태를 판단할 정보가 충분하지 않습니다.", "recent_change": "",
            "difficulty_status": "unknown", "difficulties": [], "difficulty_history": [], "pending_difficulties": [], "support_issues": [], "evidence": [],
            "unresolved": [], "support_request": "directions", "support_request_evidence_ids": [],
            "recommendation_intent": "uncertain", "recommendation_evidence_ids": [], "topic_relation": "uncertain"}


def text(value, length=700):
    return str(value or "").strip()[:length]


def valid_ids(values, allowed):
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(v for v in values if type(v) is int and v in allowed))


def observation(event):
    """Facts come from the actual event, never model-authored observations."""
    p, kind = event["payload"], event["type"]
    if kind == "user_input":
        return "입력: “" + text(p.get("text"), 500) + "”"
    value = p.get("query") if kind == "paper_search_submit" else p.get("title")
    return ACTION_LABELS.get(kind, kind) + ": “" + text(value, 250) + "”"


def quoted_evidence(refs, allowed):
    result = []
    for ref in (refs or [])[:8]:
        if not isinstance(ref, dict) or type(ref.get("event_id")) is not int:
            continue
        event = allowed.get(ref["event_id"])
        quote = text(ref.get("quote"), 1000)
        if event and event["type"] == "user_input" and len(quote) >= 3 and quote in event["payload"].get("text", ""):
            result.append({"event_id": event["id"], "quote": quote})
    return result


def _anchors(value):
    tokens = re.findall(r"[a-z][a-z0-9_-]+|[가-힣]{2,}", value.lower())
    endings = ("으로", "에서", "에게", "에는", "하고", "이", "가", "을", "를", "의", "에", "과", "와", "은", "는")
    return {next((t[:-len(s)] for s in endings if t.endswith(s) and len(t) - len(s) >= 2), t) for t in tokens}


def _problem_supported(problem, quote, basis=None):
    # Exact quotes are not semantic proof. Reject keywords, generic help and unanchored reconstructions.
    substantive = r"영향|차이|관계|한계|효과|변화|변하는|방해|실패|오해|불일치|오류|의도.{0,5}전이|어떻게.{2,30}(?:달라|미치|지원)"
    commitment = r"연구|다루|알아보|확인|보고\s*싶|비교|탐구|검증|어떻게|왜|미치는|변하는|영향"
    common = (_anchors(problem) & _anchors(quote)) - {"연구", "주제", "문제", "질문", "범위", "관심", "방법", "어떻게", "구체화"}
    if len(quote) < 12 or len(common) < 2:
        return False
    if basis and basis.get("kind") in {"phenomenon", "relationship", "design_goal"}:
        subject, focus = text(basis.get("subject")), text(basis.get("focus"))
        context = text(basis.get("context"))
        if basis["kind"] in {"phenomenon", "design_goal"} and not (
                len(context) >= 3 and context in quote and context != focus and context not in subject):
            return False
        # Semantic role assignment is model-proposed; exact, distinct source spans
        # anchor it. Causal/effect vocabulary is not required for a phenomenon.
        return (len(subject) >= 2 and len(re.sub(r"\s", "", focus)) >= 6 and subject != focus
                and subject in quote and focus in quote and focus not in {"연구 주제 구체화", "연구 문제 정하기"}
                and not re.search(r"(?:추천|설명|정리|구체화).{0,5}해\s*줘|어떻게.{0,12}(?:정하|구체화)|모르겠", quote))
    return bool(re.search(substantive, quote) and re.search(commitment, quote))


def _problem_excerpt(quote):
    # Keep the displayed problem within the user's own words rather than expanding a purpose.
    statement = re.split(r"(?<=[.!?])\s+", quote, maxsplit=1)[0].strip()
    match = re.search(r"(?:연구하고|다루고|알아보고|확인하고)\s*싶", statement)
    if match and match.start() >= 8:
        statement = re.sub(r"(?:을|를)\s*$", "", statement[:match.start()].strip())
    return text(statement.rstrip('. '), 220)


def _resolved_expression(value):
    if re.search(r"아직|여전히|안\s*됐|이해.{0,6}(?:안|못)|해결.{0,5}(?:안|못)", value):
        return False
    if re.search(r"될까|했을까|될\s*것|되면|됐으면|인지\s*모르", value):
        return False
    return bool(re.search(r"이제.{0,15}(?:알겠|정리됐|정리가 됐)|이해(?:가)?\s*(?:됐|되었|했)|해결(?:이)?\s*(?:됐|되었|했)|(?:선택|결정)했|골랐|연구\s*(?:문제|질문).{0,8}정했|더\s*이상.{0,10}어렵지|어려움(?:이|은)?\s*없", value))


def _clauses(value):
    return [part.strip() for part in re.split(r"[.!?。\n]|지만\s*|는데\s*|다만\s*|하지만\s*|그런데\s*", value) if part.strip()]


def _difficulty_expression(kind, value):
    return negative_experience(value) and guess_kind(value) == kind


def _request_kind(content):
    if re.search(r"(?:논문|문헌).{0,15}(?:추천|찾|보여)|관련\s*논문", content):
        return "literature"
    if technical_connection(content) or re.search(r"(?:뭐야|무슨\s*뜻|정의|개념.{0,10}설명|쉽게.{0,10}설명|예시.{0,10}설명|이해가?\s*안)", content):
        return "concept"
    if re.search(r"(?:관련|연결|왜\s*추천)", content):
        return "connection"
    if re.search(r"비교|차이|뭘\s*고|고르.{0,8}(?:모르|어려)|선택.{0,10}(?:모르|어려)", content):
        return "compare"
    if re.search(r"구체화|연구\s*(?:문제|질문).{0,20}(?:정하|만들|바꾸|모르)|연구\s*주제.{0,20}모르", content):
        return "formulate"
    return "directions"


def _new_directions(content):
    return bool(re.search(r"(?:다른|새로운|다음).{0,12}(?:주제|방향|후보|키워드)|(?:주제|방향|후보|키워드).{0,12}(?:추천|제안|없어)|어떤\s*연구|무슨\s*연구|연구.{0,10}할\s*수|research\s+(?:topics?|directions?)", content, re.I))


def _history(previous):
    history = copy.deepcopy((previous or {}).get("difficulty_history") or [])
    if not history:
        for d in (previous or {}).get("difficulties", []):
            ids = d.get("evidence_ids") or []
            if ids:
                history.append(dict(d, id=f"difficulty-{min(ids)}-{d['type']}", topic=(previous or {}).get("current_direction", ""),
                                    lifecycle="unconfirmed", first_event_id=min(ids), last_event_id=max(ids), support_provided=False))
    return history


def summarize_state(state):
    direction = state["current_direction"]
    if state["direction_stage"] == "forming":
        summary = f"‘{state['research_problem']}’에 관한 연구 문제의 범위와 접근을 구체화하고 있어요."
    elif direction:
        summary = (f"현재 ‘{direction}’에 관한 연구 방향을 살펴보고 있으며, 연구 문제의 구체화 정도는 아직 판단하기 어려워요."
                   if state["direction_stage"] == "unknown" else f"현재 ‘{direction}’에 관한 연구 방향을 살펴보고 있어요.")
    else:
        summary = "현재 기록만으로는 탐색 방향과 구체화 정도를 판단하기 어려워요."
    expressed = [d for d in state["difficulties"] if d["status"] == "expressed"]
    if expressed:
        summary += (" 탐색에서 어려움을 표현했으며 유형은 아직 판단하지 않았어요." if len(expressed) == 1 and expressed[0]["type"] == "unspecified"
                    else " " + "·".join(DIFFICULTY_LABELS[d["type"]] for d in expressed) + "의 어려움을 표현했어요.")
    elif state["difficulties"]:
        summary += " 활동과 대화 맥락상 탐색 지원이 필요할 가능성이 있어요."
    elif pending_difficulties(state):
        summary += " 이전에 표현한 어려움의 해소 여부는 아직 확인되지 않았어요."
    return summary


def validate_state(raw, events, previous=None, trace=None):
    """Validate quotations and conservative criteria; no claim of psychological truth."""
    state, warnings = empty_state(), []
    allowed = {e["id"]: e for e in events if e["type"] in OBSERVATIONS}
    latest = next((e for e in reversed(events) if e["type"] in {"user_input", "topic_browse", "topic_refine"}), None)
    latest_id = latest["id"] if latest else 0
    content = latest["payload"].get("text", "") if latest and latest["type"] == "user_input" else ""
    state["commitment_requested"] = bool(re.search(r"확정해|최종.{0,8}결정|결정해\s*줘|정해\s*줘", content))
    state["detail_requested"] = bool(re.search(r"자세히|자세하게|상세히|상세하게|꼼꼼히|단계별로", content))
    state["direction_stage"] = raw.get("direction_stage") if raw.get("direction_stage") in STAGE_LABELS else "unknown"
    state["current_direction"] = text(raw.get("current_direction"), 180)
    state["recent_change"] = text(raw.get("recent_change"), 250)
    state["unresolved"] = [text(x, 180) for x in (raw.get("unresolved") or [])[:4] if isinstance(x, str)]
    problem = text(raw.get("research_problem"), 350)
    refs = quoted_evidence(raw.get("problem_evidence"), allowed)
    basis = copy.deepcopy(raw.get("problem_basis") or {})
    basis_ref = next((r for r in refs if r["event_id"] == basis.get("event_id")), None)
    if basis_ref:
        for field in ("subject", "focus", "context"):
            basis[field] = grounded_span(basis.get(field), basis_ref["quote"], context=field == "context")
    refs = [r for r in refs if _problem_supported(problem, r["quote"], basis if basis.get("event_id") == r["event_id"] else None)]
    if problem and refs:
        quote = min(refs, key=lambda r: len(r["quote"]))["quote"]
        if basis.get("kind") in {"phenomenon", "relationship", "design_goal"} and basis.get("focus"):
            quote = next((c.strip() for c in re.split(r"[?!。]|(?:이|가)?\s*아니라|말고|보다는", quote)
                          if basis["focus"] in c and basis.get("subject", "") in c), quote)
        state.update(research_problem=_problem_excerpt(quote),
                     problem_evidence=refs, problem_evidence_ids=[r["event_id"] for r in refs])
        state["problem_basis"] = basis
    if state["direction_stage"] == "forming" and not state["research_problem"]:
        state["direction_stage"] = "unknown"
        warnings.append("forming_without_grounded_problem")
    state["clarified_aspects"] = []
    for aspect in (raw.get("clarified_aspects") or [])[:4]:
        if not isinstance(aspect, dict) or aspect.get("kind") not in {"interest", "problem", "perspective", "scope"}:
            continue
        if aspect["kind"] == "problem":
            continue
        for ref in quoted_evidence(aspect.get("evidence_quotes"), allowed)[:1]:
            state["clarified_aspects"].append({"kind": aspect["kind"], "text": ref["quote"], "evidence_ids": [ref["event_id"]]})

    request = raw.get("support_request")
    request_ids = valid_ids(raw.get("support_request_evidence_ids"), allowed)
    if latest and latest["type"] == "user_input":
        if request in {"concept", "connection", "compare", "formulate", "literature"} and latest_id in request_ids:
            state["support_request"], state["support_request_evidence_ids"] = request, [latest_id]
        else:
            state["support_request"] = _request_kind(content)
            state["support_request_evidence_ids"] = [latest_id] if state["support_request"] != "directions" else []
        rec_ids = valid_ids(raw.get("recommendation_evidence_ids"), allowed)
        if _new_directions(content) or (raw.get("recommendation_intent") == "generate" and latest_id in rec_ids and state["support_request"] in {"directions", "formulate"}):
            state["recommendation_intent"], state["recommendation_evidence_ids"] = "generate", [latest_id]
        elif state["support_request"] in {"concept", "connection", "compare", "literature"}:
            state["recommendation_intent"] = "preserve"
        if technical_connection(content):
            state["support_request"], state["support_request_evidence_ids"] = "concept", [latest_id]

    topic_ids = valid_ids(raw.get("topic_evidence_ids"), allowed)
    explicit_shift = bool(re.search(r"다른\s*주제|주제를?\s*바꾸|이제.{0,25}(?:대신|주제로|탐색할)|그건\s*그만", content))
    action_shift = latest and latest["type"] == "topic_browse" and latest["payload"].get("title") != (previous or {}).get("current_direction")
    changed = raw.get("topic_relation") == "changed" and latest_id in topic_ids and (explicit_shift or action_shift)
    state["topic_relation"] = "changed" if changed else ("same" if raw.get("topic_relation") == "same" else "uncertain")
    state["focus_update"] = validated_focus_update(raw, events, previous)
    state["correction_requested"] = correction_request(content)
    if changed and state["research_problem"] and latest_id not in state["problem_evidence_ids"]:
        state.update(research_problem="", problem_evidence=[], problem_evidence_ids=[])
        if state["direction_stage"] == "forming":
            state["direction_stage"] = "unknown"
        warnings.append("old_problem_outside_current_direction")
    history = _history(previous)
    for h in history:
        if h.get("lifecycle") in {"active", "unconfirmed"}:
            value = " ".join(r.get("quote", "") for r in h.get("evidence_quotes", []))
            kind = difficulty_kind(h["type"], value)
            if value and kind != h["type"]:
                h.setdefault("original_type", h["type"])
                h["type"] = kind
    old_through = max([h.get("last_event_id", 0) for h in history] or [0])
    for h in history:
        if h["lifecycle"] in {"active", "unconfirmed"}:
            h["lifecycle"] = "out_of_scope" if changed else "unconfirmed"
    behavior_trace = trace if trace is not None else events
    # The behavior window handles explicit topic switches. Candidate comparisons may cross branches.
    progress_ids = list(state.get("problem_evidence_ids", []))
    for a in state.get("clarified_aspects", []) + (previous or {}).get("clarified_aspects", []):
        if a.get("kind") in {"scope", "perspective"}:
            if request_type(a.get("text", "")) and not re.search(r"상황|대상|환경|한정|조건|에서|관점|초점|맥락", a.get("text", "")):
                continue
            progress_ids.extend(i for i in a.get("evidence_ids", []) if i in allowed and allowed[i]["type"] == "user_input"
                                and a.get("text") and a["text"] in allowed[i]["payload"].get("text", ""))
    progress_boundary = max(progress_ids or [0])
    patterns = behavior_signals([e for e in behavior_trace if e["id"] >= progress_boundary])
    bundles = {b["type"]: b for b in support_bundles(patterns, latest_id)}
    state["behavior_signals"] = patterns
    state["support_issues"] = support_issues(behavior_trace)
    candidates = list(raw.get("difficulties") or [])[:5]
    if content and negative_experience(content) and not any(
            negative_experience(r["quote"]) for d in candidates if isinstance(d, dict)
            for r in quoted_evidence(d.get("evidence_quotes"), allowed) if r["event_id"] == latest_id):
        candidates.append({"type": guess_kind(content), "status": "expressed", "evidence_ids": [latest_id],
                           "evidence_quotes": [{"event_id": latest_id, "quote": content}]})
    for kind, bundle in bundles.items():
        candidates.append({"type": kind, "status": "possible", "evidence_ids": bundle["event_ids"], "evidence_quotes": []})
    for d in candidates:
        if not isinstance(d, dict) or d.get("type") not in DIFFICULTY_LABELS:
            continue
        kind = d["type"]
        ids = valid_ids(d.get("evidence_ids"), allowed)
        if not ids:
            warnings.append("difficulty_without_observation")
            continue
        quotes = quoted_evidence(d.get("evidence_quotes"), allowed)
        direct = [r for r in quotes if r["event_id"] in ids and negative_experience(r["quote"])
                  and negative_experience(allowed[r["event_id"]]["payload"].get("text", ""))]
        if direct:
            status = "expressed"
            original_kind = kind
            kind = difficulty_kind(kind, direct[0]["quote"])
            if kind != original_kind:
                warnings.append("difficulty_type_reinterpreted")
        elif kind in bundles and len(set(ids) & set(bundles[kind]["event_ids"])) >= 3 and d.get("status") == "possible":
            status = "possible"
        else:
            warnings.append("difficulty_without_supported_meaning")
            continue
        if changed and latest_id not in ids:
            continue
        existing = next((h for h in reversed(history) if h["type"] == kind and h["lifecycle"] in {"active", "unconfirmed"}), None)
        if existing and existing.get("status") == "expressed" and status == "possible":
            existing.update(recent_signals=bundles[kind]["signals"], last_behavior_event_id=max(ids))
            continue
        if existing and max(ids) <= existing.get("last_event_id", 0):
            continue
        description = (("탐색에서 어려움을 표현했어요. 어떤 유형인지는 아직 판단하지 않았어요." if kind == "unspecified"
                        else f"{DIFFICULTY_LABELS[kind]}의 어려움을 직접 표현했어요.") if status == "expressed"
                       else f"서로 다른 활동 단서가 함께 나타나 {DIFFICULTY_LABELS[kind]} 지원이 필요할 가능성이 있어요. 어려움으로 확정하지 않았어요.")
        item = {"type": kind, "status": status, "description": description,
                "evidence_ids": ids, "evidence_quotes": direct,
                "source": "utterance" if status == "expressed" else "behavior",
                "signals": bundles[kind]["signals"] if status == "possible" else []}
        if existing:
            existing.update(item, lifecycle="active", last_event_id=max(ids))
        else:
            existing = dict(item, id=f"difficulty-{min(ids)}-{kind}", topic=state["current_direction"], lifecycle="active",
                            first_event_id=min(ids), last_event_id=max(ids), support_provided=False)
            history.append(existing)
        state["difficulties"].append(dict(item, id=existing["id"]))

    open_history = [h for h in history if h["lifecycle"] in {"active", "unconfirmed"}]
    for update in (raw.get("difficulty_updates") or [])[:8]:
        if not isinstance(update, dict):
            continue
        h = next((h for h in open_history if h["id"] == update.get("difficulty_id")), None)
        refs = quoted_evidence(update.get("evidence_quotes"), allowed)
        refs = [r for r in refs if r["event_id"] > old_through and r["event_id"] == latest_id]
        if not h or update.get("outcome") != "resolved" or not refs:
            continue
        original = allowed[latest_id]["payload"].get("text", "")
        names = {"concept": r"개념|이해|설명|뜻|원리", "connection": r"관련|연결", "selection": r"선택|결정|고르|골랐", "formulation": r"구체화|연구\s*(?:문제|질문)|범위", "unspecified": r"정리|흐름|막막|어려움|방향"}
        positive = " ".join(clause for clause in _clauses(original) if _resolved_expression(clause)
                            and any(r["quote"] in clause or clause in r["quote"] for r in refs))
        if positive and (len(open_history) == 1 or re.search(names[h["type"]], positive)):
            h.update(lifecycle="resolved", followup_evidence=refs, last_event_id=latest_id)
            state["difficulties"] = [d for d in state["difficulties"] if d["id"] != h["id"]]
        else:
            warnings.append("resolution_without_explicit_followup")
    state["difficulty_history"] = history[-20:]
    state["pending_difficulties"] = pending_difficulties(state)
    if state["difficulties"]:
        state["difficulty_status"] = "expressed" if any(d["status"] == "expressed" for d in state["difficulties"]) else "possible"
    elif any(h["lifecycle"] == "resolved" and h.get("last_event_id") == latest_id for h in history):
        state["difficulty_status"] = "clear"
    elif raw.get("difficulty_status") == "clear":
        warnings.append("clear_without_user_observation")

    requested_evidence = list(raw.get("evidence") or [])
    needed = state["problem_evidence_ids"] + [i for d in state["difficulties"] for i in d["evidence_ids"]]
    for i in needed:
        if not any(isinstance(e, dict) and e.get("event_id") == i for e in requested_evidence):
            requested_evidence.append({"event_id": i, "interpretation": "상태 추정에서 참조한 기록입니다."})
    for e in requested_evidence:
        if not isinstance(e, dict) or type(e.get("event_id")) is not int or e["event_id"] not in allowed:
            warnings.append("invalid_evidence_reference")
            continue
        if any(item["event_id"] == e["event_id"] for item in state["evidence"]):
            continue
        source = allowed[e["event_id"]]
        state["evidence"].append({"event_id": source["id"], "event_type": source["type"], "observation": observation(source),
                                  "interpretation": text(e.get("interpretation"), 300)})
        if len(state["evidence"]) >= 5:
            break
    state["summary"] = summarize_state(state)
    return state, warnings


def preview_state(events, previous=None):
    """Explicitly labelled offline preview, using the same evidence guards."""
    raw = empty_state()
    speech = [e for e in events if e["type"] == "user_input"]
    if not speech:
        return raw
    last = next((e for e in reversed(events) if e["type"] in {"user_input", "topic_browse", "topic_refine"}), speech[-1])
    content = last["payload"].get("text", "")
    actions = [e for e in events if e["type"] in {"topic_browse", "topic_refine"}]
    raw["current_direction"] = text(actions[-1]["payload"].get("title") if actions else speech[0]["payload"].get("text"), 180)
    raw["direction_stage"] = "exploring"
    raw["support_request"] = _request_kind(content)
    raw["support_request_evidence_ids"] = [last["id"]] if content else []
    raw["evidence"] = [{"event_id": last["id"], "interpretation": "현재 입력과 선택을 탐색 맥락으로 기록했습니다."}]
    if content and negative_experience(content):
        raw["difficulties"].append({"type": guess_kind(content), "status": "expressed",
                                    "evidence_ids": [last["id"]], "evidence_quotes": [{"event_id": last["id"], "quote": content}]})
    return validate_state(raw, events, previous)[0]
