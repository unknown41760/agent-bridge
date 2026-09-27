from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_bridge.cli import _run_worker, build_parser, cmd_consult, cmd_delegate, cmd_resume, cmd_status
from agent_bridge.history import HistoryRecord, HistoryStore
from agent_bridge.opencode_adapter import OpenCodeResult
from agent_bridge.protocol import CheckResult, TaskEnvelope
from agent_bridge.workspace import GitWorkspace


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class StubOpenCode:
    def __init__(self):
        self.executable = "opencode"
        self.models_checked: list[str] = []
        self.run_args = None

    def version(self):
        return "opencode 1.test"

    def list_models(self):
        return {"provider/original"}

    def ensure_model_available(self, model: str):
        self.models_checked.append(model)

    def run_task(self, task, *, model, workspace, session_id=None, correction=None, timeout=None):
        self.run_args = {"task": task, "model": model, "workspace": str(workspace), "session_id": session_id, "correction": correction}
        return OpenCodeResult(status="completed", session_id=session_id or "ses_new", duration_ms=5, summary="fixed", unresolved=[])


class StubCodex:
    def __init__(self):
        self.executable = "codex"

    def version(self):
        return "codex 1.test"


class CliTests(unittest.TestCase):
    def test_worker_child_cannot_delegate_or_resume_recursively(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / "task.json"
            task.write_text(json.dumps({"version": 1, "id": "t1", "objective": "fix", "acceptance_criteria": ["fixed"], "worker": "fast"}), encoding="utf-8")
            correction = root / "correction.json"
            correction.write_text(json.dumps({"findings": [{"message": "fix it"}]}), encoding="utf-8")
            commands = [
                ["delegate", str(task), "--json"],
                ["resume", str(task), "--session", "ses_same", "--correction", str(correction), "--json"],
            ]
            for argv in commands:
                args = build_parser().parse_args(argv)
                with mock.patch.dict(os.environ, {"AGENT_BRIDGE_NO_CODEX_REENTRY": "1"}, clear=False), contextlib.redirect_stdout(io.StringIO()) as out:
                    code = args.func(args)
                self.assertEqual(code, 1)
                self.assertEqual(json.loads(out.getvalue())["failure"]["category"], "recursion_blocked")

    def test_delegate_sets_codex_origin_even_without_caller_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q")
            (root / "agent-bridge.toml").write_text('[workers.fast]\nmodel = "provider/original"\n', encoding="utf-8")
            task = root / "task.json"
            task.write_text(json.dumps({"version": 1, "id": "t1", "objective": "fix", "acceptance_criteria": ["fixed"], "worker": "fast"}), encoding="utf-8")
            stub = StubOpenCode()
            args = build_parser().parse_args(["delegate", str(task), "--json"])
            old = Path.cwd()
            os.chdir(root)
            try:
                with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(root / "state")}), mock.patch("agent_bridge.cli.OpenCodeAdapter", return_value=stub), contextlib.redirect_stdout(io.StringIO()):
                    code = cmd_delegate(args)
            finally:
                os.chdir(old)
            self.assertEqual(code, 0)
            self.assertEqual(stub.run_args["task"].caller["harness"], "codex")

    def test_validation_changes_to_ignored_forbidden_path_are_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q")
            (root / ".gitignore").write_text("*.key\n", encoding="utf-8")
            git(root, "add", ".gitignore")
            task = TaskEnvelope.from_mapping({
                "version": 1, "id": "t1", "objective": "fix", "acceptance_criteria": ["fixed"],
                "worker": "fast", "forbidden_paths": ["**/*.key"], "validation_commands": ["check"],
            })

            def validate(commands, cwd):
                (root / "secret.key").write_text("created by check", encoding="utf-8")
                return [CheckResult(command="check", status="passed", summary="ok", exit_code=0)]

            with mock.patch("agent_bridge.cli.run_validation_commands", side_effect=validate):
                result = _run_worker(task, worker_alias="fast", model="provider/original", workspace=GitWorkspace(root), adapter=StubOpenCode())
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.failure["category"], "protected_path_violation")
            self.assertEqual(result.files_changed, ["secret.key"])

    def test_codex_originated_worker_cannot_consult_recursively(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = Path(tmp) / "request.json"
            req.write_text(json.dumps({"version": 1, "id": "c1", "question": "q", "caller": {"harness": "opencode"}}), encoding="utf-8")
            args = build_parser().parse_args(["consult", str(req), "--json"])
            with mock.patch.dict(os.environ, {"AGENT_BRIDGE_NO_CODEX_REENTRY": "1"}, clear=False):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = cmd_consult(args)
            self.assertEqual(code, 1)
            self.assertIn("recursion_blocked", out.getvalue())

    def test_same_worker_correction_uses_history_model_and_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q")
            task = root / "task.json"
            task.write_text(json.dumps({
                "version": 1,
                "id": "t1",
                "objective": "fix",
                "acceptance_criteria": ["fixed"],
                "worker": "fast",
            }), encoding="utf-8")
            correction = root / "correction.json"
            correction.write_text(json.dumps({"findings": [{"severity": "required", "message": "fix it", "paths": []}]}), encoding="utf-8")
            state = root / "state"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(state)}, clear=False):
                store = HistoryStore()
                store.append(HistoryRecord(
                    task_id="t1", direction="codex_to_opencode", worker_alias="fast", model="provider/original",
                    session_id="ses_same", duration_ms=1, process_outcome="completed", validation_result="passed",
                    correction_count=0, failure_category=None, workspace=str(root),
                ))
                stub = StubOpenCode()
                args = build_parser().parse_args(["resume", str(task), "--session", "ses_same", "--correction", str(correction), "--json"])
                old = Path.cwd()
                os.chdir(root)
                try:
                    out = io.StringIO()
                    with mock.patch("agent_bridge.cli.OpenCodeAdapter", return_value=stub), contextlib.redirect_stdout(out):
                        code = cmd_resume(args)
                finally:
                    os.chdir(old)
            self.assertEqual(code, 0)
            self.assertEqual(stub.models_checked, ["provider/original"])
            self.assertEqual(stub.run_args["model"], "provider/original")
            self.assertEqual(stub.run_args["session_id"], "ses_same")
            self.assertEqual(stub.run_args["task"].caller["harness"], "codex")

    def test_resume_rejects_another_task_or_worker_for_recorded_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q")
            task = root / "task.json"
            correction = root / "correction.json"
            correction.write_text(json.dumps({"findings": [{"message": "fix it"}]}), encoding="utf-8")
            state = root / "state"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(state)}):
                HistoryStore().append(HistoryRecord(
                    task_id="original", direction="codex_to_opencode", worker_alias="fast", model="provider/original",
                    session_id="ses_same", duration_ms=1, process_outcome="completed", validation_result="passed",
                    correction_count=0, failure_category=None, workspace=str(root),
                ))
                old = Path.cwd()
                os.chdir(root)
                try:
                    for task_id, worker in [("other", "fast"), ("original", "other")]:
                        task.write_text(json.dumps({"version": 1, "id": task_id, "objective": "fix", "acceptance_criteria": ["fixed"], "worker": worker}), encoding="utf-8")
                        args = build_parser().parse_args(["resume", str(task), "--session", "ses_same", "--correction", str(correction), "--json"])
                        with mock.patch("agent_bridge.cli.OpenCodeAdapter") as adapter, contextlib.redirect_stdout(io.StringIO()) as out:
                            code = cmd_resume(args)
                        self.assertEqual(code, 1)
                        self.assertEqual(json.loads(out.getvalue())["failure"]["category"], "session_unavailable")
                        adapter.assert_not_called()
                finally:
                    os.chdir(old)

    def test_status_makes_no_paid_inference_request(self):
        opencode = StubOpenCode()
        codex = StubCodex()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "agent-bridge.toml").write_text('[workers.fast]\nmodel = "provider/original"\n', encoding="utf-8")
            args = build_parser().parse_args(["status", "--json"])
            old = Path.cwd()
            os.chdir(root)
            try:
                out = io.StringIO()
                with mock.patch("agent_bridge.cli.OpenCodeAdapter", return_value=opencode), mock.patch("agent_bridge.cli.CodexAdapter", return_value=codex), contextlib.redirect_stdout(out):
                    code = cmd_status(args)
            finally:
                os.chdir(old)
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["workers"]["fast"]["listed_by_opencode"])
        self.assertFalse(hasattr(opencode, "run_task_called"))

    def test_cli_surface_contains_required_commands(self):
        parser = build_parser()
        for argv in (["delegate", "task.json"], ["resume", "task.json", "--session", "s", "--correction", "c.json"], ["consult", "r.json"], ["status"]):
            parsed = parser.parse_args(argv)
            self.assertTrue(hasattr(parsed, "func"))
