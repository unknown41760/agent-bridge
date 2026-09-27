# agent-bridge

A deliberately small Codex ↔ OpenCode bridge. Codex stays the premium coordinator; OpenCode stays the multi-provider coding harness. The bridge only validates a small task contract, resolves an exact worker alias, invokes the existing CLIs, hands sessions back, normalizes compact results, checks repository scope, runs explicit validation commands, prevents recursive delegation, and records lightweight execution history.

It does **not** provide a model/provider abstraction, planner, agent loop, server, daemon, scheduler, message queue, workspace service, or second session system.

## Requirements

- Python 3.12+
- Git for writable delegation
- OpenCode for `delegate` / `resume`
- Codex CLI for `consult`

The package has no runtime Python dependencies outside the standard library.

## Configuration

Create `agent-bridge.toml` in the repository, `.agent-bridge.toml`, or `$XDG_CONFIG_HOME/agent-bridge/config.toml`. `AGENT_BRIDGE_CONFIG` can point to an explicit file.

```toml
[workers.fast]
model = "configured-provider/configured-model"

[workers.local]
model = "lmstudio/local-model-id"

[defaults]
worker = "fast"
```

Aliases map only to exact OpenCode `provider/model` strings. Provider credentials and provider configuration stay in OpenCode. The bridge never silently substitutes another worker or model.

## Commands

```bash
agent-bridge delegate task.json
agent-bridge delegate task.json --worker fast --json

agent-bridge resume task.json \
  --session ses_123 \
  --correction correction.json

agent-bridge consult request.json --json
agent-bridge status --json
```

`codex-delegate` and `codex-consult` are convenience entry points with no independent logic.

## Current CLI assumptions

The vendor-specific assumptions are isolated in `opencode_adapter.py` and `codex_adapter.py`.

OpenCode v2.0.18 supports `--model provider/model`, `--session`, and `--format json` on `run`. The bridge sets the subprocess working directory instead of passing a directory flag. The adapter expects JSON events with a top-level `sessionID`; a live run is still needed to verify that event contract against v2.0.18.

Codex's current `exec` surface supports JSONL events, `--output-schema`, a working directory, sandbox configuration, and thread IDs in `thread.started`. Consultations use a read-only sandbox and structured final output.

If either CLI changes, update only its adapter and adapter tests.

## Task contract

A task envelope intentionally carries only execution-relevant information:

```json
{
  "version": 1,
  "id": "retry-downloads",
  "objective": "Implement bounded retry handling for transient download failures.",
  "acceptance_criteria": [
    "Transient failures are retried.",
    "Permanent failures remain visible.",
    "Regression coverage is added."
  ],
  "allowed_paths": ["src/downloader/**", "tests/downloader/**"],
  "forbidden_paths": [".env", "**/*.pem", "**/*.key"],
  "validation_commands": ["python -m unittest"],
  "worker": "fast",
  "source": {
    "kind": "openspec",
    "change": "retry-downloads",
    "task_ids": ["2.1", "2.2"]
  },
  "caller": {"harness": "codex"}
}
```

OpenSpec is optional. When it is already present, references should point to its proposal/spec/design/tasks artifacts rather than copying them into a second specification format.

## Deterministic workspace evidence

For each writable run the bridge requires a Git repository, fingerprints the tracked and ordinary untracked working tree before execution, and additionally scans paths covered by explicit allowed/forbidden patterns so ignored protected files can still be detected. The post-run snapshot is compared with that exact baseline; pre-existing dirty files are not attributed to the worker unless their content changes further.

Explicit validation commands run after the worker returns. Only command, exit code, status, and bounded tail output are returned to the coordinator.

This is a guardrail, not an OS sandbox. If a protected or out-of-scope path changes, the bridge reports the violation and does not silently revert it.

## Sessions and recursion

A correction resumes the original OpenCode session and the exact worker/model recorded in bridge history. It does not re-resolve the alias to a potentially different model.

Codex-originated OpenCode work receives `AGENT_BRIDGE_NO_CODEX_REENTRY=1`. Reverse Codex consultation receives both Codex- and OpenCode-reentry guards plus an explicit direct-answer prompt. CLI entry points fail closed when those guards are present.

## Status

`agent-bridge status` performs only cheap read-only checks: CLI presence/version, configured aliases, and whether `opencode models` currently lists each configured model. It does not make a paid inference request and never prints credentials.

## Tests

```bash
python -m unittest discover -s tests -v
```

The normal suite uses fake process runners and temporary Git repositories. Optional live tests are disabled unless explicit environment variables are set; installing or testing the package does not spend provider credit by default.
