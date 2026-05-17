"""Launcher settings persisted as JSON in the data folder."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
from pathlib import Path

import version


@dataclass
class LauncherState:
    auto_restart_on_crash: bool = True
    launch_on_startup: bool = False
    check_interval_minutes: int = 60
    last_seen_version: str = version.VERSION
    last_update_check_iso: str | None = None

    _path: Path | None = None

    @classmethod
    def load(cls, project_root: Path) -> "LauncherState":
        path = Path(project_root) / "data" / "launcher.json"
        path.parent.mkdir(parents=True, exist_ok=True)

        if not path.exists():
            state = cls(_path=path)
            state.save()
            return state

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = cls(_path=path)
            state.save()
            return state

        fields = {
            "auto_restart_on_crash": bool(raw.get("auto_restart_on_crash", True)),
            "launch_on_startup": bool(raw.get("launch_on_startup", False)),
            "check_interval_minutes": int(raw.get("check_interval_minutes", 60)),
            "last_seen_version": str(raw.get("last_seen_version", version.VERSION)),
            "last_update_check_iso": raw.get("last_update_check_iso"),
            "_path": path,
        }
        return cls(**fields)

    def save(self) -> None:
        if self._path is None:
            raise RuntimeError("launcher state path is not set")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data.pop("_path", None)
        self._path.write_text(
            json.dumps(data, indent=2) + "\n",
            encoding="utf-8",
        )

    def mark_update_check(self) -> None:
        self.last_update_check_iso = datetime.now(timezone.utc).isoformat()
        self.save()

    def set_launch_on_startup(self, enabled: bool) -> None:
        self.launch_on_startup = enabled
        self.save()

    def set_last_seen_version(self, bot_version: str) -> None:
        self.last_seen_version = bot_version
        self.save()
