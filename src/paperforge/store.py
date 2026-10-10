from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from paperforge.config import Settings, write_settings
from paperforge.schemas import Decision, Project

STAGES = ("intake", "literature", "plan", "analysis", "draft", "review", "export")


def now() -> str:
    return datetime.now(UTC).isoformat()


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class Store:
    """Single-author project store. SQLite is the checkpoint authority, files are artifacts."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.db_path = self.root / "workflow.sqlite3"
        self.config_path = self.root / "paperforge.yaml"

    @classmethod
    def create(cls, root: Path, project: Project, settings: Settings | None = None) -> Store:
        store = cls(root)
        if store.root.exists() and any(store.root.iterdir()):
            raise FileExistsError(
                "Use an empty project directory; v2 projects are not imported implicitly"
            )
        for folder in ("inputs", "outputs", "audit"):
            (store.root / folder).mkdir(parents=True, exist_ok=True)
        with store.connect() as db:
            db.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE stages (name TEXT PRIMARY KEY, status TEXT NOT NULL,
                    attempt INTEGER NOT NULL, output TEXT, error TEXT, updated TEXT NOT NULL);
                CREATE TABLE events (id INTEGER PRIMARY KEY, time TEXT NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE calls (id INTEGER PRIMARY KEY, request_key TEXT NOT NULL,
                    route TEXT NOT NULL, status TEXT NOT NULL, cost REAL NOT NULL,
                    reply TEXT, time TEXT NOT NULL, detail TEXT);
                CREATE INDEX call_cache ON calls(request_key, status);
                CREATE TABLE checkpoints (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """
            )
        store.set("schema_version", 1)
        store.set("project", project.model_dump(mode="json"))
        store.set("status", "pending")
        write_settings(store.config_path, settings or Settings())
        store.event("created", project.model_dump(mode="json"))
        return store

    @contextmanager
    def connect(self):
        if not self.db_path.exists() and not self.root.exists():
            raise FileNotFoundError("Project directory does not exist")
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def lock(self):
        return FileLock(str(self.root / "workflow.lock"), timeout=1)

    def get(self, key: str, default: Any = None) -> Any:
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value: Any) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))

    def event(self, kind: str, payload: Any) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO events(time,kind,payload) VALUES (?,?,?)",
                (now(), kind, json.dumps(payload)),
            )

    def stage(self, name: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM stages WHERE name=?", (name,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["output"] = json.loads(result["output"]) if result["output"] else None
        return result

    def start(self, name: str) -> None:
        old = self.stage(name)
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO stages VALUES (?,?,?,?,?,?)",
                (name, "running", (old["attempt"] if old else 0) + 1, None, None, now()),
            )
        self.set("status", "running")

    def finish(self, name: str, output: Any, status: str = "completed", error: str | None = None):
        with self.connect() as db:
            db.execute(
                "UPDATE stages SET status=?,output=?,error=?,updated=? WHERE name=?",
                (status, json.dumps(output), error, now(), name),
            )
        self.event("stage", {"name": name, "status": status, "error": error})
        self.write(
            f"audit/stages/{name}-{now().replace(':', '-')}.json",
            json.dumps(
                {"status": status, "output": output, "error": error}, indent=2, ensure_ascii=False
            ),
        )

    def checkpoint(self, key: str, value: Any) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO checkpoints VALUES (?,?)", (key, json.dumps(value)))

    def cached_checkpoint(self, key: str) -> Any:
        with self.connect() as db:
            row = db.execute("SELECT value FROM checkpoints WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def invalidate(self, start: str, reason: str) -> None:
        affected = STAGES[STAGES.index(start) :]
        with self.connect() as db:
            for name in affected:
                db.execute("DELETE FROM stages WHERE name=?", (name,))
                key = "epoch:" + name
                row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
                epoch = json.loads(row[0]) if row else 0
                db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(epoch + 1)))
            # An export of old inputs must never appear current after invalidation.
            db.execute("INSERT OR REPLACE INTO meta VALUES ('status', '\"pending\"')")
        self.event("invalidated", {"stages": affected, "reason": reason})

    def spent(self) -> float:
        with self.connect() as db:
            return float(db.execute("SELECT COALESCE(SUM(cost),0) FROM calls").fetchone()[0])

    def reserve(self, request_key: str, route: str, amount: float, budget: float) -> int:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            spent = float(db.execute("SELECT COALESCE(SUM(cost),0) FROM calls").fetchone()[0])
            if amount > 0 and spent + amount > budget + 1e-9:
                raise ValueError(
                    f"Budget would be exceeded: reserved/spent ₹{spent:.4f}, next ceiling ₹{amount:.4f}, cap ₹{budget:.2f}"
                )
            cursor = db.execute(
                "INSERT INTO calls(request_key,route,status,cost,time) VALUES (?,?,?,?,?)",
                (request_key, route, "reserved", amount, now()),
            )
            return int(cursor.lastrowid)

    def settle(
        self,
        call_id: int,
        status: str,
        cost: float | None = None,
        reply: dict | None = None,
        detail: str | None = None,
    ):
        with self.connect() as db:
            db.execute(
                "UPDATE calls SET status=?,cost=COALESCE(?,cost),reply=?,detail=? WHERE id=?",
                (status, cost, json.dumps(reply) if reply else None, detail, call_id),
            )

    def cached_call(self, request_key: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT reply FROM calls WHERE request_key=? AND status='completed' ORDER BY id DESC LIMIT 1",
                (request_key,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def decide(self, decision: Decision) -> None:
        with self.lock():
            if self.get("status") == "cancelled":
                raise ValueError("Cancelled projects are preserved but cannot be resumed")
            if decision.stage and decision.stage not in STAGES:
                raise ValueError("Unknown decision stage")
            if decision.action == "cancel":
                self.set("status", "cancelled")
            elif decision.action == "defer":
                self.set("status", "paused")
            else:
                if decision.note.strip():
                    # A decision is author-supplied context, never independently verified data.
                    stamp = now().replace(":", "-")
                    self.write(f"inputs/author-decision-{stamp}.txt", decision.note)
                stage = decision.stage or next(
                    (
                        s
                        for s in STAGES
                        if (self.stage(s) or {}).get("status") in {"blocked", "failed"}
                    ),
                    "review",
                )
                if decision.action == "revise" and not decision.stage:
                    stage = "draft"
                self.invalidate(stage, "Author requested continuation or revision")
            self.event("decision", decision.model_dump(mode="json"))

    def safe_path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Path must remain inside the project")
        return path

    def write(self, relative: str, text: str) -> Path:
        path = self.safe_path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(text, encoding="utf-8")
        temp.replace(path)
        return path

    def snapshot(self) -> dict:
        with self.connect() as db:
            stages = [
                dict(row)
                for row in db.execute("SELECT name,status,attempt,error,updated FROM stages")
            ]
        return {
            "project": self.get("project"),
            "status": self.get("status"),
            "spent_or_reserved_inr": self.spent(),
            "stages": stages,
        }
