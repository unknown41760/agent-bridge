from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from typing import Any

from .codex_adapter import CodexAdapter, CodexError
from .config import ConfigError, load_config
from .history import HistoryRecord, HistoryStore
from .opencode_adapter import OpenCodeAdapter, OpenCodeError
from .protocol import ConsultationRequest, CorrectionRequest, ProtocolError, ResultEnvelope, TaskEnvelope
from .validation import run_validation_commands
from .workspace import GitWorkspace, WorkspaceError


def _failure(task_id: str, category: str, message: str, *, worker: str | None = None, model: str | None = None, session_id: str | None = None, files_changed: list[str] | None = None) -> ResultEnvelope:
    return ResultEnvelope(
        task_id=task_id,
        status="failed",
        execution={"worker": worker, "model": model, "session_id": session_id, "duration_ms": 0},
        summary="",
        files_changed=files_changed or [],
        checks=[],
        unresolved=[],
        failure={"category": category, "message": message},
    )


def _emit_result(result: ResultEnvelope, as_json: bool) -> None:
    data = result.to_mapping()
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return
    print(f"{result.task_id}: {result.status}")
    execution = result.execution
    if execution.get("worker"):
        print(f"worker: {execution.get('worker')} -> {execution.get('model')}")
    if execution.get("session_id"):
        print(f"session: {execution.get('session_id')}")
    if result.files_changed:
        print("files changed:")
        for path in result.files_changed:
            print(f"  {path}")
    for check in result.checks:
        print(f"check {check.status}: {check.command} — {check.summary}")
    if result.summary:
        print(result.summary)
    if result.failure:
        print(f"failure: {result.failure['category']}: {result.failure['message']}", file=sys.stderr)


def _safe_history_append(store: HistoryStore, record: HistoryRecord) -> None:
    try:
        store.append(record)
    except OSError:
        pass


def _record_for(result: ResultEnvelope, *, direction: str, correction_count: int, workspace: Path) -> HistoryRecord:
    checks = result.checks
    validation = "not_run" if not checks else ("passed" if all(c.status == "passed" for c in checks) else "failed")
    return HistoryRecord(
        task_id=result.task_id,
        direction=direction,
        worker_alias=result.execution.get("worker"),
        model=result.execution.get("model"),
        session_id=result.execution.get("session_id"),
        duration_ms=int(result.execution.get("duration_ms") or 0),
        process_outcome=result.status,
        validation_result=validation,
        correction_count=correction_count,
        failure_category=None if result.failure is None else result.failure.get("category"),
        workspace=str(workspace),
    )


def _run_worker(
    task: TaskEnvelope,
    *,
    worker_alias: str,
    model: str,
    workspace: GitWorkspace,
    adapter: OpenCodeAdapter,
    session_id: str | None = None,
    correction: CorrectionRequest | None = None,
) -> ResultEnvelope:
    scope_patterns = list(dict.fromkeys(task.allowed_paths + task.forbidden_paths))
    with workspace.writer_lock():
        baseline = workspace.snapshot(scope_patterns)
        worker_result = None
        failure: dict[str, str] | None = None
        try:
            adapter.ensure_model_available(model)
            worker_result = adapter.run_task(
                task,
                model=model,
                workspace=workspace.root,
                session_id=session_id,
                correction=correction,
            )
        except OpenCodeError as exc:
            failure = {"category": exc.category, "message": exc.message}
        after = workspace.snapshot(scope_patterns)
        changed = after.changed_since(baseline)
        violations = workspace.scope_violations(changed, task.allowed_paths, task.forbidden_paths)

        checks = []
        if failure is None and not violations:
            checks = run_validation_commands(task.validation_commands, workspace.root)
            if task.validation_commands:
                # Validation commands can write files too; inspect the final workspace state.
                after = workspace.snapshot(scope_patterns)
                changed = after.changed_since(baseline)
                violations = workspace.scope_violations(changed, task.allowed_paths, task.forbidden_paths)

    status = "failed"
    summary = ""
    unresolved: list[Any] = []
    duration_ms = 0
    resulting_session = session_id
    if worker_result is not None:
        status = worker_result.status
        summary = worker_result.summary
        unresolved = worker_result.unresolved
        duration_ms = worker_result.duration_ms
        resulting_session = worker_result.session_id
        if status == "failed" and failure is None:
            failure = {"category": "unknown", "message": "worker reported failure"}

    if violations:
        status = "failed"
        failure = {
            "category": "protected_path_violation",
            "message": "delegation or validation changed forbidden or out-of-scope paths: " + ", ".join(violations),
        }
    elif any(check.status != "passed" for check in checks):
        status = "failed"
        failure = {"category": "validation_failed", "message": "one or more validation commands failed"}
    elif failure is not None:
        status = "failed"

    return ResultEnvelope(
        task_id=task.id,
        status=status,
        execution={
            "worker": worker_alias,
            "model": model,
            "session_id": resulting_session,
            "duration_ms": duration_ms,
        },
        summary=summary,
        files_changed=changed,
        checks=checks,
        unresolved=unresolved,
        failure=failure,
    )


def cmd_delegate(args: argparse.Namespace) -> int:
    task_id = Path(args.task).stem
    try:
        task = TaskEnvelope.load(args.task)
        task_id = task.id
        if os.environ.get("AGENT_BRIDGE_NO_OPENCODE_REENTRY") == "1" or os.environ.get("AGENT_BRIDGE_NO_CODEX_REENTRY") == "1":
            result = _failure(task.id, "recursion_blocked", "OpenCode delegation is blocked in this child process")
            _emit_result(result, args.json)
            return 1
        if args.worker:
            task = replace(task, worker=args.worker)
        task = replace(task, caller={**task.caller, "harness": "codex"})
        config = load_config(Path.cwd())
        worker = config.resolve_worker(task.worker)
        workspace = GitWorkspace(Path.cwd())
        adapter = OpenCodeAdapter()
        result = _run_worker(task, worker_alias=worker.alias, model=worker.model, workspace=workspace, adapter=adapter)
        _safe_history_append(HistoryStore(), _record_for(result, direction="codex_to_opencode", correction_count=0, workspace=workspace.root))
    except (ProtocolError, json.JSONDecodeError) as exc:
        result = _failure(task_id, "unknown", f"invalid task envelope: {exc}")
    except ConfigError as exc:
        category = "worker_not_configured" if str(exc).startswith("worker_not_configured") else "unknown"
        result = _failure(task_id, category, str(exc))
    except WorkspaceError as exc:
        result = _failure(task_id, "workspace_invalid", str(exc))
    _emit_result(result, args.json)
    return 0 if result.status in {"completed", "needs_coordinator", "blocked"} else 1


def cmd_resume(args: argparse.Namespace) -> int:
    task_id = Path(args.task).stem
    try:
        task = TaskEnvelope.load(args.task)
        task_id = task.id
        correction = CorrectionRequest.load(args.correction)
        if os.environ.get("AGENT_BRIDGE_NO_OPENCODE_REENTRY") == "1" or os.environ.get("AGENT_BRIDGE_NO_CODEX_REENTRY") == "1":
            result = _failure(task.id, "recursion_blocked", "OpenCode delegation is blocked in this child process", session_id=args.session)
            _emit_result(result, args.json)
            return 1
        store = HistoryStore()
        prior = store.find_session(args.session)
        if prior is None or prior.get("direction") != "codex_to_opencode":
            result = _failure(task.id, "session_unavailable", "session was not found in agent-bridge execution history", session_id=args.session)
            _emit_result(result, args.json)
            return 1
        if prior.get("task_id") != task.id or prior.get("worker_alias") != task.worker:
            result = _failure(task.id, "session_unavailable", "resume task ID and worker must match the original session", session_id=args.session)
            _emit_result(result, args.json)
            return 1
        workspace = GitWorkspace(Path.cwd())
        recorded_workspace = prior.get("workspace")
        if recorded_workspace and Path(recorded_workspace).resolve() != workspace.root:
            result = _failure(task.id, "session_unavailable", "resume must use the original workspace", session_id=args.session)
            _emit_result(result, args.json)
            return 1
        worker_alias = prior.get("worker_alias")
        model = prior.get("model")
        if not isinstance(worker_alias, str) or not isinstance(model, str):
            result = _failure(task.id, "session_unavailable", "history does not contain the original worker/model", session_id=args.session)
            _emit_result(result, args.json)
            return 1
        task = replace(task, caller={**task.caller, "harness": "codex"})
        adapter = OpenCodeAdapter()
        result = _run_worker(
            task,
            worker_alias=worker_alias,
            model=model,
            workspace=workspace,
            adapter=adapter,
            session_id=args.session,
            correction=correction,
        )
        correction_count = int(prior.get("correction_count") or 0) + 1
        _safe_history_append(store, _record_for(result, direction="codex_to_opencode", correction_count=correction_count, workspace=workspace.root))
    except (ProtocolError, json.JSONDecodeError) as exc:
        result = _failure(task_id, "unknown", f"invalid input: {exc}", session_id=args.session)
    except WorkspaceError as exc:
        result = _failure(task_id, "workspace_invalid", str(exc), session_id=args.session)
    _emit_result(result, args.json)
    return 0 if result.status in {"completed", "needs_coordinator", "blocked"} else 1


def cmd_consult(args: argparse.Namespace) -> int:
    request_id = Path(args.request).stem
    if os.environ.get("AGENT_BRIDGE_NO_CODEX_REENTRY") == "1":
        payload = {
            "version": 1,
            "request_id": request_id,
            "status": "failed",
            "answer": "",
            "findings": [],
            "failure": {"category": "recursion_blocked", "message": "Codex consultation is blocked in this child process"},
        }
        print(json.dumps(payload, indent=2, sort_keys=True) if args.json else "consultation failed: recursion_blocked")
        return 1
    try:
        request = ConsultationRequest.load(args.request)
        request_id = request.id
        adapter = CodexAdapter()
        result = adapter.consult(request, workspace=Path.cwd())
        data = result.to_mapping()
        history = HistoryRecord(
            task_id=request.id,
            direction="opencode_to_codex",
            worker_alias=None,
            model=None,
            session_id=result.session_id,
            duration_ms=result.duration_ms,
            process_outcome=result.status,
            validation_result="not_run",
            correction_count=0,
            failure_category=None,
            workspace=str(Path.cwd().resolve()),
        )
        _safe_history_append(HistoryStore(), history)
        if args.json:
            print(json.dumps(data, indent=2, sort_keys=True))
        else:
            print(f"{request.id}: {result.status}")
            print(result.answer)
        return 0 if result.status in {"completed", "needs_coordinator", "blocked"} else 1
    except (ProtocolError, json.JSONDecodeError) as exc:
        category, message = "unknown", f"invalid consultation request: {exc}"
    except CodexError as exc:
        category, message = exc.category, exc.message
    payload = {
        "version": 1,
        "request_id": request_id,
        "status": "failed",
        "answer": "",
        "findings": [],
        "failure": {"category": category, "message": message},
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"consultation failed: {category}: {message}", file=sys.stderr)
    return 1


def cmd_status(args: argparse.Namespace) -> int:
    config_error = None
    try:
        config = load_config(Path.cwd())
    except ConfigError as exc:
        config = None
        config_error = str(exc)
    opencode = OpenCodeAdapter()
    codex = CodexAdapter()
    opencode_version = opencode.version()
    codex_version = codex.version()
    models: set[str] = set()
    models_error = None
    if opencode.executable:
        try:
            models = opencode.list_models()
        except OpenCodeError as exc:
            models_error = {"category": exc.category, "message": exc.message}
    workers: dict[str, Any] = {}
    if config is not None:
        for alias, worker in sorted(config.workers.items()):
            workers[alias] = {"model": worker.model, "listed_by_opencode": worker.model in models if opencode.executable and models_error is None else None}
    data = {
        "codex": {"found": bool(codex.executable), "version": codex_version},
        "opencode": {"found": bool(opencode.executable), "version": opencode_version, "models_error": models_error},
        "workers": workers,
        "default_worker": None if config is None else config.default_worker,
        "config_path": None if config is None or config.path is None else str(config.path),
        "config_error": config_error,
    }
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(f"Codex CLI: {codex_version or 'not found'}")
        print(f"OpenCode CLI: {opencode_version or 'not found'}")
        if data["config_path"]:
            print(f"config: {data['config_path']}")
        else:
            print("config: not found")
        for alias, item in workers.items():
            state = "listed" if item["listed_by_opencode"] is True else "not listed" if item["listed_by_opencode"] is False else "unknown"
            print(f"{alias}: {item['model']} ({state})")
        if config_error:
            print(f"config error: {config_error}", file=sys.stderr)
    return 1 if config_error else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-bridge")
    sub = parser.add_subparsers(dest="command", required=True)

    delegate = sub.add_parser("delegate", help="delegate one bounded task to OpenCode")
    delegate.add_argument("task")
    delegate.add_argument("--worker")
    delegate.add_argument("--json", action="store_true")
    delegate.set_defaults(func=cmd_delegate)

    resume = sub.add_parser("resume", help="resume the same OpenCode worker session for one correction")
    resume.add_argument("task")
    resume.add_argument("--session", required=True)
    resume.add_argument("--correction", required=True)
    resume.add_argument("--json", action="store_true")
    resume.set_defaults(func=cmd_resume)

    consult = sub.add_parser("consult", help="request a bounded Codex consultation")
    consult.add_argument("request")
    consult.add_argument("--json", action="store_true")
    consult.set_defaults(func=cmd_consult)

    status = sub.add_parser("status", help="show cheap read-only diagnostics")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


def delegate_entrypoint() -> None:
    raise SystemExit(main(["delegate", *sys.argv[1:]]))


def consult_entrypoint() -> None:
    raise SystemExit(main(["consult", *sys.argv[1:]]))


if __name__ == "__main__":
    raise SystemExit(main())
