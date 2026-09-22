"""Small append-only Git outbox. Git and HTTP diagnostics never leave this module."""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .core import canonical, now, safe_text, validate_event


class SyncError(RuntimeError):
    """Only fixed, nonsensitive diagnostic strings may be used here."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise SyncError("Repository moved; register its verified current address before syncing.")


def _git(repo, *args, input=None, check=True):
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], input=input, text=True,
            capture_output=True, timeout=45,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true",
                 "GIT_COMMITTER_NAME": "Worklog", "GIT_COMMITTER_EMAIL": "worklog@users.noreply.github.com"},
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SyncError("Git operation unavailable or timed out.") from None
    if check and result.returncode:
        raise SyncError("Git operation failed; local records are retained.")
    return result


def _repo(store):
    repo = Path(store.config["repo_path"]).expanduser().resolve()
    if not (repo / ".git").is_dir():
        raise SyncError("A dedicated journal clone must be initialized first.")
    return repo


@contextmanager
def _lock(repo):
    target = repo / ".git" / "worklog.lock"
    if target.is_symlink():
        raise SyncError("Journal lock path is unsafe.")
    with target.open("a") as handle:
        os.chmod(target, 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SyncError("Another journal operation is already running.") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _verify_remote(store, repo):
    remote = _git(repo, "remote", "get-url", "origin").stdout.strip()
    if not store.config.get("remote") or remote != store.config["remote"]:
        raise SyncError("Journal origin differs from the registered remote.")
    parsed = urlsplit(remote)
    if store.config.get("local_repo") and (not parsed.scheme or parsed.scheme == "file"):
        return
    if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SyncError("Journal remote must be a registered HTTPS GitHub repository.")
    match = re.fullmatch(r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", parsed.path)
    if not match:
        raise SyncError("Journal remote format is unsupported.")
    owner, name = match.groups()
    credential = _git(repo, "credential", "fill", input="protocol=https\nhost=github.com\npath=" + parsed.path.lstrip("/") + "\n\n", check=False)
    values = dict(line.split("=", 1) for line in credential.stdout.splitlines() if "=" in line)
    token = values.get("password")
    if credential.returncode or not token:
        raise SyncError("Journal write credential is unavailable in the configured credential helper.")
    request = urllib.request.Request(
        "https://api.github.com/repos/" + owner + "/" + name,
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "worklog-private-journal"},
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=20) as response:
            metadata = json.load(response)
        if metadata.get("private") is not True or metadata.get("permissions", {}).get("push") is not True:
            raise SyncError("Journal must remain private and the device credential must allow writing.")
    except SyncError:
        raise
    except Exception:
        raise SyncError("Private repository verification failed; nothing was uploaded.") from None


def _device(store):
    device = store.config.get("device_id", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", device):
        raise SyncError("Device identifier is invalid.")
    safe_text(store.config.get("device_name", "Mac"), 160)
    return device


def _safe_directory(repo, relative):
    target = repo
    for part in relative.parts:
        target = target / part
        if target.is_symlink():
            raise SyncError("Journal output contains an unsafe symbolic link.")
        target.mkdir(exist_ok=True)
    return target


def _read_events(path):
    if path.is_symlink():
        raise SyncError("Journal data cannot use symbolic links.")
    try:
        return [validate_event(json.loads(line)) for line in path.read_text().splitlines() if line.strip()]
    except (OSError, ValueError, TypeError, KeyError):
        raise SyncError("Existing journal data is invalid; it was not overwritten.") from None


def _write_json(path, value):
    if path.is_symlink():
        raise SyncError("Journal data cannot use symbolic links.")
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path.exists() and path.read_text() == text:
        return
    temporary = path.with_name(path.name + ".tmp")
    if temporary.is_symlink():
        raise SyncError("Journal output contains an unsafe temporary file.")
    temporary.write_text(text)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _materialize(store, repo, device):
    directory = _safe_directory(repo, Path("devices") / device)
    days_dir = _safe_directory(repo, Path("devices") / device / "days")
    by_day = {}
    for path in sorted(days_dir.iterdir()):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.jsonl", path.name):
            raise SyncError("Unexpected file in device journal directory.")
        by_day[path.stem] = _read_events(path)
        if any(event["day"] != path.stem for event in by_day[path.stem]):
            raise SyncError("Existing journal records do not match their day file.")
    for event in store.events(eligible_only=True):
        event = validate_event(event)
        records = by_day.setdefault(event["day"], [])
        old = next((row for row in records if row["id"] == event["id"]), None)
        if old and canonical(old) != canonical(event):
            raise SyncError("A journal record conflicts with the local record; both are retained.")
        if not old:
            records.append(event)
    all_events = []
    for day, records in sorted(by_day.items()):
        path = days_dir / (day + ".jsonl")
        records.sort(key=lambda event: (event["occurred_at"], event["id"]))
        text = "".join(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n" for event in records)
        if not path.exists() or path.read_text() != text:
            temporary = path.with_suffix(".tmp")
            if temporary.is_symlink():
                raise SyncError("Journal output contains an unsafe temporary file.")
            temporary.write_text(text)
            os.chmod(temporary, 0o600)
            temporary.replace(path)
        all_events.extend(records)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
    snapshot = {
        "schema_version": 1, "device": {"id": device, "name": store.config.get("device_name", "Mac")},
        "days": sorted(by_day), "coverage_start": min(by_day, default=None),
        "events": [event for event in all_events if event["day"] >= cutoff or event["kind"] in {"idea", "checkpoint", "revision", "tombstone"}],
        "issues": [],
    }
    # Local collector issues can disclose unselected project activity. Keep them local.
    snapshot_path = directory / "snapshot.json"
    previous = {}
    if snapshot_path.exists():
        if snapshot_path.is_symlink():
            raise SyncError("Journal data cannot use symbolic links.")
        try:
            previous = json.loads(snapshot_path.read_text())
            if not isinstance(previous, dict) or previous.get("schema_version") != 1:
                raise ValueError()
        except (OSError, ValueError):
            raise SyncError("Existing device index is invalid.") from None
    old_time = previous.pop("generated_at", None)
    snapshot["generated_at"] = old_time if previous == snapshot and old_time else now()
    _write_json(snapshot_path, snapshot)


def _parse_catalog(raw):
    try:
        catalog = json.loads(raw)
        if not isinstance(catalog, dict) or set(catalog) != {"schema_version", "projects"} or catalog.get("schema_version") != 1 or not isinstance(catalog["projects"], list):
            raise ValueError()
        seen = set()
        for project in catalog["projects"]:
            if not isinstance(project, dict) or set(project) - {"id", "name", "goal"}:
                raise ValueError()
            identity = project.get("id", "")
            if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", identity) or identity in seen:
                raise ValueError()
            seen.add(identity)
            safe_text(project.get("name"), 160)
            if "goal" in project:
                safe_text(project["goal"], 2000)
        return catalog
    except (ValueError, TypeError, KeyError):
        raise SyncError("Project catalog is invalid; it was not committed.") from None


def _catalog(store, repo):
    if not store.config.get("primary"):
        return False
    selected = [p for p in store.config.get("projects", []) if p.get("publish") and p.get("policy") != "local_only"]
    target = repo / "projects.json"
    if target.is_symlink():
        raise SyncError("Project catalog cannot use symbolic links.")
    if not selected and not target.exists():
        return False
    try:
        catalog = _parse_catalog(target.read_text()) if target.exists() else {"schema_version": 1, "projects": []}
        existing = {p["id"]: p for p in catalog["projects"]}
        for project in selected:
            entry = {"id": project["id"], "name": safe_text(project["name"], 160)}
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", entry["id"]):
                raise ValueError()
            if project.get("goal"):
                entry["goal"] = safe_text(project["goal"], 2000)
            if entry["id"] in existing and existing[entry["id"]].get("name") != entry["name"]:
                raise SyncError("A published project name conflicts with the registered project.")
            existing[entry["id"]] = entry
        catalog["projects"] = sorted(existing.values(), key=lambda p: p["id"])
    except SyncError:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise SyncError("Project catalog is invalid; it was not overwritten.") from None
    _write_json(target, catalog)
    return True


def _check_dirty(repo, device, allow_catalog=False):
    paths = set()
    for args in (("diff", "--name-only", "-z"), ("diff", "--cached", "--name-only", "-z"), ("ls-files", "--others", "--exclude-standard", "-z")):
        paths.update(p for p in _git(repo, *args).stdout.split("\0") if p)
    pattern = r"devices/" + re.escape(device) + r"/(?:snapshot\.json|days/\d{4}-\d{2}-\d{2}\.jsonl)"
    for path in paths:
        if not re.fullmatch(pattern, path) and not (allow_catalog and path == "projects.json"):
            raise SyncError("The journal clone contains unrelated changes; sync stopped without overwriting them.")


def _guard_held_history(store, repo, device):
    """Revoked outbox content must not survive inside an unpublished Git commit."""
    held = {row[0] for row in store.db.execute("SELECT id FROM events WHERE eligible=0 AND synced=0")}
    if not held:
        return
    relative = "devices/" + device + "/days"
    candidates = []
    working = repo / relative
    if working.is_dir() and not working.is_symlink():
        for path in working.glob("*.jsonl"):
            if path.is_symlink():
                raise SyncError("Journal data cannot use symbolic links.")
            candidates.append(path.read_text())
    arguments = ["rev-list", "HEAD"]
    if _git(repo, "rev-parse", "--verify", "refs/remotes/origin/main", check=False).returncode == 0:
        arguments += ["--not", "refs/remotes/origin/main"]
    for commit in _git(repo, *arguments).stdout.splitlines():
        for path in _git(repo, "ls-tree", "-r", "--name-only", commit, "--", relative).stdout.splitlines():
            candidates.append(_git(repo, "show", commit + ":" + path).stdout)
    for content in candidates:
        for line in content.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                raise SyncError("Unpublished journal history contains invalid records.") from None
            if isinstance(event, dict) and event.get("id") in held:
                raise SyncError("Unpublished journal history contains records now held by privacy policy. Resolve the local history before syncing.")


def _guard_catalog_history(store, repo):
    if not store.config.get("primary"):
        return
    blocked = {p["id"] for p in store.config.get("projects", []) if p.get("policy") == "local_only" or not p.get("publish")}
    if not blocked:
        return
    remote = _git(repo, "show", "refs/remotes/origin/main:projects.json", check=False)
    baseline = {p["id"]: p for p in _parse_catalog(remote.stdout)["projects"]} if remote.returncode == 0 else {}
    candidates = []
    target = repo / "projects.json"
    if target.is_symlink():
        raise SyncError("Project catalog cannot use symbolic links.")
    if target.exists():
        candidates.append(target.read_text())
    arguments = ["rev-list", "HEAD"]
    if _git(repo, "rev-parse", "--verify", "refs/remotes/origin/main", check=False).returncode == 0:
        arguments += ["--not", "refs/remotes/origin/main"]
    for commit in _git(repo, *arguments).stdout.splitlines():
        catalog = _git(repo, "show", commit + ":projects.json", check=False)
        if catalog.returncode == 0:
            candidates.append(catalog.stdout)
    for raw in candidates:
        for project in _parse_catalog(raw)["projects"]:
            if project["id"] in blocked and baseline.get(project["id"]) != project:
                raise SyncError("Unpublished project catalog includes a project no longer approved for publication. Resolve local history before syncing.")


def _commit(repo, device, catalog=False):
    _check_dirty(repo, device, allow_catalog=catalog)
    paths = ["devices/" + device]
    if catalog:
        paths.append("projects.json")
    paths = [path for path in paths if (repo / path).exists()]
    if not paths:
        return
    _git(repo, "add", "--", *paths)
    if _git(repo, "diff", "--cached", "--quiet", check=False).returncode:
        _git(repo, "-c", "user.name=Worklog", "-c", "user.email=worklog@users.noreply.github.com", "commit", "-m", "Update private work journal")


def _fetch_rebase(repo):
    _git(repo, "fetch", "origin")
    if _git(repo, "rev-parse", "--verify", "refs/remotes/origin/main", check=False).returncode == 0:
        result = _git(repo, "rebase", "refs/remotes/origin/main", check=False)
        if result.returncode:
            _git(repo, "rebase", "--abort", check=False)
            raise SyncError("Journal histories conflict; local commits were retained and no force push was used.")


def _receipt(repo, device, pending):
    _git(repo, "fetch", "origin")
    head = _git(repo, "rev-parse", "refs/remotes/origin/main").stdout.strip()
    if _git(repo, "merge-base", "--is-ancestor", "HEAD", head, check=False).returncode:
        raise SyncError("Remote receipt does not contain the uploaded commit.")
    received = {}
    for day in {event["day"] for event in pending}:
        raw = _git(repo, "show", head + ":devices/" + device + "/days/" + day + ".jsonl").stdout
        try:
            for line in raw.splitlines():
                event = validate_event(json.loads(line))
                received[event["id"]] = event
        except (ValueError, TypeError, KeyError):
            raise SyncError("Remote receipt is invalid; records remain queued.") from None
    if any(event["id"] not in received or canonical(event) != canonical(received[event["id"]]) for event in pending):
        raise SyncError("Remote receipt did not include all pending records.")
    return head


def sync(store, retries=3):
    """A record is synced only after an authenticated remote readback."""
    try:
        repo, device = _repo(store), _device(store)
        with _lock(repo):
            if _git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip() != "main":
                raise SyncError("The journal clone must be on its main branch.")
            _verify_remote(store, repo)
            _check_dirty(repo, device, allow_catalog=bool(store.config.get("primary")))
            _guard_held_history(store, repo, device)
            _guard_catalog_history(store, repo)
            # A record arriving while we upload belongs to the next receipt batch.
            pending = store.pending()
            # Recover a prior interruption between atomic file writes and commit.
            _materialize(store, repo, device)
            catalog = _catalog(store, repo)
            _commit(repo, device, catalog)
            _fetch_rebase(repo)
            _materialize(store, repo, device)
            catalog = _catalog(store, repo)
            _commit(repo, device, catalog)
            error = None
            for attempt in range(max(1, min(retries, 5))):
                try:
                    if attempt:
                        _fetch_rebase(repo)
                    _guard_held_history(store, repo, device)
                    _guard_catalog_history(store, repo)
                    _git(repo, "push", "origin", "HEAD:refs/heads/main")
                    head = _receipt(repo, device, pending)
                    store.mark_synced([event["id"] for event in pending])
                    store.set_cursor("sync.receipt", {"at": now(), "commit": head, "count": len(pending)})
                    store.clear_issue("sync_failed")
                    return {"ok": True, "synced": len(pending), "commit": head}
                except SyncError as exc:
                    error = exc
                    if attempt + 1 < max(1, min(retries, 5)):
                        time.sleep(min(2 ** attempt, 4))
            raise error
    except (SyncError, OSError, ValueError, KeyError) as exc:
        # Never surface raw Git/HTTP exceptions, URLs, paths or credentials.
        message = str(exc) if isinstance(exc, SyncError) else "Journal state is invalid or unavailable."
        store.issue("sync_failed", message)
        return {"ok": False, "synced": 0, "error": message}


def backup(store):
    """Create a weekly bundle and verify it by restoring an actual bare clone."""
    last = store.cursor("backup.verified")
    if last:
        try:
            stamp = datetime.fromisoformat(last["at"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - stamp < timedelta(days=7):
                return {"ok": True, "created": False, "verified_at": last["at"]}
        except (KeyError, ValueError, TypeError):
            pass
    try:
        repo = _repo(store)
        with _lock(repo):
            commit = _git(repo, "rev-parse", "HEAD").stdout.strip()
            target_dir = store.home / "backups"
            target_dir.mkdir(exist_ok=True, mode=0o700)
            os.chmod(target_dir, 0o700)
            target = target_dir / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + commit[:12] + ".bundle")
            _git(repo, "bundle", "create", str(target), "--all")
            os.chmod(target, 0o600)
            _git(repo, "bundle", "verify", str(target))
            with tempfile.TemporaryDirectory(prefix="worklog-restore-") as temp:
                restored = Path(temp) / "restored.git"
                _git(repo, "clone", "--bare", str(target), str(restored))
                _git(restored, "fsck", "--full")
                actual = _git(restored, "rev-parse", "HEAD").stdout.strip()
                if actual != commit:
                    raise SyncError("Backup restore did not match the source commit.")
            stamp = now()
            store.set_cursor("backup.verified", {"at": stamp, "commit": commit})
            store.clear_issue("backup_failed")
            return {"ok": True, "created": True, "verified_at": stamp}
    except (SyncError, OSError, ValueError, KeyError):
        store.issue("backup_failed", "The local journal backup could not be verified; previous backups were retained.")
        return {"ok": False, "created": False, "error": "Backup verification failed."}
