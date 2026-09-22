"""Read explicitly registered local sources; never copy transcript content."""
from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .core import make_event

_KNOWN_RECORDS = {
    "session_meta", "turn_context", "event_msg", "response_item", "compacted",
    "world_state", "token_usage_record", "inter_agent_communication_metadata",
}
_PARSER_VERSION = 1


def _id(value):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "worklog:" + value))


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return None


def _issue(store, code):
    messages = {
        "codex_unreadable": "A registered Codex source could not be read.",
        "codex_rewritten": "A Codex log changed or was truncated; it was rescanned with logical deduplication.",
        "codex_schema": "A Codex metadata format is unsupported; affected activity was left local.",
        "codex_unknown_record": "An unrecognized Codex record type was encountered; only known metadata was read.",
        "codex_unmapped": "Codex activity has no registered project mapping and remains local.",
        "codex_fork_boundary": "Inherited Codex history has no supported boundary; affected activity was not imported.",
        "git_unreadable": "A registered project Git history could not be read.",
    }
    store.issue(code, messages[code])


def _fingerprint(handle, length):
    handle.seek(0)
    digest = hashlib.sha256()
    while length:
        data = handle.read(min(length, 1024 * 1024))
        if not data:
            break
        digest.update(data)
        length -= len(data)
    return digest.hexdigest()


def _scan_file(store, path, cutoff, days, summarized):
    key = "codex:" + hashlib.sha256(str(path).encode()).hexdigest()
    cursor = store.cursor(key) or {}
    count = 0
    try:
        stat = path.stat()
        with path.open("rb") as handle:
            old_offset = cursor.get("offset", 0)
            identity = [stat.st_dev, stat.st_ino]
            changed = bool(cursor) and (
                identity != cursor.get("identity") or stat.st_size < old_offset
                or _fingerprint(handle, cursor.get("prefix_length", 0)) != cursor.get("prefix")
            )
            if cursor and not changed and old_offset:
                handle.seek(max(0, old_offset - 2048))
                changed = hashlib.sha256(handle.read(min(2048, old_offset))).hexdigest() != cursor.get("tail")
                if not changed and cursor.get("consumed_digest") and (stat.st_mtime_ns != cursor.get("mtime_ns") or stat.st_size != old_offset):
                    changed = _fingerprint(handle, old_offset) != cursor["consumed_digest"]
            if changed:
                _issue(store, "codex_rewritten")
                cursor = {}
            mapping = hashlib.sha256(json.dumps(store.config.get("projects", []), sort_keys=True).encode()).hexdigest()
            if cursor.get("mapping") != mapping or cursor.get("window_days", days) != days or cursor.get("parser_version") != _PARSER_VERSION:
                cursor = {}
            if cursor and stat.st_size == cursor.get("offset") and stat.st_mtime_ns == cursor.get("mtime_ns"):
                return 0
            offset = cursor.get("offset", 0)
            ordinal = cursor.get("ordinal", 0)
            meta = cursor.get("meta")
            handle.seek(offset)
            while True:
                start = handle.tell()
                line = handle.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    handle.seek(start)
                    break
                offset = handle.tell()
                current_ordinal = ordinal
                ordinal += 1
                try:
                    record = json.loads(line)
                except (UnicodeError, ValueError):
                    _issue(store, "codex_schema")
                    continue
                if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
                    _issue(store, "codex_schema")
                    continue
                typ, payload = record.get("type"), record["payload"]
                if current_ordinal == 0:
                    if typ != "session_meta" or not isinstance(payload.get("id"), str):
                        _issue(store, "codex_schema")
                        meta = {"unsupported": True}
                    else:
                        boundary = payload.get("subagent_history_start_ordinal")
                        inherited = bool(payload.get("forked_from_id") or payload.get("parent_thread_id"))
                        unsupported = inherited and (not isinstance(boundary, int) or isinstance(boundary, bool) or boundary < 1)
                        meta = {
                            "id": payload["id"], "cwd": payload.get("cwd"),
                            "boundary": boundary if isinstance(boundary, int) else 0,
                            "unsupported": unsupported,
                        }
                        if unsupported:
                            _issue(store, "codex_fork_boundary")
                if typ not in _KNOWN_RECORDS:
                    _issue(store, "codex_unknown_record")
                if typ != "turn_context" or not meta or meta.get("unsupported"):
                    continue
                # First metadata identifies this file. Later metadata belongs to inherited history.
                if current_ordinal < meta["boundary"]:
                    continue
                turn = payload.get("turn_id")
                occurred = _time(record.get("timestamp"))
                if not isinstance(turn, str) or not turn or occurred is None:
                    _issue(store, "codex_schema")
                    continue
                if occurred < cutoff:
                    continue
                if turn in summarized:
                    continue
                cwd = payload.get("cwd") or meta.get("cwd")
                project = store.project_for_path(cwd) if isinstance(cwd, str) else None
                if not project:
                    _issue(store, "codex_unmapped")
                    continue
                event = make_event(
                    "activity", project["id"], "Codex 活动已发现，摘要待补",
                    id=_id("codex-turn:" + turn), occurred_at=occurred.isoformat(),
                    summary="已发现一次 Codex 工作活动；尚无结构化工作小结。",
                    source={"kind": "codex", "thread_id": meta["id"], "turn_id": turn,
                            "device_id": store.config["device_id"]},
                )
                try:
                    store.add_event(event, eligible=True)
                    count += 1
                except ValueError:
                    _issue(store, "codex_schema")
            prefix_length = min(offset, 2048)
            prefix = _fingerprint(handle, prefix_length)
            handle.seek(max(0, offset - 2048))
            tail = hashlib.sha256(handle.read(min(2048, offset))).hexdigest()
            store.set_cursor(key, {"offset": offset, "ordinal": ordinal, "meta": meta,
                                   "identity": identity, "prefix_length": prefix_length,
                                   "prefix": prefix, "tail": tail,
                                   "consumed_digest": _fingerprint(handle, offset),
                                   "mtime_ns": stat.st_mtime_ns, "mapping": mapping, "window_days": days,
                                   "parser_version": _PARSER_VERSION})
    except (OSError, ValueError):
        _issue(store, "codex_unreadable")
    return count


def _scan_git(store, cutoff):
    count = 0
    visited = set()
    for project in store.config.get("projects", []):
        for path in project.get("paths", []):
            root = Path(path).expanduser()
            if not root.is_dir() or not (root / ".git").exists():
                continue
            try:
                # No subjects, authors, remotes, filenames or code are read into events.
                proc = subprocess.run(
                    ["git", "-C", str(root), "log", "--all", "--since=" + cutoff.isoformat(),
                     "--format=%H%x09%cI"], capture_output=True, text=True, timeout=20,
                )
                if proc.returncode:
                    if (root / ".git").exists():
                        _issue(store, "git_unreadable")
                    continue
                for row in proc.stdout.splitlines():
                    commit, stamp = row.split("\t", 1)
                    occurred = _time(stamp)
                    logical = project["id"] + ":" + commit
                    if logical in visited or occurred is None or occurred < cutoff:
                        continue
                    visited.add(logical)
                    event = make_event(
                        "activity", project["id"], "Git 提交已发现，摘要待补",
                        id=_id("git:" + logical), occurred_at=occurred.isoformat(),
                        summary="已发现一条 Git 提交；提交存在不代表测试或实验通过。",
                        source={"kind": "git", "commit": commit,
                                "device_id": store.config["device_id"]},
                    )
                    store.add_event(event, eligible=True)
                    count += 1
            except (OSError, ValueError, subprocess.TimeoutExpired):
                _issue(store, "git_unreadable")
    return count


def scan(store, days=7):
    """Import metadata only; all project privacy decisions remain in Store."""
    if not isinstance(days, int) or not 1 <= days <= 3650:
        raise ValueError("days must be between 1 and 3650")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    count = 0
    files = set()
    for home in store.config.get("codex_homes", []):
        home = Path(home).expanduser()
        for name in ("sessions", "archived_sessions"):
            root = home / name
            if not root.exists():
                continue
            try:
                files.update(root.rglob("*.jsonl"))
            except OSError:
                _issue(store, "codex_unreadable")
    summarized = {event.get("source", {}).get("turn_id") for event in store.events()
                  if event["kind"] in {"worklog", "checkpoint", "revision"}}
    for path in sorted(files):
        count += _scan_file(store, path, cutoff, days, summarized)
    git_count = _scan_git(store, cutoff)
    return {"files": len(files), "activities_seen": count, "git_commits_seen": git_count}
