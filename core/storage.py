"""Session-scoped, append-only observations and separate model projections."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(value):
    return json.dumps(value, ensure_ascii=False)


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = self.directory / "workspace.sqlite3"
        with self.connect() as con:
            con.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                  id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                  title TEXT NOT NULL DEFAULT '새 탐색', condition TEXT NOT NULL DEFAULT 'adaptive');
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                  client_id TEXT NOT NULL, type TEXT NOT NULL, created_at TEXT NOT NULL,
                  payload TEXT NOT NULL, UNIQUE(session_id, client_id));
                CREATE INDEX IF NOT EXISTS event_session ON events(session_id, id);
                CREATE TABLE IF NOT EXISTS messages (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                  message_key TEXT NOT NULL, role TEXT NOT NULL, payload TEXT NOT NULL,
                  created_at TEXT NOT NULL, UNIQUE(session_id, message_key));
                CREATE TABLE IF NOT EXISTS estimates (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                  request_id TEXT NOT NULL, through_event_id INTEGER NOT NULL,
                  state TEXT NOT NULL, plan TEXT NOT NULL, provenance TEXT NOT NULL,
                  created_at TEXT NOT NULL, UNIQUE(session_id, request_id));
                CREATE TABLE IF NOT EXISTS turns (
                  session_id TEXT NOT NULL, request_id TEXT NOT NULL, response TEXT NOT NULL,
                  PRIMARY KEY(session_id, request_id));
                CREATE TABLE IF NOT EXISTS papers (
                  session_id TEXT NOT NULL, paper_id TEXT NOT NULL, payload TEXT NOT NULL,
                  saved INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(session_id, paper_id));
                CREATE TABLE IF NOT EXISTS search_cache (
                  cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at REAL NOT NULL);
            """)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.db, timeout=20)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def create_session(self, condition="adaptive"):
        sid = str(uuid.uuid4())
        with self.connect() as con:
            con.execute("INSERT INTO sessions(id,created_at,condition) VALUES(?,?,?)",
                        (sid, now(), condition))
        self.event(sid, "session_start", {"condition": condition}, "session-start")
        return sid

    def exists(self, sid):
        with self.connect() as con:
            return bool(con.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone())

    def event(self, sid, kind, payload, client_id=None):
        client_id = client_id or str(uuid.uuid4())
        with self.connect() as con:
            con.execute("INSERT OR IGNORE INTO events(session_id,client_id,type,created_at,payload) VALUES(?,?,?,?,?)",
                        (sid, client_id, kind, now(), dump(payload)))
            row = con.execute("SELECT * FROM events WHERE session_id=? AND client_id=?", (sid, client_id)).fetchone()
        return self._event(row)

    @staticmethod
    def _event(row):
        return {"id": row["id"], "type": row["type"], "created_at": row["created_at"],
                "payload": json.loads(row["payload"])}

    def events(self, sid):
        with self.connect() as con:
            return [self._event(r) for r in con.execute("SELECT * FROM events WHERE session_id=? ORDER BY id", (sid,))]

    def add_message(self, sid, key, role, payload):
        with self.connect() as con:
            con.execute("INSERT OR IGNORE INTO messages(session_id,message_key,role,payload,created_at) VALUES(?,?,?,?,?)",
                        (sid, key, role, dump(payload), now()))

    def messages(self, sid):
        with self.connect() as con:
            rows = con.execute("SELECT * FROM messages WHERE session_id=? ORDER BY id", (sid,)).fetchall()
            states = {r["request_id"]: json.loads(r["state"]) for r in con.execute(
                "SELECT request_id,state FROM estimates WHERE session_id=?", (sid,))}
        messages = []
        for r in rows:
            request_id = r["message_key"].rsplit(":", 1)[0]
            content = json.loads(r["payload"])
            # Associate each answer with its own validated estimate, including older sessions.
            # This projection does not rewrite the original messages or estimate history.
            if r["role"] == "assistant" and request_id in states:
                state = states[request_id]
                content = dict(content, difficulty_status=state.get("difficulty_status", "unknown"),
                               difficulty_types=[d["type"] for d in state.get("difficulties", [])])
            messages.append({"id": r["id"], "request_id": request_id, "role": r["role"],
                             "created_at": r["created_at"], "content": content})
        return messages

    def put_estimate(self, sid, request_id, through, state, plan, provenance):
        with self.connect() as con:
            con.execute("""INSERT INTO estimates(session_id,request_id,through_event_id,state,plan,provenance,created_at)
                        VALUES(?,?,?,?,?,?,?) ON CONFLICT(session_id,request_id) DO UPDATE SET
                        through_event_id=excluded.through_event_id,state=excluded.state,plan=excluded.plan,provenance=excluded.provenance""",
                        (sid, request_id, through, dump(state), dump(plan), dump(provenance), now()))

    def estimate(self, sid, request_id):
        with self.connect() as con:
            r = con.execute("SELECT * FROM estimates WHERE session_id=? AND request_id=?", (sid, request_id)).fetchone()
        return self._estimate(r)

    def latest_estimate(self, sid, exclude_request_id=""):
        with self.connect() as con:
            r = con.execute("SELECT * FROM estimates WHERE session_id=? AND request_id!=? ORDER BY through_event_id DESC,id DESC LIMIT 1",
                            (sid, exclude_request_id)).fetchone()
        return self._estimate(r)

    @staticmethod
    def _estimate(r):
        if not r:
            return None
        return {"request_id": r["request_id"], "state": json.loads(r["state"]), "plan": json.loads(r["plan"]),
                "provenance": json.loads(r["provenance"]), "through_event_id": r["through_event_id"],
                "created_at": r["created_at"]}

    def cached_turn(self, sid, request_id):
        with self.connect() as con:
            r = con.execute("SELECT response FROM turns WHERE session_id=? AND request_id=?", (sid, request_id)).fetchone()
        return json.loads(r[0]) if r else None

    def finish_turn(self, sid, request_id, response):
        with self.connect() as con:
            con.execute("INSERT OR REPLACE INTO turns VALUES(?,?,?)", (sid, request_id, dump(response)))

    def set_title(self, sid, text):
        with self.connect() as con:
            con.execute("UPDATE sessions SET title=? WHERE id=? AND title='새 탐색'", (text[:45], sid))

    def put_papers(self, sid, papers):
        with self.connect() as con:
            for p in papers:
                con.execute("INSERT INTO papers(session_id,paper_id,payload) VALUES(?,?,?) ON CONFLICT(session_id,paper_id) DO UPDATE SET payload=excluded.payload",
                            (sid, p["id"], dump(p)))

    def paper(self, sid, pid):
        with self.connect() as con:
            r = con.execute("SELECT * FROM papers WHERE session_id=? AND paper_id=?", (sid, pid)).fetchone()
        return dict(json.loads(r["payload"]), saved=bool(r["saved"])) if r else None

    def set_saved(self, sid, pid, saved):
        with self.connect() as con:
            con.execute("UPDATE papers SET saved=? WHERE session_id=? AND paper_id=?", (int(saved), sid, pid))

    def saved(self, sid):
        with self.connect() as con:
            return [dict(json.loads(r["payload"]), saved=True) for r in con.execute(
                "SELECT * FROM papers WHERE session_id=? AND saved=1 ORDER BY rowid DESC", (sid,))]

    def snapshot(self, sid):
        with self.connect() as con:
            r = con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        events = self.events(sid)
        loaded = next((e for e in reversed(events) if e["type"] == "paper_results_loaded"), None)
        last_search = None
        if loaded:
            p = loaded["payload"]
            last_search = {"query": p["query"], "page": p["page"], "total": p["total"], "per_page": 10,
                           "sort": p.get("sort", "relevance"), "year_from": p.get("year_from"),
                           "year_to": p.get("year_to"), "source": p.get("source", "manual_panel"),
                           "recommendation_id": p.get("recommendation_id", ""),
                           "papers": [paper for pid in p["paper_ids"] if (paper := self.paper(sid, pid))]}
        return {"session": dict(r), "messages": self.messages(sid), "events": events,
                "estimate": self.latest_estimate(sid), "saved_papers": self.saved(sid), "last_search": last_search}

    def export(self, sid):
        result = self.snapshot(sid)
        with self.connect() as con:
            result["estimate_history"] = [dict(state=json.loads(r["state"]), plan=json.loads(r["plan"]),
                provenance=json.loads(r["provenance"]), through_event_id=r["through_event_id"], created_at=r["created_at"])
                for r in con.execute("SELECT * FROM estimates WHERE session_id=? ORDER BY id", (sid,))]
        result["exported_at"] = now()
        result["schema_version"] = "11.10-v1"
        return result
