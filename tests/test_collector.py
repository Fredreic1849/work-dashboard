import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from worklog.collector import scan
from worklog.core import Store, make_event


STAMP = datetime.now(timezone.utc).isoformat()


def line(kind, payload, stamp=STAMP):
    return json.dumps({"type": kind, "payload": payload, "timestamp": stamp}) + "\n"


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.codex = self.root / "codex"
        (self.codex / "sessions").mkdir(parents=True)
        self.store = Store(self.root / "state")
        self.store.config.update({"device_id": "mac-a", "codex_homes": [str(self.codex)],
                                  "projects": [{"id": "project", "name": "Project", "paths": [str(self.project)], "policy": "auto"}]})

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def write(self, name, text):
        path = self.codex / "sessions" / name
        path.write_text(text)
        return path

    def meta(self, identity="thread-one", **extra):
        return line("session_meta", {"id": identity, "cwd": str(self.project), **extra})

    def turn(self, identity="turn-one", **extra):
        return line("turn_context", {"turn_id": identity, "cwd": str(self.project), **extra})

    def issues(self):
        return {row[0] for row in self.store.db.execute("SELECT code FROM issues")}

    def test_partial_line_is_resumed_without_duplicate_or_transcript(self):
        turn = self.turn()
        path = self.write("one.jsonl", self.meta() + turn[:20])
        scan(self.store)
        self.assertEqual([], self.store.events())
        with path.open("a") as handle:
            handle.write(turn[20:] + line("response_item", {"type": "message", "content": "TRAINING COMPLETED ghp_sensitive_token /mnt/private/log"}))
        scan(self.store)
        scan(self.store)
        events = self.store.events()
        self.assertEqual(1, len(events))
        self.assertEqual("unverified", events[0]["evidence_level"])
        self.assertNotIn("sensitive", json.dumps(events))
        self.assertNotIn("TRAINING", json.dumps(events))
        self.assertNotIn(str(self.project), json.dumps(events))

    def test_copied_archived_and_inherited_history_deduplicate(self):
        parent = self.write("parent.jsonl", self.meta() + self.turn())
        archive = self.codex / "archived_sessions"
        archive.mkdir()
        shutil.copy(parent, archive / "copy.jsonl")
        self.write("child.jsonl", self.meta("child", parent_thread_id="thread-one", subagent_history_start_ordinal=3)
                   + self.meta() + self.turn() + self.turn("child-turn"))
        scan(self.store)
        events = self.store.events()
        self.assertEqual(2, len(events))
        child = next(e for e in events if e["source"]["turn_id"] == "child-turn")
        self.assertEqual("child", child["source"]["thread_id"])

    def test_unknown_fork_boundary_and_schema_have_visible_issues(self):
        self.write("fork.jsonl", self.meta("fork", forked_from_id="thread-one") + self.turn())
        self.write("unknown.jsonl", line("session_meta", {"version": 77}) + self.turn("unknown"))
        scan(self.store)
        self.assertEqual([], self.store.events())
        self.assertTrue({"codex_fork_boundary", "codex_schema"}.issubset(self.issues()))

    def test_truncated_file_is_rescanned_with_stable_logical_id(self):
        path = self.write("one.jsonl", self.meta() + self.turn() + self.turn("second"))
        scan(self.store)
        path.write_text(self.meta() + self.turn())
        scan(self.store)
        self.assertEqual(2, len(self.store.events()))
        self.assertIn("codex_rewritten", self.issues())

    def test_longest_registered_mapping_and_review_policy(self):
        nested = self.project / "nested"
        nested.mkdir()
        self.store.config["projects"].append({"id": "restricted", "name": "Restricted", "paths": [str(nested)], "policy": "review"})
        self.write("one.jsonl", self.meta() + self.turn(cwd=str(nested)))
        scan(self.store)
        self.assertEqual("restricted", self.store.events()[0]["project_id"])
        self.assertEqual([], self.store.pending())

    def test_unmapped_and_old_activity_are_not_exported(self):
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        self.write("one.jsonl", self.meta() + line("turn_context", {"turn_id": "old", "cwd": str(self.project)}, old)
                   + self.turn("unmapped", cwd=str(self.root / "unmapped")))
        scan(self.store)
        self.assertEqual([], self.store.events())
        self.assertIn("codex_unmapped", self.issues())

    def test_git_metadata_has_no_commit_subject_author_or_paths(self):
        def git(*args):
            subprocess.run(["git", "-C", str(self.project), *args], check=True, capture_output=True)
        git("init", "-b", "main")
        (self.project / "private-file.txt").write_text("private payload")
        git("add", ".")
        git("-c", "user.name=Sensitive Person", "-c", "user.email=private@example.org", "commit", "-m", "sensitive subject")
        scan(self.store)
        scan(self.store)
        events = self.store.events()
        self.assertEqual(1, len(events))
        raw = json.dumps(events)
        for value in ("Sensitive", "private-file", "private payload", "sensitive subject", "private@example"):
            self.assertNotIn(value, raw)
        self.assertEqual("git", events[0]["source"]["kind"])

    def test_new_mapping_revisits_previously_unclassified_turn(self):
        other = self.root / "other"
        other.mkdir()
        self.write("one.jsonl", self.meta() + self.turn(cwd=str(other)))
        scan(self.store)
        self.assertEqual([], self.store.events())
        self.store.config["projects"][0]["paths"].append(str(other))
        scan(self.store)
        self.assertEqual(1, len(self.store.events()))

    def test_summary_already_exists_so_no_pending_activity_is_added(self):
        self.store.add_event(make_event("worklog", "project", "Completed an edit",
                                       source={"kind": "codex", "thread_id": "thread-one", "turn_id": "turn-one"}))
        self.write("one.jsonl", self.meta() + self.turn())
        scan(self.store)
        self.assertEqual(["worklog"], [e["kind"] for e in self.store.events()])


if __name__ == "__main__":
    unittest.main()
