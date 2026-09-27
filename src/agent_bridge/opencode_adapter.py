from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import json
import os
import shutil
import subprocess
import time

from .protocol import CorrectionRequest, TaskEnvelope


Runner = Callable[..., subprocess.CompletedProcess[str]]


class OpenCodeError(RuntimeError):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category
        self.message = message


@dataclass(slots=True)
class OpenCodeResult:
    status: str
    session_id: str
    duration_ms: int
    summary: str
    unresolved: list[Any]


def _default_runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, **kwargs)


def _tail(text: str, limit: int = 2000) -> str:
    clean = text.strip()
    if len(clean) > limit:
        clean = clean[-limit:]
    return clean


def _parse_worker_payload(text: str) -> tuple[str, str, list[Any]]:
    stripped = text.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return "completed", stripped, []
    if not isinstance(payload, dict):
        return "completed", stripped, []
    status = payload.get("status", "completed")
    if status not in {"completed", "needs_coordinator", "blocked", "failed"}:
        status = "completed"
    summary = payload.get("summary", "")
    if not isinstance(summary, str):
        summary = str(summary)
    unresolved = payload.get("unresolved", [])
    if not isinstance(unresolved, list):
        unresolved = [unresolved]
    return status, summary, unresolved


def build_worker_prompt(task: TaskEnvelope, correction: CorrectionRequest | None = None) -> str:
    contract = {
        "task": task.to_mapping(),
        "correction": None if correction is None else {"findings": correction.findings},
    }
    origin = str(task.caller.get("harness", ""))
    recursion = (
        "This task originated from Codex. Do not call Codex or agent-bridge consult; return needs_coordinator when premium judgment is required."
        if origin == "codex"
        else "Do not delegate recursively."
    )
    return f"""You own this bounded implementation until it is ready for coordinator review.

Read only the repository context needed to satisfy the brief. Perform necessary in-scope discovery, implement the requested behavior, run appropriate tests, and debug ordinary failures. Respect allowed and forbidden paths. Do not broaden requirements or redesign stable architecture; if the contract conflicts with repository evidence or an authoritative referenced spec, stop and return needs_coordinator with the conflict. {recursion}

The coordinator will independently inspect repository changes and run explicit validation commands, so do not claim mechanical facts you cannot establish.

Execution contract (JSON):
{json.dumps(contract, indent=2, sort_keys=True)}

Your final response must be one compact JSON object and nothing else:
{{"status":"completed|needs_coordinator|blocked|failed","summary":"brief outcome","unresolved":[]}}
"""


class OpenCodeAdapter:
    def __init__(self, executable: str | None = None, runner: Runner = _default_runner):
        self.executable = executable or shutil.which("opencode")
        self.runner = runner

    def _require_executable(self) -> str:
        if not self.executable:
            raise OpenCodeError("opencode_unavailable", "OpenCode CLI was not found on PATH")
        return self.executable

    def version(self) -> str | None:
        if not self.executable:
            return None
        proc = self.runner([self.executable, "--version"], text=True, capture_output=True, check=False)
        return (proc.stdout or proc.stderr).strip() or None

    def list_models(self) -> set[str]:
        exe = self._require_executable()
        proc = self.runner([exe, "models"], text=True, capture_output=True, check=False)
        if proc.returncode != 0:
            raise OpenCodeError("provider_failure", _tail(proc.stderr) or "opencode models failed")
        models: set[str] = set()
        for line in proc.stdout.splitlines():
            value = line.strip().split()[0] if line.strip() else ""
            if "/" in value:
                models.add(value)
        return models

    def ensure_model_available(self, model: str) -> None:
        if model not in self.list_models():
            raise OpenCodeError("model_unavailable", f"configured model {model!r} is not listed by OpenCode")

    def run_task(
        self,
        task: TaskEnvelope,
        *,
        model: str,
        workspace: str | Path,
        session_id: str | None = None,
        correction: CorrectionRequest | None = None,
        timeout: int | None = None,
    ) -> OpenCodeResult:
        exe = self._require_executable()
        argv = [
            exe,
            "run",
            "--model",
            model,
            "--format",
            "json",
        ]
        if session_id:
            argv.extend(["--session", session_id])
        argv.append(build_worker_prompt(task, correction))
        env = os.environ.copy()
        if task.caller.get("harness") == "codex":
            env["AGENT_BRIDGE_CALLER"] = "codex"
            env["AGENT_BRIDGE_NO_CODEX_REENTRY"] = "1"
        started = time.monotonic()
        try:
            proc = self.runner(
                argv,
                cwd=Path(workspace),
                env=env,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise OpenCodeError("timeout", f"OpenCode timed out: {_tail(str(exc))}") from exc
        duration_ms = int((time.monotonic() - started) * 1000)
        if proc.returncode != 0:
            message = _tail(proc.stderr) or _tail(proc.stdout) or f"OpenCode exited with {proc.returncode}"
            raise OpenCodeError("provider_failure", message)

        seen_session: str | None = None
        final_text: str | None = None
        stream_error: str | None = None
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise OpenCodeError("invalid_event_stream", "OpenCode emitted non-JSON output in JSON mode") from exc
            if not isinstance(event, dict):
                raise OpenCodeError("invalid_event_stream", "OpenCode JSON event was not an object")
            event_session = event.get("sessionID")
            if isinstance(event_session, str):
                if seen_session and event_session != seen_session:
                    raise OpenCodeError("invalid_event_stream", "OpenCode emitted multiple top-level session IDs")
                seen_session = event_session
            if event.get("type") == "text":
                part = event.get("part")
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    final_text = part["text"]
            elif event.get("type") == "error":
                stream_error = json.dumps(event.get("error"), ensure_ascii=False)[:1500]

        if stream_error:
            raise OpenCodeError("provider_failure", stream_error)
        if not seen_session:
            raise OpenCodeError("invalid_event_stream", "OpenCode JSON stream did not include a session ID")
        if session_id and seen_session != session_id:
            raise OpenCodeError("session_unavailable", "OpenCode resumed a different session than requested")
        if final_text is None:
            raise OpenCodeError("invalid_event_stream", "OpenCode JSON stream did not include a final text event")
        status, summary, unresolved = _parse_worker_payload(final_text)
        return OpenCodeResult(
            status=status,
            session_id=seen_session,
            duration_ms=duration_ms,
            summary=summary,
            unresolved=unresolved,
        )
