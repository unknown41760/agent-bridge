from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import json
import os
import shutil
import subprocess
import tempfile
import time

from .protocol import ConsultationRequest


Runner = Callable[..., subprocess.CompletedProcess[str]]


class CodexError(RuntimeError):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category
        self.message = message


@dataclass(slots=True)
class ConsultationResult:
    request_id: str
    status: str
    answer: str
    findings: list[Any]
    session_id: str | None
    duration_ms: int

    def to_mapping(self) -> dict[str, Any]:
        return {
            "version": 1,
            "request_id": self.request_id,
            "status": self.status,
            "answer": self.answer,
            "findings": self.findings,
            "execution": {"session_id": self.session_id, "duration_ms": self.duration_ms},
        }


def _default_runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, **kwargs)


def _tail(text: str, limit: int = 2000) -> str:
    text = text.strip()
    return text[-limit:] if len(text) > limit else text


def consultation_prompt(request: ConsultationRequest) -> str:
    return f"""This is a bounded consultation. Do not delegate this request through agent-bridge. Answer it directly. Normal consultation is read-only; do not modify files.

Request (JSON):
{json.dumps(request.to_mapping(), indent=2, sort_keys=True)}

Use referenced repository files only as needed. Focus on the question and return the requested structured result."""


class CodexAdapter:
    def __init__(self, executable: str | None = None, runner: Runner = _default_runner, schema_path: Path | None = None):
        self.executable = executable or shutil.which("codex")
        self.runner = runner
        self.schema_path = schema_path

    def _require_executable(self) -> str:
        if not self.executable:
            raise CodexError("codex_unavailable", "Codex CLI was not found on PATH")
        return self.executable

    def version(self) -> str | None:
        if not self.executable:
            return None
        proc = self.runner([self.executable, "--version"], text=True, capture_output=True, check=False)
        return (proc.stdout or proc.stderr).strip() or None

    def consult(self, request: ConsultationRequest, *, workspace: str | Path, timeout: int | None = None) -> ConsultationResult:
        exe = self._require_executable()
        schema = self.schema_path
        temporary: tempfile.TemporaryDirectory[str] | None = None
        if schema is None:
            temporary = tempfile.TemporaryDirectory(prefix="agent-bridge-codex-")
            schema = Path(temporary.name) / "consultation-result.schema.json"
            schema.write_text(json.dumps({
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["completed", "needs_coordinator", "blocked", "failed"]},
                    "answer": {"type": "string"},
                    "findings": {"type": "array"},
                },
                "required": ["status", "answer", "findings"],
                "additionalProperties": False,
            }), encoding="utf-8")
        argv = [
            exe,
            "exec",
            "--json",
            "--output-schema",
            str(schema),
            "--cd",
            str(Path(workspace).resolve()),
            "--sandbox",
            "read-only",
            "--config",
            'approval_policy="never"',
            consultation_prompt(request),
        ]
        env = os.environ.copy()
        env["AGENT_BRIDGE_CALLER"] = "opencode"
        env["AGENT_BRIDGE_NO_OPENCODE_REENTRY"] = "1"
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
            raise CodexError("timeout", f"Codex timed out: {_tail(str(exc))}") from exc
        finally:
            if temporary is not None:
                temporary.cleanup()
        duration_ms = int((time.monotonic() - started) * 1000)
        if proc.returncode != 0:
            raise CodexError("provider_failure", _tail(proc.stderr) or _tail(proc.stdout) or f"Codex exited with {proc.returncode}")

        session_id: str | None = None
        final_text: str | None = None
        stream_error: str | None = None
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CodexError("invalid_event_stream", "Codex emitted non-JSON output in JSON mode") from exc
            if not isinstance(event, dict):
                raise CodexError("invalid_event_stream", "Codex JSON event was not an object")
            if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
                session_id = event["thread_id"]
            elif event.get("type") == "item.completed":
                item = event.get("item")
                if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                    final_text = item["text"]
            elif event.get("type") in {"turn.failed", "error"}:
                stream_error = json.dumps(event, ensure_ascii=False)[:1500]
        if stream_error:
            raise CodexError("provider_failure", stream_error)
        if final_text is None:
            raise CodexError("invalid_event_stream", "Codex JSON stream did not include a final agent message")
        try:
            payload = json.loads(final_text)
        except json.JSONDecodeError as exc:
            raise CodexError("invalid_event_stream", "Codex structured final message was not JSON") from exc
        if not isinstance(payload, dict):
            raise CodexError("invalid_event_stream", "Codex structured final message was not an object")
        status = payload.get("status")
        answer = payload.get("answer")
        findings = payload.get("findings")
        if status not in {"completed", "needs_coordinator", "blocked", "failed"} or not isinstance(answer, str) or not isinstance(findings, list):
            raise CodexError("invalid_event_stream", "Codex structured result did not match the consultation schema")
        return ConsultationResult(
            request_id=request.id,
            status=status,
            answer=answer,
            findings=findings,
            session_id=session_id,
            duration_ms=duration_ms,
        )
