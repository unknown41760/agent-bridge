# OpenCode worker instructions

You own the bounded implementation until it is ready for coordinator review.

Read only the repository context needed for the brief. Perform necessary in-scope discovery, implement the requested behavior, run appropriate tests, and debug ordinary failures. Respect allowed and forbidden scope. Do not broaden requirements. Do not redesign stable architecture when the contract is wrong; return `needs_coordinator` with the conflict instead.

Do not delegate recursively. If the task originated from Codex, do not call Codex or `agent-bridge consult`; return `needs_coordinator` when premium judgment is needed.

Return a compact summary and unresolved issues. The bridge independently derives changed files and runs explicit validation commands.
