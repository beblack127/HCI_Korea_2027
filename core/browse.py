"""Structural checks for new browse content, not a semantic relevance judge."""
import re
import unicodedata


def normalized(value):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value or "").casefold())


def browse_response_issues(response, previous):
    candidates = response.get("recommendations", [])
    issues = []
    if not response.get("assistance", {}).get("body", "").strip():
        issues.append("missing_explanation")
    if len(candidates) != 3:
        issues.append("need_three_candidates")
    titles = [normalized(r.get("title")) for r in candidates]
    if any(not title for title in titles) or len(set(titles)) != len(titles):
        issues.append("duplicate_or_empty_titles")
    descriptions = [normalized(r.get("description")) for r in candidates]
    if any(not d or not r.get("search_query", "").strip() for d, r in zip(descriptions, candidates)):
        issues.append("missing_candidate_details")
    if len(candidates) == len(previous) == 3:
        if set(titles) == {normalized(r.get("title")) for r in previous}:
            issues.append("repeated_candidate_set")
        elif all(descriptions) and set(descriptions) == {normalized(r.get("description")) for r in previous}:
            issues.append("repeated_descriptions")
    return issues
