# Codex delegation guidance

Use Codex directly for architecture, requirements ambiguity, security, authentication/authorization, concurrency, ownership/lifetime, data-loss risk, production migration, hard root-cause reasoning, high-impact tradeoffs, and final high-value acceptance.

For substantial, well-specified implementation work, consider one coherent `agent-bridge delegate` bundle. Choose an explicit configured worker alias. Do not delegate trivial edits.

If OpenSpec artifacts exist for the requested change, treat them as the authoritative planning source rather than creating a competing plan. Pass references and task IDs instead of duplicating those artifacts into the task envelope.

After delegation, review the actual diff and compact deterministic evidence. Do not repeat the worker's full discovery unless a targeted check requires it. For ordinary fixable findings, resume the same OpenCode session once with one consolidated correction request.

Do not poll delegated workers. Do not allow recursive Codex ↔ OpenCode delegation.
