"""Cloud-bound records must reject secrets and enforce local project policy."""

import copy
from pathlib import Path
import tempfile
import unittest

from worklog.core import Store, make_event, validate_event


class PrivacyTests(unittest.TestCase):
    def event(self, **fields):
        title = fields.pop("title", "完成边界测试")
        return make_event("worklog", "personal", title, **fields)

    def test_plain_summary_is_accepted(self):
        validate_event(self.event(summary="离线队列恢复后已补传", result="本地检查通过"))

    def test_rejects_credentials_in_all_text_fields(self):
        # Synthetic sentinels; none are usable credentials.
        secrets = [
            "ghp_" + "A" * 36,
            "github_pat_" + "A" * 80,
            "sk-proj-" + "A" * 48,
            "-----BEGIN PRIVATE KEY-----",
        ]
        for field in ("title", "summary", "result", "next_action"):
            for secret in secrets:
                with self.subTest(field=field, secret_type=secret[:10]):
                    with self.assertRaises(ValueError):
                        validate_event(self.event(**{field: secret}))

    def test_rejects_internal_addresses_and_absolute_paths(self):
        samples = [
            "连接 10.20.30.40 后检查",
            "访问 192.168.2.3 的结果",
            "访问 172.16.0.9 的结果",
            "证据在 /Users/example/Documents/report.txt",
            "证据在 /home/example/run/output.json",
            "证据在 /data/private/run/output.json",
            "证据在 /srv/project/output.json",
            "结果来自 https://worker.internal/logs",
        ]
        for text in samples:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    validate_event(self.event(summary=text))

    def test_rejects_raw_payloads_and_unknown_fields(self):
        for patch in (
            {"summary": "```python\nprint('raw code')\n```"},
            {"summary": "a" * 2001},
            {"raw_transcript": "private task output"},
            {"api_key": "do-not-export"},
        ):
            event = self.event()
            event.update(patch)
            with self.subTest(fields=tuple(patch)):
                with self.assertRaises(ValueError):
                    validate_event(event)

    def test_rejects_secrets_in_nested_evidence(self):
        event = self.event()
        event["evidence"] = [{
            "id": "e-test",
            "label": "ghp_" + "A" * 36,
            "device_id": "test-device",
            "kind": "local",
        }]
        with self.assertRaises(ValueError):
            validate_event(event)

    def test_rejects_raw_source_fields(self):
        event = copy.deepcopy(self.event())
        event["source"]["cwd"] = "/Users/example/private-project"
        with self.assertRaises(ValueError):
            validate_event(event)

    def test_rejects_credentials_disguised_as_identifiers(self):
        token = "ghp_" + "A" * 36
        events = []
        for field in ("id", "project_id"):
            event = self.event()
            event[field] = token
            events.append(event)
        event = self.event()
        event["source"]["thread_id"] = token
        events.append(event)
        event = self.event()
        event["evidence"] = [{
            "id": token, "device_id": "test-device", "kind": "local", "label": "结果摘要",
        }]
        events.append(event)
        for index, event in enumerate(events):
            with self.subTest(location=index):
                with self.assertRaises(ValueError):
                    validate_event(event)

    def test_missing_evidence_does_not_claim_verified(self):
        event = self.event(evidence_level="remote", evidence=[])
        checked = validate_event(event)
        # Validators may return the checked event or validate in place.
        self.assertEqual((checked or event)["evidence_level"], "unverified")

    def test_project_policy_cannot_be_bypassed_by_caller(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.config["projects"] = [
                {"id": "personal", "name": "Personal", "paths": [], "policy": "local_only"},
                {"id": "reviewed", "name": "Review", "paths": [], "policy": "review"},
            ]
            store.save_config()
            store.add_event(self.event(), eligible=True)
            store.add_event(make_event("worklog", "reviewed", "待审核摘要"), eligible=True)
            self.assertEqual(store.pending(), [])
            self.assertEqual(len(store.events()), 2)
            store.db.close()


if __name__ == "__main__":
    unittest.main()
