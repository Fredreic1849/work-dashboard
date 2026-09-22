"""Exercise documented local commands without a cloud account or real Keychain."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from worklog import cli
from worklog.core import Store

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "state"
        self.remote = self.root / "journal.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.remote)],
                       check=True, capture_output=True)
        self.command("init", "--remote", str(self.remote), "--device", "Test Mac", "--primary")

    def tearDown(self):
        self.temp.cleanup()

    def command(self, *args, body=None, expected=0):
        result = subprocess.run([sys.executable, "-m", "worklog", "--home", str(self.home), *args],
                                cwd=ROOT, input=json.dumps(body) if body is not None else None,
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, expected, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return json.loads(result.stdout if expected == 0 else result.stderr)

    def project(self, identifier, policy):
        return self.command("project", "add", "--id", identifier, "--name", identifier,
                            "--path", str(self.root / identifier), "--policy", policy)

    def test_local_skill_workflow_and_policy_gate(self):
        for name, policy in (("personal", "auto"), ("work", "review"), ("private", "local_only")):
            self.project(name, policy)
        personal = self.command("record", "--project", "personal", "--title", "本地检查通过",
                                "--result", "实验结果待测", "--next-action", "运行正式评测")
        idea = self.command("idea", "--json", body={
            "project_id": "work", "title": "一个待验证想法", "hypothesis": "单独改变一个变量",
            "validation": "完成一次对照实验",
        })
        private = self.command("checkpoint", "--project", "private", "--title", "本地接力")
        self.assertEqual(personal["upload"], "pending")
        self.assertEqual(idea["upload"], "held locally")
        held = self.command("review")["held_records"]
        self.assertEqual({event["id"] for event in held}, {idea["id"], private["id"]})
        self.command("review", "--approve", private["id"], expected=1)
        self.command("review", "--approve", idea["id"])
        status = self.command("status")
        self.assertEqual((status["pending_upload"], status["held_local"]), (2, 1))
        records = self.command("context", "--project", "work")["records"]
        self.assertEqual(records[0]["kind"], "idea")
        self.assertEqual(records[0]["idea_status"], "proposed")
        self.assertEqual(records[0]["evidence_level"], "unverified")

    def test_restrictive_policy_revokes_unsent_uploads(self):
        self.project("personal", "auto")
        self.command("record", "--project", "personal", "--title", "尚未上传")
        self.assertEqual(self.command("status")["pending_upload"], 1)
        self.project("personal", "local_only")
        status = self.command("status")
        self.assertEqual((status["pending_upload"], status["held_local"]), (0, 1))

    def test_flags_override_json_fields_without_duplicate_arguments(self):
        self.project("personal", "auto")
        self.command("record", "--json", "--project", "personal", "--title", "明确的小结",
                     body={"project_id": "unused", "title": "旧标题", "summary": "一个清晰结果"})
        record = self.command("context", "--project", "personal")["records"][0]
        self.assertEqual(record["title"], "明确的小结")

    def test_same_structured_record_replay_is_idempotent(self):
        self.project("personal", "auto")
        body = {"id": "worklog:stable-turn:result", "project_id": "personal", "title": "可重放的小结", "summary": "同一份内容"}
        first = self.command("record", "--json", body=body)
        second = self.command("record", "--json", body=body)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.command("status")["local_records"], 1)

    def test_invalid_json_source_fails_without_traceback_or_saved_record(self):
        self.project("personal", "auto")
        result = self.command("record", "--json", body={
            "project_id": "personal", "title": "不应保存", "source": [],
        }, expected=1)
        self.assertIn("error", result)
        self.assertEqual(self.command("status")["local_records"], 0)

    def test_installed_symlink_runs_outside_source_checkout(self):
        alias = self.root / "bin" / "worklog"
        alias.parent.mkdir()
        alias.symlink_to(ROOT / "bin" / "worklog")
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        result = subprocess.run([str(alias), "--home", str(self.home), "status"],
                                cwd=self.root, env=environment, text=True,
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["device"], "Test Mac")

    def test_local_evidence_hashes_file_without_exporting_path_or_content(self):
        self.project("personal", "auto")
        content = b"synthetic private result body\n"
        evidence_file = self.root / "private-result.json"
        evidence_file.write_bytes(content)
        self.command("record", "--project", "personal", "--title", "检查有依据",
                     "--evidence-file", str(evidence_file), "--evidence-level", "local",
                     "--thread-id", "test-thread", "--turn-id", "test-turn")
        store = Store(self.home)
        try:
            event = store.events(eligible_only=True)[0]
            evidence = event["evidence"][0]
            self.assertEqual(evidence["digest"], hashlib.sha256(content).hexdigest())
            self.assertEqual(evidence["device_id"], store.config["device_id"])
            self.assertEqual(event["evidence_level"], "local")
            self.assertEqual(event["source"]["turn_id"], "test-turn")
            payload = json.dumps(event)
            self.assertNotIn(str(evidence_file), payload)
            self.assertNotIn(content.decode().strip(), payload)
            saved_path = store.db.execute("SELECT path FROM evidence WHERE id=?", (evidence["id"],)).fetchone()[0]
            self.assertEqual(Path(saved_path), evidence_file.resolve())
        finally:
            store.close()

    def test_malformed_json_types_fail_cleanly(self):
        self.project("personal", "auto")
        patches = [
            {"kind": []},
            {"priority": []},
            {"evidence_level": []},
            {"idea_status": []},
            {"evidence": [{"id": "proof", "device_id": "test-device", "kind": "local", "label": "测试", "digest": None}]},
        ]
        for fields in patches:
            with self.subTest(field=next(iter(fields))):
                self.command("record", "--json", body={
                    "project_id": "personal", "title": "无效输入", **fields,
                }, expected=1)
        self.assertEqual(self.command("status")["local_records"], 0)

    def test_auth_stores_token_only_through_credential_helper_stdin(self):
        store = Store(self.home)
        store.config["repo"] = "example/work-journal"
        token = "github_pat_" + "SYNTHETIC" * 6
        responses = [
            io.StringIO(json.dumps({"id": 123, "login": "example"})),
            io.StringIO(json.dumps({"private": True, "owner": {"id": 123}, "permissions": {"push": True}})),
        ]
        try:
            with patch.object(cli.sys, "platform", "darwin"), \
                 patch.object(cli.getpass, "getpass", return_value=token), \
                 patch.object(cli.urllib.request, "urlopen", side_effect=responses) as request, \
                 patch.object(cli, "run_git") as git:
                result = cli.authenticate(store, argparse.Namespace(token_stdin=False))
            self.assertTrue(result["authenticated"])
            self.assertEqual(git.call_args.args[1:], ("credential", "approve"))
            self.assertIn("password=" + token, git.call_args.kwargs["input"])
            self.assertNotIn(token, json.dumps(store.config))
            self.assertNotIn(token, json.dumps(result))
            self.assertNotIn(token, (self.home / "config.json").read_text())
            for call in request.call_args_list:
                self.assertEqual(call.args[0].host, "api.github.com")
                self.assertNotIn(token, call.args[0].full_url)
        finally:
            store.close()

    def test_auth_rejects_public_or_other_owners_repository(self):
        store = Store(self.home)
        store.config["repo"] = "example/work-journal"
        try:
            for private, owner_id in ((False, 123), (True, 456)):
                responses = [
                    io.StringIO(json.dumps({"id": 123, "login": "example"})),
                    io.StringIO(json.dumps({"private": private, "owner": {"id": owner_id}, "permissions": {"push": True}})),
                ]
                with self.subTest(private=private, owner=owner_id), \
                     patch.object(cli.sys, "platform", "darwin"), \
                     patch.object(cli.getpass, "getpass", return_value="synthetic-token"), \
                     patch.object(cli.urllib.request, "urlopen", side_effect=responses), \
                     patch.object(cli, "run_git") as git:
                    with self.assertRaises(ValueError):
                        cli.authenticate(store, argparse.Namespace(token_stdin=False))
                    git.assert_not_called()
        finally:
            store.close()

    def test_launchagent_uses_scoped_home_and_preserves_data_on_uninstall(self):
        store = Store(self.home)
        try:
            completed = subprocess.CompletedProcess([], 0)
            with patch.object(cli.sys, "platform", "darwin"), \
                 patch.object(cli.Path, "home", return_value=self.root), \
                 patch.object(cli.subprocess, "run", return_value=completed):
                result = cli.service(store, "install")
                self.assertTrue(result["verified"])
                plist = next((self.root / "Library/LaunchAgents").glob("*.plist"))
                definition = plistlib.loads(plist.read_bytes())
                args = definition["ProgramArguments"]
                self.assertEqual(Path(args[args.index("--home") + 1]), self.home.resolve())
                self.assertTrue(definition["RunAtLoad"])
                self.assertEqual(definition["StartInterval"], 300)
                cli.service(store, "uninstall")
                self.assertFalse(plist.exists())
                self.assertTrue((self.home / "state.sqlite").exists())
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
