"""Subprocess lifecycle management for the desktop launcher."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from typing import Callable

from launcher.state import LauncherState


MAX_LOG_LINES = 5000
CRASH_WINDOW_SECONDS = 60
CRASH_LIMIT = 3
RESTART_DELAY_SECONDS = 2
STOP_TIMEOUT_SECONDS = 10


StateCallback = Callable[[], None]
CrashCallback = Callable[[str], None]


@dataclass(frozen=True)
class ProcessSnapshot:
    """Small immutable status snapshot for UI rendering."""

    running: bool
    crashed: bool
    started_at: datetime | None
    return_code: int | None
    external_pid: int | None = None


class BotProcessManager:
    """Start, stop, restart, and observe the bot child process."""

    def __init__(
        self,
        project_root: Path,
        state: LauncherState,
        auto_restart_on_crash: bool = True,
    ) -> None:
        self.project_root = Path(project_root)
        self.state = state
        self.auto_restart_on_crash = auto_restart_on_crash
        self.log_queue: queue.Queue[str] = queue.Queue(maxsize=MAX_LOG_LINES)

        self._process: subprocess.Popen[str] | None = None
        self._external_pid: int | None = None
        self._reader_thread: threading.Thread | None = None
        self._watcher_thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._intentional_stop = False
        self._crashed = False
        self._started_at: datetime | None = None
        self._last_return_code: int | None = None
        self._crash_times: deque[float] = deque()
        self._state_callbacks: list[StateCallback] = []
        self._crash_callbacks: list[CrashCallback] = []

    def add_state_callback(self, callback: StateCallback) -> None:
        self._state_callbacks.append(callback)

    def add_crash_callback(self, callback: CrashCallback) -> None:
        self._crash_callbacks.append(callback)

    def snapshot(self) -> ProcessSnapshot:
        with self._lock:
            running = self._process is not None and self._process.poll() is None
            external_pid = self._external_pid
            if not running and external_pid is not None:
                if _pid_is_running(external_pid):
                    running = True
                else:
                    self.log(f"[launcher] clearing stale bot pid {external_pid}")
                    self._external_pid = None
                    external_pid = None
                    self.state.set_bot_pid(None)
            return ProcessSnapshot(
                running=running,
                crashed=self._crashed,
                started_at=self._started_at,
                return_code=self._last_return_code,
                external_pid=external_pid,
            )

    def is_running(self) -> bool:
        return self.snapshot().running

    def resolve_python(self) -> Path:
        if os.name == "nt":
            candidate = self.project_root / "venv" / "Scripts" / "python.exe"
        else:
            candidate = self.project_root / "venv" / "bin" / "python"
        if candidate.exists():
            return candidate

        self.log("[launcher] venv python not found, using current python")
        return Path(sys.executable)

    def start(self, reset_crashes: bool = False) -> bool:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self.log("[launcher] bot is already running")
                return False

            recorded_pid = self.state.bot_pid
            if recorded_pid is not None and _pid_is_running(recorded_pid):
                self._external_pid = recorded_pid
                self._crashed = False
                self.log(
                    "[launcher] bot already appears to be running "
                    f"as pid {recorded_pid}; not starting another"
                )
                self._notify_state()
                return False
            if recorded_pid is not None:
                self.log(f"[launcher] clearing stale bot pid {recorded_pid}")
                self.state.set_bot_pid(None)

            if reset_crashes:
                self._crash_times.clear()
                self._crashed = False

            python_exe = self.resolve_python()
            creationflags = 0
            if os.name == "nt":
                creationflags = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW
                )

            self._intentional_stop = False
            self._crashed = False
            self._last_return_code = None
            self._external_pid = None
            self._started_at = datetime.now(timezone.utc)

            self.log(f"[launcher] starting bot with {python_exe}")
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            self._process = subprocess.Popen(
                [str(python_exe), "-u", "bot.py"],
                cwd=self.project_root,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
            self.state.set_bot_pid(self._process.pid)
            self.log(f"[launcher] bot pid {self._process.pid}")

            self._reader_thread = threading.Thread(
                target=self._read_output,
                args=(self._process,),
                name="sucklingbot-output-reader",
                daemon=True,
            )
            self._reader_thread.start()

            self._watcher_thread = threading.Thread(
                target=self._watch_process,
                args=(self._process,),
                name="sucklingbot-process-watcher",
                daemon=True,
            )
            self._watcher_thread.start()

        self._notify_state()
        return True

    def stop(self) -> bool:
        with self._lock:
            process = self._process
            external_pid = self._external_pid
            if process is None or process.poll() is not None:
                self._process = None
                self._started_at = None
                process = None
                if external_pid is None:
                    self.log("[launcher] bot is already stopped")
                elif _pid_is_running(external_pid):
                    self.log(f"[launcher] stopping externally running bot pid {external_pid}")
                else:
                    self.log(f"[launcher] clearing stale bot pid {external_pid}")
                    self._external_pid = None
                    self.state.set_bot_pid(None)
                    external_pid = None
            else:
                self._intentional_stop = True

        if process is None:
            if external_pid is not None:
                stopped = _terminate_pid(external_pid)
                with self._lock:
                    self._external_pid = None
                    self._started_at = None
                    self._crashed = False
                    self.state.set_bot_pid(None)
                if stopped:
                    self.log(f"[launcher] external bot pid {external_pid} stopped")
                else:
                    self.log(f"[launcher] couldn't stop external bot pid {external_pid}")
                self._notify_state()
                return stopped
            self._notify_state()
            return False

        self.log("[launcher] stopping bot")
        self._request_graceful_stop(process)

        try:
            process.wait(timeout=STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self.log("[launcher] bot did not stop cleanly, killing it")
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.log("[launcher] bot process did not exit after kill")

        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2)
        if self._watcher_thread and self._watcher_thread.is_alive():
            self._watcher_thread.join(timeout=2)

        with self._lock:
            self._last_return_code = process.returncode
            self._process = None
            self._started_at = None
            self._crashed = False
            self.state.set_bot_pid(None)

        self.log(f"[launcher] bot stopped with exit code {process.returncode}")
        self._notify_state()
        return True

    def _request_graceful_stop(self, process: subprocess.Popen[str]) -> None:
        if process.stdin is not None:
            try:
                process.stdin.write("shutdown\n")
                process.stdin.flush()
                process.stdin.close()
                return
            except Exception as exc:
                self.log(f"[launcher] stdin stop request failed: {exc}")

        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.terminate()
        except Exception as exc:
            self.log(f"[launcher] graceful stop signal failed: {exc}")

    def restart(self) -> None:
        self.log("[launcher] restarting bot")
        self.stop()
        self.start(reset_crashes=True)

    def log(self, line: str) -> None:
        text = line.rstrip("\r\n")
        if not text:
            return
        while True:
            try:
                self.log_queue.put_nowait(text)
                return
            except queue.Full:
                try:
                    self.log_queue.get_nowait()
                except queue.Empty:
                    return

    def drain_logs(self, max_lines: int = 1000) -> list[str]:
        lines: list[str] = []
        for _ in range(max_lines):
            try:
                lines.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        return lines

    def _read_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        try:
            for line in process.stdout:
                self.log(line)
        except Exception as exc:
            self.log(f"[launcher] output reader failed: {exc}")
        finally:
            try:
                process.stdout.close()
            except Exception:
                pass

    def _watch_process(self, process: subprocess.Popen[str]) -> None:
        return_code = process.wait()
        with self._lock:
            if process is not self._process:
                return
            intentional = self._intentional_stop
            self._last_return_code = return_code
            self._process = None
            self._started_at = None
            self.state.set_bot_pid(None)

        if intentional:
            self._notify_state()
            return

        self.log(f"[launcher] bot exited unexpectedly with code {return_code}")
        self._handle_crash(return_code)

    def _handle_crash(self, return_code: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._crash_times.append(now)
            while self._crash_times and now - self._crash_times[0] > CRASH_WINDOW_SECONDS:
                self._crash_times.popleft()

            crash_count = len(self._crash_times)
            should_restart = self.auto_restart_on_crash and crash_count < CRASH_LIMIT
            if not should_restart:
                self._crashed = True

        if should_restart:
            self.log(
                "[launcher] restarting after crash "
                f"({crash_count}/{CRASH_LIMIT} within {CRASH_WINDOW_SECONDS}s)"
            )
            self._notify_state()
            time.sleep(RESTART_DELAY_SECONDS)
            self.start()
            return

        message = "sucklingbot crashed too many times - check the log"
        self.log(f"[launcher] {message} (last exit code {return_code})")
        self._notify_state()
        for callback in list(self._crash_callbacks):
            try:
                callback(message)
            except Exception as exc:
                self.log(f"[launcher] crash callback failed: {exc}")

    def _notify_state(self) -> None:
        for callback in list(self._state_callbacks):
            try:
                callback()
            except Exception as exc:
                self.log(f"[launcher] state callback failed: {exc}")


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate_pid(pid: int) -> bool:
    try:
        if os.name == "nt":
            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        return False

    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if not _pid_is_running(pid):
            return True
        time.sleep(0.2)

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return True
    return not _pid_is_running(pid)
