from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agent_bridge.codex_adapter import CodexAdapter
from agent_bridge.opencode_adapter import OpenCodeAdapter, OpenCodeError, build_worker_prompt
from agent_bridge.protocol import ConsultationRequest, TaskEnvelope


class FakeRunner:
    def __init__(self, responses: list[subprocess.CompletedProcess[str]]):
        self.responses = list(responses)
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        return self.responses.pop(0)


def task(caller: str = "codex") -> TaskEnvelope:
    return TaskEnvelope.from_mapping({
        "version": 1,
        "id": "t1",
        "objective": "Implement the bounded thing",
        "acceptance_criteria": ["Done"],
        "worker": "fast",
        "caller": {"harness": caller},
    })


class AdapterTests(unittest.TestCase):
    def test_opencode_receives_expected_cwd_and_model(self):
        stream = "\n".join([
            json.dumps({"type": "step_start", "sessionID": "ses_1", "part": {}}),
            json.dumps({"type": "text", "sessionID": "ses_1", "part": {"text": json.dumps({"status": "completed", "summary": "ok", "unresolved": []})}}),
        ])
        runner = FakeRunner([subprocess.CompletedProcess([], 0, stream, "")])
        adapter = OpenCodeAdapter(executable="/bin/opencode", runner=runner)
        with tempfile.TemporaryDirectory() as tmp:
            result = adapter.run_task(task(), model="provider/model", workspace=tmp)
            argv, kwargs = runner.calls[0]
            self.assertEqual(argv[:2], ["/bin/opencode", "run"])
            self.assertNotIn("--dir", argv)
            self.assertEqual(argv[argv.index("--model") + 1], "provider/model")
            self.assertEqual(Path(kwargs["cwd"]), Path(tmp))
            self.assertEqual(result.session_id, "ses_1")

    def test_json_events_parsed_without_transcript_leak(self):
        giant_tool_output = "SECRET_TRANSCRIPT" * 100
        stream = "\n".join([
            json.dumps({"type": "tool_use", "sessionID": "ses_1", "part": {"state": {"output": giant_tool_output}}}),
            json.dumps({"type": "text", "sessionID": "ses_1", "part": {"text": json.dumps({"status": "completed", "summary": "compact", "unresolved": []})}}),
        ])
        runner = FakeRunner([subprocess.CompletedProcess([], 0, stream, "")])
        result = OpenCodeAdapter(executable="opencode", runner=runner).run_task(task(), model="p/m", workspace=".")
        self.assertEqual(result.summary, "compact")
        self.assertNotIn("SECRET_TRANSCRIPT", repr(result))

    def test_session_id_is_captured(self):
        stream = "\n".join([
            json.dumps({"type": "step_start", "sessionID": "ses_capture", "part": {}}),
            json.dumps({"type": "text", "sessionID": "ses_capture", "part": {"text": "done"}}),
        ])
        result = OpenCodeAdapter(executable="opencode", runner=FakeRunner([subprocess.CompletedProcess([], 0, stream, "")])).run_task(task(), model="p/m", workspace=".")
        self.assertEqual(result.session_id, "ses_capture")

    def test_needs_coordinator_is_preserved(self):
        payload = {"status": "needs_coordinator", "summary": "spec conflicts", "unresolved": [{"path": "design.md"}]}
        stream = json.dumps({"type": "text", "sessionID": "ses_2", "part": {"text": json.dumps(payload)}})
        result = OpenCodeAdapter(executable="opencode", runner=FakeRunner([subprocess.CompletedProcess([], 0, stream, "")])).run_task(task(), model="p/m", workspace=".")
        self.assertEqual(result.status, "needs_coordinator")
        self.assertEqual(result.unresolved, payload["unresolved"])

    def test_model_presence_check_fails_without_substitution(self):
        runner = FakeRunner([subprocess.CompletedProcess([], 0, "provider/other\n", "")])
        adapter = OpenCodeAdapter(executable="opencode", runner=runner)
        with self.assertRaises(OpenCodeError) as ctx:
            adapter.ensure_model_available("provider/intended")
        self.assertEqual(ctx.exception.category, "model_unavailable")
        self.assertEqual(runner.calls[0][0], ["opencode", "models"])

    def test_openspec_source_appears_in_worker_contract(self):
        t = TaskEnvelope.from_mapping({
            "version": 1,
            "id": "os",
            "objective": "Apply task",
            "acceptance_criteria": ["done"],
            "worker": "fast",
            "source": {"kind": "openspec", "change": "add-retries", "task_ids": ["2.1"]},
        })
        prompt = build_worker_prompt(t)
        self.assertIn('"kind": "openspec"', prompt)
        self.assertIn('"task_ids"', prompt)

    def test_codex_consultation_parses_structured_output_and_sets_guards(self):
        final = json.dumps({"status": "completed", "answer": "race is guarded", "findings": []})
        stream = "\n".join([
            json.dumps({"type": "thread.started", "thread_id": "thr_1"}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": final}}),
            json.dumps({"type": "turn.completed", "usage": {}}),
        ])
        runner = FakeRunner([subprocess.CompletedProcess([], 0, stream, "")])
        request = ConsultationRequest(version=1, id="c1", question="Is this safe?", caller={"harness": "opencode"})
        with tempfile.TemporaryDirectory() as tmp:
            result = CodexAdapter(executable="codex", runner=runner).consult(request, workspace=tmp)
        self.assertEqual(result.answer, "race is guarded")
        argv, kwargs = runner.calls[0]
        self.assertIn("--sandbox", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertEqual(kwargs["env"]["AGENT_BRIDGE_NO_OPENCODE_REENTRY"], "1")
        self.assertEqual(kwargs["env"]["AGENT_BRIDGE_NO_CODEX_REENTRY"], "1")

    def test_resume_uses_requested_session_and_same_model(self):
        stream = json.dumps({"type": "text", "sessionID": "ses_same", "part": {"text": "fixed"}})
        runner = FakeRunner([subprocess.CompletedProcess([], 0, stream, "")])
        adapter = OpenCodeAdapter(executable="opencode", runner=runner)
        adapter.run_task(task(), model="provider/original", workspace=".", session_id="ses_same")
        argv = runner.calls[0][0]
        self.assertEqual(argv[argv.index("--session") + 1], "ses_same")
        self.assertEqual(argv[argv.index("--model") + 1], "provider/original")
