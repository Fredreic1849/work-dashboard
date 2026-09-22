import json
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from worklog import git_sync
from worklog.core import Store, make_event


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


class GitSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote.git"
        subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(self.remote)], check=True, capture_output=True)
        seed = self.root / "seed"
        git(self.root, "clone", str(self.remote), str(seed))
        (seed / "projects.json").write_text('{"schema_version":1,"projects":[]}\n')
        git(seed, "add", ".")
        git(seed, "-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-m", "Initialize")
        git(seed, "push", "origin", "main")
        self.stores = []
        self.store = self.device("mac-a")

    def device(self, identity):
        state = Store(self.root / identity)
        repo = self.root / (identity + "-clone")
        git(self.root, "clone", str(self.remote), str(repo))
        state.config.update({"device_id": identity, "device_name": identity, "repo_path": str(repo),
                             "remote": str(self.remote), "local_repo": True,
                             "projects": [{"id": "demo", "name": "Demo", "paths": [], "policy": "auto"}]})
        state.save_config()
        self.stores.append(state)
        return state

    def tearDown(self):
        for store in self.stores:
            store.db.close()
        self.temp.cleanup()

    def record(self, store=None, **kwargs):
        store = store or self.store
        event = make_event("worklog", "demo", "A completed local edit", **kwargs)
        event_id = store.add_event(event, eligible=True)
        return next(event for event in store.events() if event["id"] == event_id)

    def remote_file(self, relative):
        return git(self.remote, "show", "main:" + relative)

    def test_success_requires_receipt_and_repeat_has_no_commit(self):
        event = self.record()
        result = git_sync.sync(self.store)
        self.assertTrue(result["ok"])
        self.assertEqual([], self.store.pending())
        content = self.remote_file("devices/mac-a/days/" + event["day"] + ".jsonl")
        self.assertEqual(event["id"], json.loads(content)["id"])
        first = git(self.remote, "rev-parse", "main")
        self.assertTrue(git_sync.sync(self.store)["ok"])
        self.assertEqual(first, git(self.remote, "rev-parse", "main"))

    def test_first_sync_from_init_preserves_existing_remote_history(self):
        repo = self.root / "fresh-init"
        repo.mkdir()
        git(repo, "init", "--initial-branch=main")
        git(repo, "remote", "add", "origin", str(self.remote))
        self.store.config.update(repo_path=str(repo), primary=True)
        self.store.config["projects"][0]["publish"] = True
        before = git(self.remote, "rev-parse", "main")
        event = self.record()
        result = git_sync.sync(self.store)
        self.assertTrue(result["ok"], result)
        self.assertEqual([], self.store.pending())
        git(self.remote, "merge-base", "--is-ancestor", before, "main")
        catalog = json.loads(self.remote_file("projects.json"))
        self.assertEqual(["demo"], [entry["id"] for entry in catalog["projects"]])
        self.assertIn(event["id"], self.remote_file("devices/mac-a/days/" + event["day"] + ".jsonl"))

    def test_fetch_preserves_a_resolved_initial_history_merge(self):
        repo = self.root / "resolved-init"
        repo.mkdir()
        git(repo, "init", "--initial-branch=main")
        git(repo, "config", "user.name", "Test")
        git(repo, "config", "user.email", "test@example.org")
        git(repo, "remote", "add", "origin", str(self.remote))
        catalog = {"schema_version": 1, "projects": []}
        (repo / "projects.json").write_text(json.dumps(catalog, indent=2))
        git(repo, "add", "projects.json")
        git(repo, "commit", "-m", "Local initialization")
        git(repo, "fetch", "origin")
        conflict = subprocess.run(["git", "-C", str(repo), "merge", "--no-commit", "--allow-unrelated-histories", "origin/main"], capture_output=True)
        self.assertNotEqual(0, conflict.returncode)
        (repo / "projects.json").write_text(self.remote_file("projects.json") + "\n")
        git(repo, "add", "projects.json")
        git(repo, "commit", "-m", "Preserve both initial histories")
        merged = git(repo, "rev-parse", "HEAD")
        git_sync._fetch_rebase(repo)
        self.assertEqual(merged, git(repo, "rev-parse", "HEAD"))

    def test_offline_queue_survives_then_catches_up(self):
        event = self.record(occurred_at=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat())
        original = git_sync._git
        def disconnected(repo, *args, **kwargs):
            if args and args[0] == "fetch":
                raise git_sync.SyncError("Network unavailable.")
            return original(repo, *args, **kwargs)
        with patch.object(git_sync, "_git", side_effect=disconnected):
            self.assertFalse(git_sync.sync(self.store, retries=1)["ok"])
        self.assertEqual(1, len(self.store.pending()))
        self.assertTrue(git_sync.sync(self.store)["ok"])
        self.assertIn(event["id"], self.remote_file("devices/mac-a/days/" + event["day"] + ".jsonl"))

    def test_successful_push_with_lost_response_is_idempotent(self):
        event = self.record()
        original = git_sync._git
        lost = [False]
        def response_lost(repo, *args, **kwargs):
            result = original(repo, *args, **kwargs)
            if args and args[0] == "push" and not lost[0]:
                lost[0] = True
                raise git_sync.SyncError("Push response lost.")
            return result
        with patch.object(git_sync, "_git", side_effect=response_lost), patch.object(git_sync.time, "sleep"):
            self.assertTrue(git_sync.sync(self.store)["ok"])
        raw = self.remote_file("devices/mac-a/days/" + event["day"] + ".jsonl")
        self.assertEqual(1, len(raw.splitlines()))

    def test_two_devices_concurrent_writes_both_survive(self):
        second = self.device("mac-b")
        self.record()
        self.record(second)
        homes = [self.store.home, second.home]
        def synchronize(home):
            store = Store(home)
            try:
                return git_sync.sync(store)
            finally:
                store.db.close()
        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(git_sync.time, "sleep"):
            results = list(pool.map(synchronize, homes))
        self.assertTrue(all(r["ok"] for r in results), results)
        for identity in ("mac-a", "mac-b"):
            self.assertEqual(identity, json.loads(self.remote_file("devices/" + identity + "/snapshot.json"))["device"]["id"])

    def test_review_and_local_only_records_do_not_enter_commits(self):
        for policy in ("review", "local_only"):
            self.store.config["projects"].append({"id": policy, "name": policy, "paths": [], "policy": policy, "publish": True})
            self.store.add_event(make_event("worklog", policy, "Restricted record"), eligible=True)
        self.store.config["primary"] = True
        self.store.config["projects"][0]["publish"] = True
        self.record()
        self.assertTrue(git_sync.sync(self.store)["ok"])
        snapshot = json.loads(self.remote_file("devices/mac-a/snapshot.json"))
        self.assertEqual(["demo"], [e["project_id"] for e in snapshot["events"]])
        catalog = json.loads(self.remote_file("projects.json"))
        self.assertNotIn("local_only", [p["id"] for p in catalog["projects"]])
        # A review project's catalog requires an explicit publish choice; its records still stay local.
        self.assertIn("review", [p["id"] for p in catalog["projects"]])

    def test_catalog_preserves_unknown_existing_entries(self):
        repo = Path(self.store.config["repo_path"])
        (repo / "projects.json").write_text('{"schema_version":1,"projects":[{"id":"other","name":"Other"}]}')
        git(repo, "add", "projects.json")
        git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-m", "Other project")
        git(repo, "push", "origin", "main")
        self.store.config["primary"] = True
        self.store.config["projects"][0]["publish"] = True
        self.assertTrue(git_sync.sync(self.store)["ok"])
        self.assertEqual({"demo", "other"}, {p["id"] for p in json.loads(self.remote_file("projects.json"))["projects"]})

    def test_changed_origin_and_unrelated_dirty_files_are_rejected(self):
        self.record()
        repo = Path(self.store.config["repo_path"])
        (repo / "unrelated.txt").write_text("private draft")
        self.assertFalse(git_sync.sync(self.store)["ok"])
        self.assertEqual(1, len(self.store.pending()))
        (repo / "unrelated.txt").unlink()
        git(repo, "remote", "set-url", "origin", str(self.root / "wrong.git"))
        self.assertFalse(git_sync.sync(self.store)["ok"])

    def test_backup_is_restored_verified_and_weekly(self):
        self.record()
        self.assertTrue(git_sync.sync(self.store)["ok"])
        result = git_sync.backup(self.store)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["created"])
        self.assertEqual(1, len(list((self.store.home / "backups").glob("*.bundle"))))
        self.assertFalse(git_sync.backup(self.store)["created"])

    def test_snapshot_keeps_old_ideas_but_history_is_on_demand(self):
        stamp = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        self.record(occurred_at=stamp)
        self.store.add_event(make_event("idea", "demo", "An old live idea", occurred_at=stamp), eligible=True)
        self.assertTrue(git_sync.sync(self.store)["ok"])
        snapshot = json.loads(self.remote_file("devices/mac-a/snapshot.json"))
        self.assertEqual(["idea"], [e["kind"] for e in snapshot["events"]])
        self.assertEqual(1, len(snapshot["days"]))

    def test_missing_remote_receipt_never_marks_event_synced(self):
        self.record()
        with patch.object(git_sync, "_receipt", side_effect=git_sync.SyncError("Receipt unavailable.")):
            self.assertFalse(git_sync.sync(self.store, retries=1)["ok"])
        self.assertEqual(1, len(self.store.pending()))
        self.assertTrue(git_sync.sync(self.store)["ok"])

    def test_record_arriving_during_upload_waits_for_next_batch(self):
        first = self.record()
        original = git_sync._materialize
        calls = [0]
        late = []
        def materialize(*args):
            original(*args)
            calls[0] += 1
            if calls[0] == 2:
                late.append(self.record())
        with patch.object(git_sync, "_materialize", side_effect=materialize):
            self.assertTrue(git_sync.sync(self.store)["ok"])
        self.assertEqual([late[0]["id"]], [e["id"] for e in self.store.pending()])
        self.assertNotIn(first["id"], [e["id"] for e in self.store.pending()])
        self.assertTrue(git_sync.sync(self.store)["ok"])

    def test_privacy_tightening_after_failed_push_blocks_unpublished_history(self):
        event = self.record()
        original = git_sync._git
        def push_fails(repo, *args, **kwargs):
            if args and args[0] == "push":
                raise git_sync.SyncError("Offline.")
            return original(repo, *args, **kwargs)
        with patch.object(git_sync, "_git", side_effect=push_fails):
            self.assertFalse(git_sync.sync(self.store, retries=1)["ok"])
        repo = Path(self.store.config["repo_path"])
        unpublished = git(repo, "rev-parse", "HEAD")
        self.store.config["projects"][0]["policy"] = "review"
        with self.store.db:
            self.store.db.execute("UPDATE events SET eligible=0 WHERE id=?", (event["id"],))
        pushes = []
        def traced(repo, *args, **kwargs):
            if args and args[0] == "push":
                pushes.append(args)
            return original(repo, *args, **kwargs)
        with patch.object(git_sync, "_git", side_effect=traced):
            result = git_sync.sync(self.store)
        self.assertFalse(result["ok"])
        self.assertIn("privacy policy", result["error"])
        self.assertEqual([], pushes)
        self.assertEqual(unpublished, git(repo, "rev-parse", "HEAD"))
        self.assertNotEqual(unpublished, git(self.remote, "rev-parse", "main"))

    def test_catalog_policy_tightening_blocks_unpublished_names(self):
        self.store.config["primary"] = True
        self.store.config["projects"][0]["publish"] = True
        original = git_sync._git
        def offline(repo, *args, **kwargs):
            if args and args[0] == "push":
                raise git_sync.SyncError("Offline.")
            return original(repo, *args, **kwargs)
        with patch.object(git_sync, "_git", side_effect=offline):
            self.assertFalse(git_sync.sync(self.store, retries=1)["ok"])
        self.store.config["projects"][0]["policy"] = "local_only"
        before = git(self.remote, "rev-parse", "main")
        result = git_sync.sync(self.store)
        self.assertFalse(result["ok"])
        self.assertIn("no longer approved", result["error"])
        self.assertEqual(before, git(self.remote, "rev-parse", "main"))

    def test_primary_recovers_catalog_write_but_rejects_foreign_fields(self):
        self.store.config["primary"] = True
        self.store.config["projects"][0]["publish"] = True
        repo = Path(self.store.config["repo_path"])
        catalog = {"schema_version": 1, "projects": [{"id": "demo", "name": "Demo"}]}
        (repo / "projects.json").write_text(json.dumps(catalog))
        self.assertTrue(git_sync.sync(self.store)["ok"])
        before = git(self.remote, "rev-parse", "main")
        catalog["projects"][0]["private_notes"] = "This must not enter Git history"
        (repo / "projects.json").write_text(json.dumps(catalog))
        self.assertFalse(git_sync.sync(self.store)["ok"])
        self.assertEqual(before, git(self.remote, "rev-parse", "main"))
        self.assertNotIn("private_notes", self.remote_file("projects.json"))

    def test_initial_local_commit_rebases_onto_unrelated_readme(self):
        remote = self.root / "readme.git"
        git(self.root, "init", "--bare", "--initial-branch=main", str(remote))
        seed = self.root / "readme-seed"
        git(self.root, "clone", str(remote), str(seed))
        (seed / "README.md").write_text("Private journal")
        git(seed, "add", ".")
        git(seed, "-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-m", "Initial README")
        git(seed, "push", "origin", "main")
        repo = self.root / "independent"
        git(self.root, "init", "-b", "main", str(repo))
        (repo / "projects.json").write_text('{"schema_version":1,"projects":[]}')
        git(repo, "add", ".")
        git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-m", "Local initialization")
        git(repo, "remote", "add", "origin", str(remote))
        self.store.config.update({"repo_path": str(repo), "remote": str(remote)})
        self.record()
        self.assertTrue(git_sync.sync(self.store)["ok"])
        self.assertEqual("Private journal", git(remote, "show", "main:README.md"))
        self.assertIn("mac-a", git(remote, "show", "main:devices/mac-a/snapshot.json"))

    def test_lock_prevents_second_writer_to_same_clone(self):
        self.record()
        with git_sync._lock(Path(self.store.config["repo_path"])):
            result = git_sync.sync(self.store)
        self.assertFalse(result["ok"])
        self.assertEqual(1, len(self.store.pending()))

    def test_remote_public_repository_blocks_before_any_commit(self):
        self.record()
        repo = Path(self.store.config["repo_path"])
        remote = "https://github.com/example/work-journal.git"
        git(repo, "remote", "set-url", "origin", remote)
        self.store.config.update({"remote": remote, "local_repo": False})
        original = git_sync._git
        def credential(repo, *args, **kwargs):
            if args == ("credential", "fill"):
                return subprocess.CompletedProcess(args, 0, "username=example\npassword=synthetic-token\n", "")
            return original(repo, *args, **kwargs)
        from io import BytesIO
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value = BytesIO(b'{"private":false,"permissions":{"push":true}}')
        opener = MagicMock()
        opener.open.return_value = response
        before = git(repo, "rev-parse", "HEAD")
        with patch.object(git_sync, "_git", side_effect=credential), patch.object(git_sync.urllib.request, "build_opener", return_value=opener):
            result = git_sync.sync(self.store)
        self.assertFalse(result["ok"])
        self.assertNotIn("synthetic-token", str(result))
        self.assertEqual(before, git(repo, "rev-parse", "HEAD"))
        self.assertEqual(1, len(self.store.pending()))


if __name__ == "__main__":
    unittest.main()
