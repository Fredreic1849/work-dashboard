"""Validated cloud records and local-only state."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from datetime import datetime, timedelta, timezone
import uuid

KINDS = {"activity", "worklog", "idea", "checkpoint", "revision", "tombstone"}
FIELDS = {"schema_version", "id", "kind", "project_id", "occurred_at", "day", "title", "summary", "result", "next_action", "priority", "evidence_level", "source", "evidence", "idea_status", "hypothesis", "validation", "supersedes"}
TEXT_FIELDS = {"title": 160, "summary": 2000, "result": 2000, "next_action": 2000, "hypothesis": 2000, "validation": 2000}
SENSITIVE = re.compile(r"(?:github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9_]+|\bsk-[A-Za-z0-9_-]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+\S+|\b(?:api[_-]?key|password|passwd|secret|access[_-]?token|refresh[_-]?token)\s*[:=]\s*\S+|\b[A-Z][A-Z0-9_]{2,}\s*=\s*\S+|\b(?:\d{1,3}\.){3}\d{1,3}\b|(?:^|[\s\"'(])/(?:Users|home|mnt|Volumes|private|etc|var|opt|tmp|pfs|workspace)/|[A-Za-z]:\\|\b[\w.-]+\.(?:internal|local|corp)\b|(?:joyspace|jdy|joybuilder)\.jd\.com|```)", re.I)
SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
UTC = timezone.utc
SHANGHAI = timezone(timedelta(hours=8))


def now():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_time(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return result
    except (ValueError, TypeError, AttributeError):
        raise ValueError("timestamp must be ISO 8601 with timezone") from None


def safe_text(value, limit=2000):
    if not isinstance(value, str) or len(value) > limit or SENSITIVE.search(value):
        raise ValueError("content requires local review: length or sensitive pattern")
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError("control characters are not allowed")
    if re.search(r"(?:^|[\s\"'(])/(?:[^\s/]+/)+[^\s]*", value):
        raise ValueError("absolute filesystem paths must remain local")
    return value


def _id(value):
    safe_text(value, 160)
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ValueError("invalid identifier")


def make_event(kind, project_id, title, **fields):
    stamp = fields.pop("occurred_at", now())
    event = {"schema_version": 1, "id": str(uuid.uuid4()), "kind": kind,
             "project_id": project_id, "occurred_at": stamp,
             "day": parse_time(stamp).astimezone(SHANGHAI).date().isoformat(),
             "title": title, "summary": "", "result": "", "next_action": "",
             "priority": "P2", "evidence_level": "unverified",
             "source": {"kind": "manual", "device_id": "unassigned"}, "evidence": []}
    event.update(fields)
    return event


def validate_event(event):
    """Return an independent validated record. Never include input in errors."""
    if not isinstance(event, dict) or set(event) - FIELDS:
        raise ValueError("unknown event fields")
    result = copy.deepcopy(event)
    if type(result.get("schema_version")) is not int or result.get("schema_version") != 1 or not isinstance(result.get("kind"), str) or result.get("kind") not in KINDS:
        raise ValueError("unsupported event schema")
    for key in ("id", "project_id"):
        _id(result.get(key))
    stamp = parse_time(result.get("occurred_at"))
    if result.get("day") != stamp.astimezone(SHANGHAI).date().isoformat():
        raise ValueError("day must use Asia/Shanghai")
    for key, limit in TEXT_FIELDS.items():
        if key in result:
            safe_text(result[key], limit)
    if not result.get("title") or not isinstance(result.get("priority"), str) or result.get("priority") not in {"P0", "P1", "P2"}:
        raise ValueError("title and valid priority required")
    if not isinstance(result.get("evidence_level"), str) or result.get("evidence_level") not in {"unverified", "local", "remote"}:
        raise ValueError("invalid evidence level")
    source = result.get("source")
    if not isinstance(source, dict) or set(source) - {"kind", "device_id", "thread_id", "turn_id", "commit"}:
        raise ValueError("invalid source fields")
    if not isinstance(source.get("kind"), str) or source.get("kind") not in {"manual", "codex", "git"}:
        raise ValueError("invalid source kind")
    for key, value in source.items():
        if key != "kind":
            _id(value)
    evidence = result.get("evidence", [])
    if not isinstance(evidence, list) or len(evidence) > 20:
        raise ValueError("invalid evidence list")
    for item in evidence:
        if not isinstance(item, dict) or set(item) - {"id", "label", "device_id", "digest", "checked_at", "kind", "url"}:
            raise ValueError("invalid evidence fields")
        for key in ("id", "device_id"):
            _id(item.get(key))
        safe_text(item.get("label"), 160)
        if not isinstance(item.get("kind"), str) or item.get("kind") not in {"local", "remote", "reference"}:
            raise ValueError("invalid evidence kind")
        if "digest" in item and (not isinstance(item["digest"], str) or not re.fullmatch(r"[a-f0-9]{40,64}", item["digest"])):
            raise ValueError("invalid evidence digest")
        if "checked_at" in item:
            parse_time(item["checked_at"])
        if "url" in item:
            from urllib.parse import urlsplit
            url = urlsplit(safe_text(item["url"], 1000))
            if url.scheme != "https" or url.hostname not in {"github.com", "arxiv.org", "doi.org", "openreview.net"} or url.username or url.password or url.query:
                raise ValueError("evidence URL must be a public reference without credentials or query")
    if result["evidence_level"] != "unverified":
        proof = [e for e in evidence if e.get("kind") == result["evidence_level"] and e.get("checked_at") and (e.get("digest") or e.get("url"))]
        if not proof:
            result["evidence_level"] = "unverified"
    if "idea_status" in result and (not isinstance(result["idea_status"], str) or result["idea_status"] not in {"proposed", "testing", "adopted", "parked"}):
        raise ValueError("invalid idea status")
    parents = result.get("supersedes", [])
    if not isinstance(parents, list) or len(parents) > 20:
        raise ValueError("invalid revision references")
    for parent in parents:
        _id(parent)
        if parent == result["id"]:
            raise ValueError("revision cannot reference itself")
    if result["kind"] in {"revision", "tombstone"} and not parents:
        raise ValueError("revision requires prior event IDs")
    if len(json.dumps(result, ensure_ascii=False).encode()) > 20000:
        raise ValueError("record too large")
    return result


def canonical(event):
    clean = copy.deepcopy(event)
    clean.get("source", {}).pop("device_id", None)
    return json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def resolve_events(events):
    """Append-only revisions: preserve conflicting leaves; never revive tombstones."""
    unique, conflicts = {}, set()
    variants = []
    for original in events:
        event = copy.deepcopy(original)
        old = unique.get(event["id"])
        if old and canonical(old) != canonical(event):
            conflicts.add(event["id"])
            if not any(canonical(v) == canonical(event) for v in variants):
                variants.append(event)
        elif not old:
            unique[event["id"]] = event
    all_events = list(unique.values()) + variants
    hidden = {p for e in all_events for p in e.get("supersedes", [])}
    visible = [e for e in all_events if e["id"] not in hidden and e["kind"] != "tombstone"]
    summarized = {(e["project_id"], e.get("source", {}).get("turn_id")) for e in visible
                  if e["kind"] in {"worklog", "checkpoint", "revision"} and e.get("source", {}).get("turn_id")}
    visible = [e for e in visible if not (e["kind"] == "activity" and
               (e["project_id"], e.get("source", {}).get("turn_id")) in summarized)]
    def ancestors(e, seen=None):
        seen = set() if seen is None else seen
        for parent in e.get("supersedes", []):
            if parent not in seen:
                seen.add(parent)
                if parent in unique:
                    ancestors(unique[parent], seen)
        return seen
    ancestry = {e["id"]: ancestors(e) for e in visible}
    for i, e in enumerate(visible):
        e["conflict"] = e["id"] in conflicts or any(ancestry[e["id"]] & ancestry[v["id"]] for j, v in enumerate(visible) if i != j)
    return sorted(visible, key=lambda e: (e["occurred_at"], e["id"]), reverse=True)


class Store:
    def __init__(self, home):
        self.home = Path(home).expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.home, 0o700)
        cfg = self.home / "config.json"
        self.config = json.loads(cfg.read_text()) if cfg.exists() else {"device_id": str(uuid.uuid4()), "device_name": "Mac", "repo_path": str(self.home / "journal"), "projects": [], "codex_homes": [], "primary": False}
        self.db = sqlite3.connect(str(self.home / "state.sqlite"), timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, payload TEXT NOT NULL, eligible INTEGER NOT NULL, synced INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS cursors(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS issues(code TEXT PRIMARY KEY, message TEXT NOT NULL, at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, path TEXT NOT NULL);
        """)
        os.chmod(self.home / "state.sqlite", 0o600)

    def save_config(self):
        target = self.home / "config.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.config, ensure_ascii=False, indent=2) + "\n")
        os.chmod(temporary, 0o600)
        temporary.replace(target)

    def get_project(self, project_id):
        return next((p for p in self.config["projects"] if p["id"] == project_id), None)

    def project_for_path(self, path):
        target = Path(path).expanduser().resolve()
        matches = []
        for project in self.config["projects"]:
            for raw in project.get("paths", []):
                root = Path(raw).expanduser().resolve()
                if target == root or root in target.parents:
                    matches.append((len(root.parts), project))
        return max(matches, key=lambda p: p[0])[1] if matches else None

    def add_event(self, event, eligible=False, approved=False):
        event = copy.deepcopy(event)
        event.setdefault("source", {"kind": "manual"})
        if not isinstance(event["source"], dict):
            raise ValueError("source must be an object")
        if not event["source"].get("device_id") or event["source"].get("device_id") == "unassigned":
            event["source"]["device_id"] = self.config["device_id"]
        valid = validate_event(event)
        project = self.get_project(valid["project_id"])
        if not project:
            raise ValueError("register the local project before recording")
        policy = project.get("policy", "local_only")
        export = bool((eligible and policy == "auto") or (approved and policy == "review"))
        old = self.db.execute("SELECT payload FROM events WHERE id=?", (valid["id"],)).fetchone()
        if old:
            if canonical(json.loads(old[0])) != canonical(valid):
                self.issue("record_conflict", "A logical record has conflicting content; both source files remain local.")
                raise ValueError("record ID already exists with different content")
            return valid["id"]
        with self.db:
            self.db.execute("INSERT INTO events(id,payload,eligible) VALUES(?,?,?)", (valid["id"], json.dumps(valid, ensure_ascii=False), int(export)))
        return valid["id"]

    def approve(self, event_id):
        row = self.db.execute("SELECT payload FROM events WHERE id=?", (event_id,)).fetchone()
        if not row:
            raise ValueError("record not found")
        event = validate_event(json.loads(row[0]))
        project = self.get_project(event["project_id"])
        if not project or project.get("policy") == "local_only":
            raise ValueError("local_only projects cannot be approved for upload; change project policy explicitly first")
        with self.db:
            self.db.execute("UPDATE events SET eligible=1 WHERE id=?", (event_id,))

    def events(self, eligible_only=False):
        query = "SELECT payload FROM events" + (" WHERE eligible=1" if eligible_only else "") + " ORDER BY rowid"
        result = [json.loads(row[0]) for row in self.db.execute(query)]
        return [e for e in result if not eligible_only or (self.get_project(e["project_id"]) or {}).get("policy", "local_only") != "local_only"]

    def pending(self):
        events = [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM events WHERE eligible=1 AND synced=0 ORDER BY rowid")]
        return [e for e in events if (self.get_project(e["project_id"]) or {}).get("policy", "local_only") != "local_only"]

    def mark_synced(self, ids):
        with self.db:
            self.db.executemany("UPDATE events SET synced=1 WHERE id=? AND eligible=1", [(value,) for value in ids])

    def cursor(self, key):
        row = self.db.execute("SELECT value FROM cursors WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_cursor(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO cursors VALUES(?,?)", (key, json.dumps(value)))

    def issue(self, code, message):
        _id(code)
        safe_text(message, 500)
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO issues VALUES(?,?,?)", (code, message, now()))

    def clear_issue(self, code):
        with self.db:
            self.db.execute("DELETE FROM issues WHERE code=?", (code,))

    def issues(self):
        return [dict(row) for row in self.db.execute("SELECT code,message,at FROM issues ORDER BY at DESC")]

    def close(self):
        self.db.close()
