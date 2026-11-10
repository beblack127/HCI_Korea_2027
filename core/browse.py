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


def completed_response_issues(response, previous, mode, selected=None, latest_input=""):
    """Check completed proposals; preserve candidates for explanations/comparisons.

    This is a repetition guard, not proof of semantic quality. New titles alone
    cannot conceal unchanged descriptions. New details with stable titles can be
    useful in a refinement, unlike an unchanged set.
    """
    if mode == "browse":
        return browse_response_issues(response, previous)
    if mode not in {"directions", "formulate"}:
        return []
    if mode == "directions" and re.search(r"(?:기존|같은).{0,12}(?:후보|목록).{0,10}(?:그대로|다시|보여)|(?:후보|목록).{0,10}그대로", latest_input):
        return []
    candidates = response.get("recommendations", [])
    pairs = lambda items: {(normalized(r.get("title")), normalized(r.get("description"))) for r in items}
    issues = []
    if len(candidates) >= 2 and pairs(candidates) == pairs(previous):
        issues.append("repeated_candidate_set")
    elif len(candidates) >= 2 and len(candidates) == len(previous) and all(r.get("description") for r in candidates) and (
            {normalized(r.get("description")) for r in candidates} == {normalized(r.get("description")) for r in previous}):
        issues.append("repeated_descriptions")
    if mode == "formulate" and selected and any(
            normalized(r.get("title")) == normalized(selected.get("title")) and
            normalized(r.get("description")) == normalized(selected.get("description")) for r in candidates):
        issues.append("selected_parent_repeated")
    return issues
