"""Strict model response shapes. Evidence semantics are additionally checked in state.py."""


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def string(choices=None):
    result = {"type": "string"}
    if choices:
        result["enum"] = choices
    return result


def array(items):
    return {"type": "array", "items": items}


ids = array({"type": "integer"})
quotes = array(obj({"event_id": {"type": "integer"}, "quote": string()}))
SCHEMAS = {
    "estimate": obj({
        "direction_stage": string(["exploring", "forming", "unknown"]),
        "current_direction": string(), "research_problem": string(), "problem_evidence": quotes,
        "problem_basis": obj({"kind": string(["none", "phenomenon", "relationship", "design_goal"]),
                              "subject": string(), "focus": string(), "context": string(), "event_id": {"type": "integer"}}),
        "focus_update": obj({"operation": string(["none", "add", "refine", "replace", "compare"]),
                             "previous_focus": string(), "current_focus": string(), "evidence_quotes": quotes}),
        "summary": string(), "recent_change": string(),
        "clarified_aspects": array(obj({"kind": string(["interest", "problem", "perspective", "scope"]), "evidence_quotes": quotes})),
        "difficulty_status": string(["unknown", "possible", "expressed", "clear"]),
        "difficulties": array(obj({"type": string(["concept", "connection", "selection", "formulation", "unspecified"]),
            "status": string(["possible", "expressed"]), "description": string(), "target": string(), "reason": string(),
            "related_difficulty_id": string(), "evidence_ids": ids,
            "evidence_quotes": quotes})),
        "difficulty_updates": array(obj({"difficulty_id": string(),
            "outcome": string(["resolved", "progressed", "redirected", "unconfirmed"]),
            "evidence_ids": ids, "evidence_quotes": quotes, "reason": string()})),
        "topic_relation": string(["same", "changed", "uncertain"]), "topic_evidence_ids": ids,
        "evidence": array(obj({"event_id": {"type": "integer"}, "interpretation": string()})),
        "unresolved": array(string()),
        "support_request": string(["directions", "concept", "connection", "compare", "formulate", "literature"]),
        "support_request_evidence_ids": ids,
        "recommendation_intent": string(["generate", "preserve", "uncertain"]), "recommendation_evidence_ids": ids}),
    "response": obj({"assistance": obj({"title": string(), "body": string(), "paper_ids": array(string())}),
        "recommendations": array(obj({"title": string(), "description": string(),
        "rationale": string(), "search_query": string(),
        "relation": string(["problem", "perspective", "scope", "concept", "continuation"])})),
        "next_step": string(), "next_step_reason": string()}),
    "query": obj({"query": string(), "reason": string()})
}
