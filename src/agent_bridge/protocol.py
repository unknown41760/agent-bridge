from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json


class ProtocolError(ValueError):
    pass


def _expect_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{name} must be an object")
    return value


def _expect_str(value: Any, name: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str):
        raise ProtocolError(f"{name} must be a string")
    if nonempty and not value.strip():
        raise ProtocolError(f"{name} must not be empty")
    return value


def _expect_str_list(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ProtocolError(f"{name} must be an array of strings")
    return list(value)


def _expect_version(value: Any) -> int:
    if value != 1:
        raise ProtocolError("version must be 1")
    return 1


@dataclass(slots=True)
class TaskEnvelope:
    version: int
    id: str
    objective: str
    acceptance_criteria: list[str]
    worker: str
    context: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    validation_commands: list[str] = field(default_factory=list)
    risk: str = "medium"
    source: dict[str, Any] | None = None
    caller: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "TaskEnvelope":
        _expect_object(data, "task")
        version = _expect_version(data.get("version"))
        task_id = _expect_str(data.get("id"), "id")
        objective = _expect_str(data.get("objective"), "objective")
        criteria = _expect_str_list(data.get("acceptance_criteria"), "acceptance_criteria")
        if not criteria:
            raise ProtocolError("acceptance_criteria must contain at least one item")
        worker = _expect_str(data.get("worker"), "worker")
        risk = _expect_str(data.get("risk", "medium"), "risk")
        if risk not in {"low", "medium", "high"}:
            raise ProtocolError("risk must be low, medium, or high")
        source = data.get("source")
        if source is not None:
            source = _expect_object(source, "source")
        caller = _expect_object(data.get("caller", {}), "caller")
        return cls(
            version=version,
            id=task_id,
            objective=objective,
            acceptance_criteria=criteria,
            worker=worker,
            context=_expect_str_list(data.get("context"), "context"),
            references=_expect_str_list(data.get("references"), "references"),
            allowed_paths=_expect_str_list(data.get("allowed_paths"), "allowed_paths"),
            forbidden_paths=_expect_str_list(data.get("forbidden_paths"), "forbidden_paths"),
            validation_commands=_expect_str_list(data.get("validation_commands"), "validation_commands"),
            risk=risk,
            source=source,
            caller=caller,
        )

    @classmethod
    def load(cls, path: str | Path) -> "TaskEnvelope":
        return cls.from_mapping(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_mapping(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "id": self.id,
            "objective": self.objective,
            "context": self.context,
            "references": self.references,
            "acceptance_criteria": self.acceptance_criteria,
            "allowed_paths": self.allowed_paths,
            "forbidden_paths": self.forbidden_paths,
            "validation_commands": self.validation_commands,
            "risk": self.risk,
            "worker": self.worker,
            "source": self.source,
            "caller": self.caller,
        }


@dataclass(slots=True)
class CorrectionRequest:
    findings: list[dict[str, Any]]

    @classmethod
    def load(cls, path: str | Path) -> "CorrectionRequest":
        data = _expect_object(json.loads(Path(path).read_text(encoding="utf-8")), "correction")
        findings = data.get("findings")
        if not isinstance(findings, list) or not findings:
            raise ProtocolError("findings must be a non-empty array")
        normalized: list[dict[str, Any]] = []
        for index, finding in enumerate(findings):
            item = _expect_object(finding, f"findings[{index}]")
            message = _expect_str(item.get("message"), f"findings[{index}].message")
            severity = _expect_str(item.get("severity", "required"), f"findings[{index}].severity")
            paths = _expect_str_list(item.get("paths"), f"findings[{index}].paths")
            normalized.append({"severity": severity, "message": message, "paths": paths})
        return cls(findings=normalized)


@dataclass(slots=True)
class ConsultationRequest:
    version: int
    id: str
    question: str
    context: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    mode: str = "review"
    caller: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "ConsultationRequest":
        data = _expect_object(json.loads(Path(path).read_text(encoding="utf-8")), "consultation request")
        return cls(
            version=_expect_version(data.get("version")),
            id=_expect_str(data.get("id"), "id"),
            question=_expect_str(data.get("question"), "question"),
            context=_expect_str_list(data.get("context"), "context"),
            references=_expect_str_list(data.get("references"), "references"),
            mode=_expect_str(data.get("mode", "review"), "mode"),
            caller=_expect_object(data.get("caller", {}), "caller"),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "id": self.id,
            "question": self.question,
            "context": self.context,
            "references": self.references,
            "mode": self.mode,
            "caller": self.caller,
        }


@dataclass(slots=True)
class CheckResult:
    command: str
    status: str
    summary: str
    exit_code: int

    def to_mapping(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "status": self.status,
            "summary": self.summary,
            "exit_code": self.exit_code,
        }


@dataclass(slots=True)
class ResultEnvelope:
    task_id: str
    status: str
    execution: dict[str, Any]
    summary: str
    files_changed: list[str]
    checks: list[CheckResult]
    unresolved: list[Any]
    failure: dict[str, Any] | None
    version: int = 1

    def to_mapping(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "task_id": self.task_id,
            "status": self.status,
            "execution": self.execution,
            "summary": self.summary,
            "files_changed": self.files_changed,
            "checks": [item.to_mapping() for item in self.checks],
            "unresolved": self.unresolved,
            "failure": self.failure,
        }
