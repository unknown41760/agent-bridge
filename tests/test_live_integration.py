from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_bridge.codex_adapter import CodexAdapter
from agent_bridge.opencode_adapter import OpenCodeAdapter
from agent_bridge.protocol import ConsultationRequest, TaskEnvelope


class LiveIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("AGENT_BRIDGE_LIVE_OPENCODE") == "1", "live OpenCode integration is opt-in")
    def test_live_opencode(self):
        model = os.environ.get("AGENT_BRIDGE_LIVE_OPENCODE_MODEL")
        if not model:
            self.fail("set AGENT_BRIDGE_LIVE_OPENCODE_MODEL=provider/model when enabling the live test")
        executable = shutil.which("opencode")
        if not executable:
            self.fail("opencode is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            task = TaskEnvelope.from_mapping({
                "version": 1,
                "id": "live-opencode",
                "objective": "Create bridge_live_test.txt containing exactly: ok",
                "acceptance_criteria": ["bridge_live_test.txt contains exactly ok"],
                "allowed_paths": ["bridge_live_test.txt"],
                "worker": "live",
                "caller": {"harness": "codex"},
            })
            adapter = OpenCodeAdapter(executable=executable)
            adapter.ensure_model_available(model)
            result = adapter.run_task(task, model=model, workspace=root, timeout=300)
            self.assertTrue(result.session_id)
            self.assertEqual((root / "bridge_live_test.txt").read_text(encoding="utf-8").strip(), "ok")

    @unittest.skipUnless(os.environ.get("AGENT_BRIDGE_LIVE_CODEX") == "1", "live Codex integration is opt-in")
    def test_live_codex_consultation(self):
        executable = shutil.which("codex")
        if not executable:
            self.fail("codex is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            request = ConsultationRequest(
                version=1,
                id="live-codex",
                question="Return a brief confirmation that 2 + 2 equals 4.",
                mode="review",
                caller={"harness": "opencode"},
            )
            result = CodexAdapter(executable=executable).consult(request, workspace=root, timeout=300)
            self.assertEqual(result.status, "completed")
            self.assertTrue(result.answer)
