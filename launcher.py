"""Entry point for the sucklingbot desktop launcher."""

from __future__ import annotations

from pathlib import Path
import os
import sys
import traceback

from launcher.process import BotProcessManager
from launcher.state import LauncherState
from launcher.updater import Updater
from launcher.ui import TrayUI


_launcher_lock_handle = None


def _acquire_launcher_lock(project_root: Path) -> bool:
    """Prevent multiple launcher/tray supervisors for the same checkout."""
    global _launcher_lock_handle

    lock_path = project_root / "data" / "launcher.instance.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    handle.seek(0)

    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False

    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    _launcher_lock_handle = handle
    return True


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def main() -> None:
    project_root = _project_root()
    if not _acquire_launcher_lock(project_root):
        print("[launcher] another sucklingbot launcher is already running; exiting")
        return

    state = LauncherState.load(project_root)
    process_manager = BotProcessManager(
        project_root,
        state,
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
        root = _project_root()
        crash_log = root / "data" / "launcher.crash.log"
        crash_log.parent.mkdir(parents=True, exist_ok=True)
        crash_log.write_text(traceback.format_exc(), encoding="utf-8")
        raise
