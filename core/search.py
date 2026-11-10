import hashlib
import json
import re
import time
from datetime import date
from urllib.parse import urlparse
import requests


class SearchError(Exception):
    pass


def normalize_years(year_from=None, year_to=None):
    current_year = date.today().year
    def year(value, label):
        if value is None or value == "":
            return None
        if isinstance(value, bool) or not re.fullmatch(r"\d{4}", str(value)) or not 1800 <= int(value) <= current_year:
            raise ValueError(f"{label} 연도는 1800~{current_year} 사이로 입력해 주세요.")
        return int(value)
    start, end = year(year_from, "시작"), year(year_to, "종료")
    if start and end and start > end:
        raise ValueError("시작 연도는 종료 연도보다 늦을 수 없습니다.")
    return start, end


def safe_url(value):
    value = str(value or "")
    return value if urlparse(value).scheme in {"http", "https"} else ""


def abstract_from_index(index):
    if not isinstance(index, dict):
        return ""
    tokens = {}
    for word, positions in index.items():
        if not isinstance(positions, list):
            continue
        for pos in positions:
            if type(pos) is int and 0 <= pos < 30000:
                tokens[pos] = str(word)
    return " ".join(tokens[pos] for pos in sorted(tokens))[:18000]


def normalize_work(work):
    prim = work.get("primary_location") or {}
    source = prim.get("source") or {}
    doi = safe_url(work.get("doi"))
    work_url = safe_url(work.get("id"))
    original = safe_url(prim.get("landing_page_url")) or doi
    return {"id": str(work.get("id") or doi), "title": str(work.get("display_name") or work.get("title") or "제목 정보 없음"),
            "authors": [str((a.get("author") or {}).get("display_name", "")) for a in (work.get("authorships") or [])[:8]],
            "year": work.get("publication_year"), "source": str(source.get("display_name") or ""),
            "abstract": abstract_from_index(work.get("abstract_inverted_index")),
            "url": original or work_url, "link_label": "원문 페이지" if original else "논문 상세",
            "doi": doi, "citations": int(work.get("cited_by_count") or 0),
            "open_access": bool((work.get("open_access") or {}).get("is_oa")), "saved": False}


class OpenAlex:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store

    def search(self, query, page=1, sort="relevance", year_from=None, year_to=None):
        query = re.sub(r"\s+", " ", str(query)).strip()[:350]
        if not query:
            raise SearchError("검색어를 입력해 주세요.")
        page = max(1, min(int(page), 50))
        sort = sort if sort in {"relevance", "latest", "citations"} else "relevance"
        current_year = date.today().year
        year_from, year_to = normalize_years(year_from, year_to)
        key = hashlib.sha256(json.dumps([query, page, sort, year_from, year_to, current_year], ensure_ascii=False).encode()).hexdigest()
        with self.store.connect() as con:
            cached = con.execute("SELECT * FROM search_cache WHERE cache_key=?", (key,)).fetchone()
        if cached and time.time() - cached["created_at"] < 3600:
            return dict(json.loads(cached["payload"]), cached=True)
        params = {"search": query, "page": page, "per_page": 10,
                  "select": "id,doi,title,display_name,publication_year,authorships,primary_location,open_access,abstract_inverted_index,cited_by_count"}
        filters = ["to_publication_date:" + date.today().isoformat()]
        if year_from or year_to:
            filters.append(f"publication_year:{year_from or 1800}-{year_to or current_year}")
        params["filter"] = ",".join(filters)
        if sort != "relevance":
            params["sort"] = "publication_date:desc" if sort == "latest" else "cited_by_count:desc"
        if self.settings.openalex_key:
            params["api_key"] = self.settings.openalex_key
        if self.settings.openalex_mailto:
            params["mailto"] = self.settings.openalex_mailto
        try:
            r = requests.get("https://api.openalex.org/works", params=params, timeout=(8, 25))
        except requests.RequestException as exc:
            raise SearchError("OpenAlex에 연결할 수 없습니다. 잠시 후 다시 검색해 주세요.") from exc
        if r.status_code in {401, 403, 429}:
            raise SearchError("OpenAlex 인증 또는 사용 한도를 확인해 주세요. 필요하면 .env에 OPENALEX_API_KEY를 추가할 수 있습니다.")
        if not r.ok:
            raise SearchError(f"OpenAlex 검색을 처리하지 못했습니다(HTTP {r.status_code}).")
        try:
            data = r.json()
            result = {"query": query, "page": page, "per_page": 10, "total": int((data.get("meta") or {}).get("count") or 0),
                      "sort": sort, "year_from": year_from, "year_to": year_to,
                      "papers": [normalize_work(w) for w in data.get("results", []) if w.get("id")], "cached": False}
        except (ValueError, TypeError, KeyError) as exc:
            raise SearchError("OpenAlex 검색 결과의 형식을 확인하지 못했습니다.") from exc
        with self.store.connect() as con:
            con.execute("INSERT OR REPLACE INTO search_cache VALUES(?,?,?)", (key, json.dumps(result, ensure_ascii=False), time.time()))
        return result
