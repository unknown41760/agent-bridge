from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import tomllib


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    alias: str
    model: str


@dataclass(slots=True)
class BridgeConfig:
    path: Path | None
    workers: dict[str, WorkerConfig]
    default_worker: str | None = None

    def resolve_worker(self, alias: str | None) -> WorkerConfig:
        selected = alias or self.default_worker
        if not selected:
            raise ConfigError("worker_not_configured: no worker alias selected and no default configured")
        try:
            return self.workers[selected]
        except KeyError as exc:
            raise ConfigError(f"worker_not_configured: unknown worker alias {selected!r}") from exc


def candidate_paths(cwd: Path) -> list[Path]:
    explicit = os.environ.get("AGENT_BRIDGE_CONFIG")
    paths: list[Path] = []
    if explicit:
        paths.append(Path(explicit).expanduser())
    paths.extend([cwd / "agent-bridge.toml", cwd / ".agent-bridge.toml"])
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    paths.append(xdg / "agent-bridge" / "config.toml")
    return paths


def load_config(cwd: str | Path = ".") -> BridgeConfig:
    base = Path(cwd).resolve()
    selected: Path | None = next((path for path in candidate_paths(base) if path.is_file()), None)
    if selected is None:
        return BridgeConfig(path=None, workers={}, default_worker=None)
    data = tomllib.loads(selected.read_text(encoding="utf-8"))
    workers_raw = data.get("workers", {})
    if not isinstance(workers_raw, dict):
        raise ConfigError("[workers] must be a table")
    workers: dict[str, WorkerConfig] = {}
    for alias, raw in workers_raw.items():
        if not isinstance(raw, dict):
            raise ConfigError(f"[workers.{alias}] must be a table")
        model = raw.get("model")
        if not isinstance(model, str) or not model.strip() or "/" not in model:
            raise ConfigError(f"workers.{alias}.model must be a non-empty provider/model string")
        workers[alias] = WorkerConfig(alias=alias, model=model)
    defaults = data.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ConfigError("[defaults] must be a table")
    default_worker = defaults.get("worker")
    if default_worker is not None and not isinstance(default_worker, str):
        raise ConfigError("defaults.worker must be a string")
    return BridgeConfig(path=selected, workers=workers, default_worker=default_worker)
