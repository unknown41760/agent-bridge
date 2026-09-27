from __future__ import annotations

from pathlib import Path
import subprocess

from .protocol import CheckResult


def _compact(text: str, limit: int = 1200) -> str:
    text = "\n".join(line.rstrip() for line in text.strip().splitlines()[-20:])
    if len(text) > limit:
        text = text[-limit:]
    return text


def run_validation_commands(commands: list[str], cwd: str | Path, timeout: int = 300) -> list[CheckResult]:
    results: list[CheckResult] = []
    for command in commands:
        try:
            proc = subprocess.run(
                command,
                cwd=Path(cwd),
                shell=True,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            combined = "\n".join(part for part in [proc.stdout, proc.stderr] if part)
            results.append(
                CheckResult(
                    command=command,
                    status="passed" if proc.returncode == 0 else "failed",
                    summary=_compact(combined) or f"exit code {proc.returncode}",
                    exit_code=proc.returncode,
                )
            )
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") + "\n" + (exc.stderr or "")
            results.append(
                CheckResult(
                    command=command,
                    status="failed",
                    summary=_compact(output) or f"timed out after {timeout}s",
                    exit_code=124,
                )
            )
    return results
