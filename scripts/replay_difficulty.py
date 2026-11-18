#!/usr/bin/env python3
"""Targeted, read-only replay of study user-input turns for difficulty changes.

The script does not update the study database and never writes raw utterances,
quotes, API keys, or model responses. Its optional report contains only turn
indices, validated labels, evidence IDs, and aggregate counts.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.behavior import behavior_signals, request_type, support_issues
from core.config import Settings, load_env
from core.difficulty import concept_question, negative_experience
from core.llm import GeminiClient
from core.presentation import enrich_state
from core.state import OBSERVATIONS, empty_state, validate_state


def read_rows(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        sessions = con.execute("SELECT id,created_at FROM sessions ORDER BY created_at,id").fetchall()
        result = []
        for row in sessions:
            sid = row["id"]
            events = [dict(r, payload=json.loads(r["payload"])) for r in con.execute(
                "SELECT id,type,created_at,payload FROM events WHERE session_id=? ORDER BY id", (sid,))]
            messages = [dict(r, content=json.loads(r["payload"])) for r in con.execute(
                "SELECT id,role,created_at,payload FROM messages WHERE session_id=? ORDER BY id", (sid,))]
            estimates = [(r["through_event_id"], json.loads(r["state"])) for r in con.execute(
                "SELECT through_event_id,state FROM estimates WHERE session_id=? ORDER BY through_event_id", (sid,))]
            if len(events) > 1:
                result.append({"id": sid, "events": events, "messages": messages, "estimates": estimates})
        return result
    finally:
        con.close()


def old_state_at(estimates, event_id):
    prior = [state for through, state in estimates if through <= event_id]
    return prior[-1] if prior else empty_state()


def compact_message_history(messages, through_time):
    from core.service import conversation_context
    rows = [m for m in messages if m["created_at"] <= through_time]
    return conversation_context(rows)


def summarize_state(state, warnings, latest_id):
    active = state.get("difficulties") or []
    history = state.get("difficulty_history") or []
    latest_active = [d for d in active if latest_id in d.get("evidence_ids", [])]
    latest_question = [d for d in latest_active if d.get("source") == "question"]
    return {
        "difficulty_status": state.get("difficulty_status", "unknown"),
        "difficulty_types": sorted({d.get("type", "") for d in latest_active if d.get("type")}),
        "difficulty_sources": sorted({d.get("source", "") for d in latest_active if d.get("source")}),
        "latest_question_support": bool(latest_question),
        "episode_outcomes": sorted({h.get("lifecycle", "") for h in history
                                     if h.get("lifecycle") in {"resolved", "progressed", "redirected"}}),
        "latest_evidence_valid": all(latest_id in d.get("evidence_ids", []) for d in latest_active),
        "validation_warnings": len(warnings),
    }


def run(args):
    if args.env_file:
        load_env(Path(args.env_file).expanduser().resolve())
    settings = Settings()
    if not settings.live:
        raise SystemExit("Gemini API key missing; no replay was run. Set GEMINI_API_KEY in the selected env file.")
    client = GeminiClient(settings)
    db_path = Path(args.db).expanduser().resolve()
    prompt = (ROOT / "prompts" / "estimate.txt").read_text(encoding="utf-8")
    criteria = json.loads((ROOT / "config" / "state_criteria.json").read_text(encoding="utf-8"))
    sessions = read_rows(db_path)[:args.sessions]
    cases, categories = [], Counter()
    call_count = 0

    for sidx, session in enumerate(sessions, 1):
        previous = None
        inputs = [e for e in session["events"] if e["type"] == "user_input"]
        input_ord = {e["id"]: i for i, e in enumerate(inputs, 1)}
        for target in inputs:
            if args.max_turns and call_count >= args.max_turns:
                break
            text = target["payload"].get("text", "")
            category = ("concept_definition" if concept_question(text) else
                        "explicit_difficulty" if negative_experience(text) else
                        "concept_request" if request_type(text) == "concept" else "other_input")
            categories[category] += 1
            events = [e for e in session["events"] if e["id"] <= target["id"] and e["type"] in OBSERVATIONS]
            if not events:
                continue
            trace = [e for e in session["events"] if e["id"] <= target["id"]]
            initial = next((e for e in events if e["type"] == "user_input"), None)
            selected = events[-160:]
            if initial and initial not in selected:
                selected = [initial] + selected
            prior = previous or old_state_at(session["estimates"], target["id"] - 1)
            available = next((m["content"].get("recommendations", []) for m in reversed(session["messages"])
                              if m["created_at"] <= target["created_at"] and m["role"] == "assistant"
                              and m["content"].get("recommendations")), [])
            context = {
                "events": selected,
                "prior_estimate": prior,
                "behavior_signals": behavior_signals(trace),
                "support_issues": support_issues(trace),
                "messages": compact_message_history(session["messages"], target["created_at"]),
                "available_directions": available,
                "latest_action": "message",
                "latest_input": text,
                "selected_direction": None,
                "context_window": {"total_events": len(trace), "observed_events": len(events),
                                   "included_events": len(selected), "message_window": 8},
                "criteria": criteria,
            }
            raw = None
            last_error = None
            for attempt in range(3):
                try:
                    raw = client.generate(prompt, context, schema="estimate")
                    break
                except Exception as exc:  # do not print provider payloads or utterances
                    last_error = type(exc).__name__
                    if attempt < 2:
                        time.sleep(2 ** attempt)
            call_count += 1
            if raw is None:
                cases.append({"session": f"S{sidx:02d}", "turn": input_ord[target["id"]],
                              "category": category, "replay": "error", "error_type": last_error})
                previous = prior
                continue
            state, warnings = validate_state(raw, selected, prior, trace=trace)
            state = enrich_state(state, selected, prior)
            current = summarize_state(state, warnings, target["id"])
            old = summarize_state(old_state_at(session["estimates"], target["id"]), [], target["id"])
            cases.append({"session": f"S{sidx:02d}", "turn": input_ord[target["id"]],
                          "category": category, "old": old, "new": current, "replay": "ok"})
            previous = state
            if args.delay:
                time.sleep(args.delay)

    diff = Counter()
    for case in cases:
        if case["replay"] != "ok":
            diff["errors"] += 1
            continue
        old, new = case["old"], case["new"]
        diff[f"old_{old['difficulty_status']}"] += 1
        diff[f"new_{new['difficulty_status']}"] += 1
        diff["new_latest_evidence_valid"] += int(new["latest_evidence_valid"])
        diff["new_latest_question_support"] += int(new["latest_question_support"])
        diff["turns_with_warning"] += int(new["validation_warnings"] > 0)
        diff["turns_with_episode_outcome"] += int(bool(new["episode_outcomes"]))
    report = {"model": settings.model, "criteria_version": criteria["version"],
              "sessions": len(sessions), "calls": call_count, "input_categories": dict(categories),
              "comparison_counts": dict(diff), "cases": cases}
    target = Path(args.out).expanduser() if args.out else Path("/private/tmp/sense_difficulty_replay.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, ensure_ascii=False, indent=2))
    print(f"Report saved outside the repository: {target}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(ROOT / "data" / "workspace.sqlite3"))
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--sessions", type=int, default=11)
    parser.add_argument("--max-turns", type=int, default=0, help="Optional API-call cap; 0 replays all selected user turns.")
    parser.add_argument("--delay", type=float, default=0)
    parser.add_argument("--out", default="/private/tmp/sense_difficulty_replay.json")
    run(parser.parse_args())
