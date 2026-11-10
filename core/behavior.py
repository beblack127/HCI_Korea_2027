"""Observable interaction patterns; never proof of a user's experienced difficulty."""
import hashlib
import re
import unicodedata

CHOICES = {"topic_select", "topic_browse", "topic_refine"}
USER_EVENTS = CHOICES | {"user_input", "paper_search_submit", "abstract_expand", "abstract_collapse",
                         "paper_open", "paper_save", "paper_unsave", "session_wrapup"}


def normalized(value):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", str(value or "")).casefold())


def topic_key(payload):
    title = normalized(payload.get("title"))
    if title:
        return hashlib.sha256(title.encode()).hexdigest()[:20]
    return payload.get("topic_key") or payload.get("recommendation_id") or ""


def topic_steps(events):
    steps, pending = [], None
    for e in events:
        kind, p = e["type"], e["payload"]
        if kind in {"user_input", "session_wrapup"}:
            pending = None
        if kind not in CHOICES:
            continue
        key = topic_key(p)
        if not key:
            continue
        if (kind != "topic_select" and pending is not None
                and pending["recommendation_id"] == p.get("recommendation_id", "")
                and pending["key"] == key):
            pending["action"] = kind
            pending["event_ids"].append(e["id"])
            pending["id"] = e["id"]
            pending = None
            continue
        step = {"key": key, "title": p.get("title", ""), "action": kind, "id": e["id"],
                "event_ids": [e["id"]], "recommendation_id": p.get("recommendation_id", "")}
        steps.append(step)
        pending = step if kind == "topic_select" else None
    return steps


def request_type(value):
    if re.search(r"구체화|범위.{0,10}좁|연구\s*질문.{0,10}(?:만들|정리|제안)", value):
        return "formulation"
    if re.search(r"비교|차이|뭘\s*고|어느.{0,8}(?:고르|선택)", value):
        return "selection"
    if re.search(r"뭐야|무슨\s*뜻|설명해|설명해\s*줘|예시|더\s*쉽게|다르게.{0,8}설명", value):
        return "concept"
    return ""


def _window(events):
    boundary = max([e["id"] for e in events if e["type"] == "session_wrapup"
                    or (e["type"] == "user_input" and re.search(r"다른\s*주제|주제를?\s*바꾸|그건\s*그만", e["payload"].get("text", "")))] or [0])
    observed = [e for e in events if e["type"] in USER_EVENTS and e["id"] >= boundary][-40:]
    start = observed[0]["id"] if observed else boundary
    return [e for e in events if e["id"] >= start]


def behavior_signals(events):
    recent = _window(events)
    steps = topic_steps(recent)[-12:]
    signals = []
    def add(name, kind, family, ids, note):
        signals.append({"pattern": name, "type": kind, "family": family,
                        "event_ids": sorted(set(ids)), "observation": note})
    trail = []
    for s in steps:
        if not trail or s["key"] != trail[-1]["key"]:
            trail.append(s)
    seen, returns = set(), 0
    for s in trail:
        returns += s["key"] in seen
        seen.add(s["key"])
    if len(trail) >= 4 and returns >= 2:
        add("candidate_revisits", "selection", "navigation", [s["id"] for s in trail[-6:]],
            "서로 다른 후보를 살펴본 뒤 이전 주제로 돌아오는 선택이 반복됐어요. 비교를 위한 활동일 수도 있어요.")

    responses = [e["id"] for e in recent if e["type"] == "agent_response"]
    refines = [s for s in steps if s["action"] == "topic_refine"]
    if len(refines) >= 3 and len({s["key"] for s in refines[-3:]}) == 1 and all(
            any(a["id"] < r < b["id"] for r in responses) for a, b in zip(refines[-3:], refines[-2:])):
        add("repeated_refinement", "formulation", "navigation", [s["id"] for s in refines[-3:]],
            "응답을 받은 뒤 같은 주제를 구체화하는 요청이 반복됐어요.")

    requests = {k: [] for k in ["concept", "selection", "formulation"]}
    request_topic = ""
    for e in recent:
        if e["type"] in {"topic_browse", "topic_refine"}:
            key = topic_key(e["payload"])
            if request_topic and request_topic != key:
                requests = {k: [] for k in requests}
            request_topic = key
        if e["type"] == "user_input":
            kind = request_type(e["payload"].get("text", ""))
            if kind:
                requests[kind].append(e)
            else:
                # A new substantive statement interrupts an old series of requests.
                requests = {k: [] for k in requests}
    for kind, items in requests.items():
        answered = [a for a, b in zip(items, items[1:]) if any(a["id"] < r < b["id"] for r in responses)]
        sequence = answered + (items[-1:] if answered else [])
        if len(sequence) >= (3 if kind == "concept" else 2):
            add("repeated_" + kind + "_requests", kind, "dialogue", [e["id"] for e in sequence],
                "응답을 받은 뒤 같은 종류의 설명·검토 요청이 이어졌어요.")
        repairs = [e for e in sequence if re.search(r"다시|다르게|다른\s*예시|더\s*쉽게|그\s*설명.{0,8}말고", e["payload"].get("text", ""))]
        if kind == "concept" and len(repairs) >= 2:
            add("explanation_repair_requests", kind, "repair", [e["id"] for e in repairs],
                "설명 후 다른 예시나 설명 방식으로 바꿔 달라는 요청이 반복됐어요.")
    searches = [e for e in recent if e["type"] == "paper_search_submit"]
    reviews = [e for e in recent if e["type"] in {"abstract_expand", "paper_open", "paper_save"}]
    if len({normalized(e["payload"].get("query")) for e in searches}) >= 3 and len({e["payload"].get("paper_id") for e in reviews} - {None, ""}) >= 2:
        add("search_and_review", "selection", "literature", [e["id"] for e in searches[-3:] + reviews[-2:]],
            "검색어를 바꾸고 서로 다른 문헌을 검토했어요. 그 자체로 어려움을 뜻하지는 않아요.")
    return signals


def support_bundles(signals, latest_id):
    result = []
    for kind in sorted({s["type"] for s in signals}):
        group = [s for s in signals if s["type"] == kind]
        ids = sorted({i for s in group for i in s["event_ids"]})
        if len({s["family"] for s in group}) >= 2 and len(ids) >= 3 and latest_id in ids:
            result.append({"type": kind, "event_ids": ids, "signals": group})
    return result


def support_issues(events):
    replies = [e for e in _window(events) if e["type"] == "agent_response"][-2:]
    if len(replies) != 2 or any(e["payload"].get("support_mode") not in {"directions", "browse", "formulate"} for e in replies):
        return []
    sets = [{(normalized(r.get("title")), normalized(r.get("description"))) for r in e["payload"].get("offered_options", [])} for e in replies]
    if len(sets[0]) >= 2 and sets[0] == sets[1]:
        return [{"type": "repeated_candidates", "event_ids": [e["id"] for e in replies],
                 "observation": "직전 두 응답에서 같은 제목·설명의 후보 목록이 제공됐어요. 시스템 추천의 반복으로 기록했어요."}]
    return []
