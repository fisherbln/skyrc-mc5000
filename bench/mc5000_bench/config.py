"""Where the app keeps its settings, presets and last results."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "mc5000-bench"


@dataclass
class Settings:
    url: str = "http://homeassistant.local:8123"
    token: str = ""
    verify_tls: bool = True

    @property
    def complete(self) -> bool:
        return bool(self.url.strip() and self.token.strip())


def _path() -> Path:
    return config_dir() / "settings.json"


def load_settings() -> Settings:
    path = _path()
    if not path.exists():
        return Settings()
    raw: dict[str, Any] = json.loads(path.read_text())
    return Settings(**{k: v for k, v in raw.items() if k in Settings.__dataclass_fields__})


def save_settings(settings: Settings) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # The token can control Home Assistant: keep the file private.
    fd = os.open(path.with_suffix(".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(asdict(settings), fh, indent=2)
    os.chmod(path.with_suffix(".tmp"), 0o600)
    path.with_suffix(".tmp").replace(path)


def load_json(name: str) -> Any:
    path = config_dir() / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def save_json(name: str, data: Any) -> None:
    path = config_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)
