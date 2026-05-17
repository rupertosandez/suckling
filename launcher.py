"""Entry point for the sucklingbot desktop launcher."""

from __future__ import annotations

from pathlib import Path
import sys
import traceback

from launcher.process import BotProcessManager
from launcher.state import LauncherState
from launcher.updater import Updater
from launcher.ui import TrayUI


def main() -> None:
    project_root = Path(__file__).resolve().parent
    state = LauncherState.load(project_root)
    process_manager = BotProcessManager(
        project_root,
        auto_restart_on_crash=state.auto_restart_on_crash,
    )
    updater = Updater(project_root, log=process_manager.log)
    ui = TrayUI(project_root, process_manager, updater, state)

    process_manager.start()
    ui.start_update_polling()
    ui.run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        root = Path(__file__).resolve().parent
        crash_log = root / "data" / "launcher.crash.log"
        crash_log.parent.mkdir(parents=True, exist_ok=True)
        crash_log.write_text(traceback.format_exc(), encoding="utf-8")
        raise
