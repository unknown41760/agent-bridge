# Codex delegation guidance

Use Codex directly for architecture, requirements ambiguity, security, authentication/authorization, concurrency, ownership/lifetime, data-loss risk, production migration, hard root-cause reasoning, high-impact tradeoffs, and final high-value acceptance.

Do changes confined to one small module and its tests, or work expected to take about three minutes, directly in Codex. For substantial, well-specified implementation work, consider one coherent `agent-bridge delegate` bundle. Choose the configured `flash` worker alias and let it own code and tests for the bounded slice.

If OpenSpec artifacts exist for the requested change, treat them as the authoritative planning source rather than creating a competing plan. Pass references and task IDs instead of duplicating those artifacts into the task envelope.

Invoke `scripts/invoke-worker.ps1` from the target Git root. Configure the Synthetic proxy through `AGENT_BRIDGE_SYNTHETIC_PROXY` or the ignored `scripts/proxy.local.txt`; the launcher scopes it and the Python environment to the worker call. After delegation, review the actual diff and compact deterministic evidence once. Run additional checks only for risks the bridge's validation did not cover. For ordinary fixable findings, resume the same OpenCode session once with one consolidated correction request.

Do not poll delegated workers. Do not allow recursive Codex ↔ OpenCode delegation.
