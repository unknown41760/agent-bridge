from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_bridge.config import ConfigError, load_config
from agent_bridge.protocol import ProtocolError, TaskEnvelope


BASE_TASK = {
    "version": 1,
    "id": "t1",
    "objective": "Do the bounded work",
    "acceptance_criteria": ["It works"],
    "worker": "fast",
}


class ProtocolConfigTests(unittest.TestCase):
    def test_task_envelope_validation(self):
        task = TaskEnvelope.from_mapping(BASE_TASK)
        self.assertEqual(task.id, "t1")
        with self.assertRaises(ProtocolError):
            TaskEnvelope.from_mapping({**BASE_TASK, "version": 2})
        with self.assertRaises(ProtocolError):
            TaskEnvelope.from_mapping({**BASE_TASK, "acceptance_criteria": []})

    def test_worker_alias_resolves_exact_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "agent-bridge.toml").write_text(
                '[workers.fast]\nmodel = "synthetic/provider-model-id"\n[defaults]\nworker = "fast"\n',
                encoding="utf-8",
            )
            cfg = load_config(root)
            worker = cfg.resolve_worker("fast")
            self.assertEqual(worker.model, "synthetic/provider-model-id")

    def test_unknown_alias_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "agent-bridge.toml").write_text(
                '[workers.fast]\nmodel = "provider/model"\n', encoding="utf-8"
            )
            cfg = load_config(root)
            with self.assertRaises(ConfigError):
                cfg.resolve_worker("missing")

    def test_openspec_metadata_passes_through(self):
        source = {"kind": "openspec", "change": "retry-downloads", "task_ids": ["2.1", "2.2"]}
        task = TaskEnvelope.from_mapping({**BASE_TASK, "source": source})
        self.assertEqual(task.to_mapping()["source"], source)

    def test_missing_intended_worker_never_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "agent-bridge.toml").write_text(
                '[workers.fast]\nmodel = "provider/fast"\n[defaults]\nworker = "fast"\n', encoding="utf-8"
            )
            cfg = load_config(root)
            with self.assertRaises(ConfigError):
                cfg.resolve_worker("explicit-missing")
