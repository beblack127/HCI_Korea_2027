"""Research-topic exploration workspace with session-separated storage."""
import json
import hmac
import logging
import os
import secrets
from datetime import timedelta
from flask import Flask, jsonify, redirect, render_template, request, session, Response
from core.config import Settings
from core.storage import Store
from core.service import Workspace
from core.policy import POLICY_VERSION
from core.behavior import topic_key
from core.llm import ModelError
from core.search import SearchError, normalize_years
from core.state import OBSERVATIONS, text


def create_app(settings=None):
    settings = settings or Settings()
    store = Store(settings.data_dir)
    secret_path = settings.data_dir / ".session-secret"
    if not secret_path.exists():
        secret_path.write_text(secrets.token_hex(32), encoding="utf-8")
        secret_path.chmod(0o600)
    app = Flask(__name__)
    app.secret_key = secret_path.read_text().strip()
    app.config.update(MAX_CONTENT_LENGTH=2 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_NAME="research_exploration_1109",
                      SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
                      PERMANENT_SESSION_LIFETIME=timedelta(days=30))
    workspace = Workspace(settings, store)
    app.extensions.update(workspace=workspace, store=store, settings=settings)

    def sid():
        value = session.get("workspace_id")
        if not value or not store.exists(value):
            value = store.create_session()
            session["workspace_id"] = value
            session.permanent = True
        return value

    @app.before_request
    def study_access_gate():
        access_code = os.getenv("STUDY_ACCESS_CODE", "").strip()
        if not access_code or session.get("study_access") or request.endpoint == "static":
            return None
        if request.path in {"/study-login", "/api/status"}:
            return None
        if request.path.startswith("/api/"):
            return jsonify(error="스터디 접속 코드를 입력해 주세요."), 401
        return render_template("study_login.html", error=None), 401

    @app.post("/study-login")
    def study_login():
        expected = os.getenv("STUDY_ACCESS_CODE", "").strip()
        submitted = request.form.get("access_code", "")
        if expected and hmac.compare_digest(submitted, expected):
            session["study_access"] = True
            return redirect("/")
        return render_template("study_login.html", error="접속 코드를 다시 확인해 주세요."), 401

    @app.before_request
    def local_api():
        if request.method == "POST" and request.path.startswith("/api/"):
            if not request.is_json or request.headers.get("X-Workspace-Request") != "1":
                return jsonify(error="올바른 앱 요청이 아닙니다."), 400
            origin = request.headers.get("Origin")
            if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
                return jsonify(error="다른 출처의 요청은 허용하지 않습니다."), 403

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        return render_template("index.html", condition="proposed")

    @app.get("/api/status")
    def status():
        return jsonify(ok=True, version=POLICY_VERSION, mode="gemini" if settings.live else "preview",
                       provider="gemini", model=settings.model, configured=bool(settings.api_key),
                       api_style=settings.api_style, thinking_level=settings.thinking_level,
                       timeout_seconds=settings.timeout,
                       search_provider="OpenAlex", openalex_configured=bool(settings.openalex_key))

    @app.get("/api/workspace")
    def snapshot():
        return jsonify(store.snapshot(sid()))

    @app.post("/api/session/new")
    def new_session():
        session["workspace_id"] = store.create_session()
        session.permanent = True
        return jsonify(store.snapshot(session["workspace_id"]))

    @app.post("/api/turn")
    def turn():
        return jsonify(workspace.turn(sid(), request.get_json() or {}))

    @app.get("/api/turn/progress")
    def turn_progress():
        current, request_id = sid(), text(request.args.get("request_id"), 100)
        value = workspace.get_progress(current, request_id)
        estimate = store.estimate(current, request_id)
        if estimate:
            value["estimate"] = {k: v for k, v in estimate.items() if k != "provenance"}
            ids = {e["event_id"] for e in estimate["state"].get("evidence", [])}
            ids.update(i for d in estimate["state"].get("difficulties", []) for i in d.get("evidence_ids", []))
            value["evidence_events"] = [e for e in store.events(current) if e["id"] in ids and e["type"] in OBSERVATIONS]
        return jsonify(value)

    @app.post("/api/events")
    def events():
        body = request.get_json() or {}
        kind = body.get("type")
        current = sid()
        if kind == "topic_select":
            rid = str(body.get("recommendation_id", ""))
            matches = [r for m in store.messages(current) if m["role"] == "assistant"
                       for r in m["content"].get("recommendations", []) if r["id"] == rid]
            if not matches:
                raise ValueError("선택한 후보가 없습니다.")
            details = {"recommendation_id": rid, "title": matches[-1]["title"], "topic_key": topic_key(matches[-1])}
        elif kind in {"abstract_expand", "abstract_collapse", "paper_open", "paper_save", "paper_unsave"}:
            paper = store.paper(current, body.get("paper_id", ""))
            if not paper:
                raise ValueError("이 세션에서 조회한 논문이 아닙니다.")
            details = {"paper_id": paper["id"], "title": paper["title"], "query": text(body.get("query"), 350),
                       "rank": body.get("rank"), "page": body.get("page"), "source": body.get("source", "search_panel")}
            if kind in {"paper_save", "paper_unsave"}:
                store.set_saved(current, paper["id"], kind == "paper_save")
        else:
            raise ValueError("지원하지 않는 행동 기록입니다.")
        details["client_time"] = text(body.get("client_time"), 60)
        result = store.event(current, kind, details, text(body.get("client_id"), 100) or None)
        return jsonify(event=result, snapshot=store.snapshot(current))

    @app.post("/api/papers/search")
    def papers_search():
        body = request.get_json() or {}
        current = sid()
        q = text(body.get("query"), 350)
        start, end = normalize_years(body.get("year_from"), body.get("year_to"))
        origin = {"source": "manual_panel", "recommendation_id": ""}
        if body.get("source") == "direction_keyword":
            rec = next((r for m in store.messages(current) if m["role"] == "assistant"
                        for r in m["content"].get("recommendations", []) if r["id"] == body.get("recommendation_id")), None)
            if not rec:
                raise ValueError("이 세션의 추천 검색어가 아닙니다.")
            if q == rec.get("search_query"):
                origin = {"source": "direction_keyword", "recommendation_id": rec["id"]}
        filters = {"sort": body.get("sort", "relevance"), "year_from": start, "year_to": end}
        store.event(current, "paper_search_submit", dict(query=q, page=body.get("page", 1), **filters, **origin))
        result = workspace.search.search(q, body.get("page", 1), body.get("sort", "relevance"), start, end)
        result.update(origin)
        store.put_papers(current, result["papers"])
        store.event(current, "paper_results_loaded", {"query": q, "page": result["page"], "total": result["total"],
                    "paper_ids": [p["id"] for p in result["papers"]], "cached": result["cached"],
                    **filters, **origin})
        saved_ids = {p["id"] for p in store.saved(current)}
        for p in result["papers"]:
            p["saved"] = p["id"] in saved_ids
        return jsonify(result=result, snapshot=store.snapshot(current))

    @app.get("/api/session/export")
    def export():
        current = sid()
        return Response(json.dumps(store.export(current), ensure_ascii=False, indent=2), mimetype="application/json",
                        headers={"Content-Disposition": f'attachment; filename="exploration-{current[:8]}.json"'})

    @app.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(ModelError)
    def model_error(exc):
        store.event(sid(), "model_error", {"message": str(exc), "code": exc.code, "model": settings.model})
        return jsonify(error=str(exc), error_code=exc.code, snapshot=store.snapshot(sid())), 502

    @app.errorhandler(SearchError)
    def search_error(exc):
        store.event(sid(), "paper_search_error", {"message": str(exc)})
        return jsonify(error=str(exc), snapshot=store.snapshot(sid())), 502

    return app


if __name__ == "__main__":
    settings = Settings()
    app = create_app(settings)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    print(f"연구 탐색 11.09 → http://127.0.0.1:{settings.port}", flush=True)
    print("Gemini 모드" if settings.live else "미리보기 모드 · .env에 GEMINI_API_KEY 입력 후 재시작", flush=True)
    app.run(host="127.0.0.1", port=settings.port, debug=False, threaded=True, use_reloader=False, load_dotenv=False)
