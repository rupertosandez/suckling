"""Tray icon, tkinter windows, and launcher user interactions."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
import webbrowser

from PIL import Image
import pystray
from pystray import Menu, MenuItem

from launcher.process import BotProcessManager
from launcher.state import LauncherState
from launcher.updater import (
    DirtyWorkingTreeError,
    PullFailedError,
    UpdateError,
    UpdateInfo,
    Updater,
)

try:
    from plyer import notification
except Exception:  # pragma: no cover - optional runtime dependency
    notification = None


class LogWindow:
    def __init__(self, root: tk.Tk, process_manager: BotProcessManager) -> None:
        self.root = root
        self.process_manager = process_manager
        self.auto_scroll = tk.BooleanVar(value=True)
        self.line_count = 0

        self.window = tk.Toplevel(root)
        self.window.title("sucklingbot log")
        self.window.geometry("900x500")
        self.window.withdraw()
        self.window.protocol("WM_DELETE_WINDOW", self.hide)

        frame = ttk.Frame(self.window)
        frame.pack(fill=tk.BOTH, expand=True)

        self.text = tk.Text(frame, wrap=tk.WORD, state=tk.DISABLED)
        scrollbar = ttk.Scrollbar(frame, command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        buttons = ttk.Frame(self.window)
        buttons.pack(fill=tk.X, padx=6, pady=6)
        ttk.Button(buttons, text="clear", command=self.clear).pack(side=tk.LEFT)
        ttk.Button(buttons, text="copy all", command=self.copy_all).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Checkbutton(
            buttons,
            text="auto-scroll",
            variable=self.auto_scroll,
        ).pack(side=tk.LEFT, padx=(12, 0))

        self.root.after(100, self.poll_logs)

    def show(self) -> None:
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def hide(self) -> None:
        self.window.withdraw()

    def clear(self) -> None:
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.configure(state=tk.DISABLED)
        self.line_count = 0

    def copy_all(self) -> None:
        content = self.text.get("1.0", tk.END).strip()
        self.root.clipboard_clear()
        self.root.clipboard_append(content)

    def poll_logs(self) -> None:
        lines = self.process_manager.drain_logs()
        if lines:
            at_bottom = self._is_at_bottom()
            self.text.configure(state=tk.NORMAL)
            for line in lines:
                self.text.insert(tk.END, line + "\n")
                self.line_count += 1
            self._trim_lines()
            self.text.configure(state=tk.DISABLED)
            if self.auto_scroll.get() and at_bottom:
                self.text.see(tk.END)
        self.root.after(100, self.poll_logs)

    def _is_at_bottom(self) -> bool:
        try:
            return self.text.yview()[1] >= 0.999
        except tk.TclError:
            return True

    def _trim_lines(self) -> None:
        extra = self.line_count - 5000
        if extra <= 0:
            return
        self.text.delete("1.0", f"{extra + 1}.0")
        self.line_count -= extra


class TrayUI:
    def __init__(
        self,
        project_root: Path,
        process_manager: BotProcessManager,
        updater: Updater,
        state: LauncherState,
    ) -> None:
        self.project_root = Path(project_root)
        self.process_manager = process_manager
        self.updater = updater
        self.state = state

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("sucklingbot launcher")

        self.log_window = LogWindow(self.root, process_manager)
        self.update_info: UpdateInfo | None = None
        self._checking_update = False
        self._quit_requested = False

        self.icons = {
            "stopped": Image.new("RGB", (64, 64), "#777777"),
            "running": self._load_icon("tray_icon.ico"),
            "update": self._load_icon("tray_icon_update.ico"),
            "error": self._load_icon("tray_icon_error.ico"),
        }
        self.icon = pystray.Icon(
            "sucklingbot",
            self.icons["stopped"],
            "sucklingbot",
            menu=self._build_menu(),
        )

        process_manager.add_state_callback(self.schedule_menu_refresh)
        process_manager.add_crash_callback(self.show_toast)
        self.root.after(30_000, self._periodic_refresh)

    def run(self) -> None:
        self.icon.run_detached()
        self.refresh_tray()
        self.root.mainloop()

    def start_update_polling(self) -> None:
        self.check_for_updates(show_dialog=False)
        thread = threading.Thread(
            target=self._update_poll_loop,
            name="sucklingbot-update-poller",
            daemon=True,
        )
        thread.start()

    def schedule_menu_refresh(self) -> None:
        self.root.after(0, self.refresh_tray)

    def refresh_tray(self) -> None:
        state = self.process_manager.snapshot()
        if state.crashed:
            icon_key = "error"
        elif self.update_info is not None:
            icon_key = "update"
        elif state.running:
            icon_key = "running"
        else:
            icon_key = "stopped"
        self.icon.icon = self.icons[icon_key]
        self.icon.menu = self._build_menu()
        self.icon.update_menu()

    def _periodic_refresh(self) -> None:
        if self._quit_requested:
            return
        self.refresh_tray()
        self.root.after(30_000, self._periodic_refresh)

    def show_toast(self, message: str, title: str = "sucklingbot") -> None:
        def _notify() -> None:
            if notification is None:
                self.process_manager.log(f"[launcher] toast: {message}")
                return
            try:
                notification.notify(title=title, message=message, app_name="sucklingbot", timeout=5)
            except Exception as exc:
                self.process_manager.log(f"[launcher] toast failed: {exc}")

        threading.Thread(target=_notify, daemon=True).start()

    def check_for_updates(self, show_dialog: bool) -> None:
        if self._checking_update:
            return
        self._checking_update = True
        self.process_manager.log("[launcher] checking for updates")
        self.refresh_tray()

        def worker() -> None:
            info = self.updater.check_for_update()

            def done() -> None:
                self._checking_update = False
                self.update_info = info
                self.state.mark_update_check()
                if info is None:
                    self.process_manager.log("[launcher] no update available")
                    if show_dialog:
                        messagebox.showinfo("sucklingbot", "no update available")
                else:
                    self.process_manager.log(
                        f"[launcher] update available: {info.old_version} -> {info.new_version}"
                    )
                    self.show_toast(
                        f"update available: {info.old_version} -> {info.new_version}"
                    )
                    if show_dialog:
                        self.show_update_dialog(info)
                self.refresh_tray()

            self.root.after(0, done)

        threading.Thread(target=worker, name="sucklingbot-update-check", daemon=True).start()

    def show_update_dialog(self, info: UpdateInfo | None = None) -> None:
        info = info or self.update_info
        if info is None:
            messagebox.showinfo("sucklingbot", "no update available")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("sucklingbot update available")
        dialog.geometry("700x500")
        dialog.transient(self.root)
        dialog.grab_set()

        header = ttk.Label(
            dialog,
            text=f"{info.old_version} -> {info.new_version}",
            font=("Segoe UI", 14, "bold"),
        )
        header.pack(anchor=tk.W, padx=12, pady=(12, 6))

        body_frame = ttk.Frame(dialog)
        body_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)
        text = tk.Text(body_frame, wrap=tk.WORD, height=18)
        scrollbar = ttk.Scrollbar(body_frame, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._insert_changelog(text, info.changelog_excerpt)
        text.configure(state=tk.DISABLED)

        buttons = ttk.Frame(dialog)
        buttons.pack(fill=tk.X, padx=12, pady=12)
        update_button = ttk.Button(buttons, text="update and restart")
        github_button = ttk.Button(
            buttons,
            text="view on github",
            command=lambda: webbrowser.open(info.compare_url),
        )
        close_button = ttk.Button(buttons, text="not now", command=dialog.destroy)
        update_button.pack(side=tk.LEFT)
        github_button.pack(side=tk.LEFT, padx=(8, 0))
        close_button.pack(side=tk.RIGHT)

        def apply_update() -> None:
            update_button.configure(state=tk.DISABLED)
            github_button.configure(state=tk.DISABLED)
            close_button.configure(state=tk.DISABLED)
            text.configure(state=tk.NORMAL)
            text.delete("1.0", tk.END)
            text.insert(tk.END, "updating...\n")
            text.configure(state=tk.DISABLED)

            def worker() -> None:
                try:
                    result = self.updater.apply_update(self.process_manager, info)
                except DirtyWorkingTreeError as exc:
                    self.root.after(0, lambda: self._show_update_error(text, close_button, str(exc)))
                    return
                except PullFailedError as exc:
                    self.root.after(0, lambda: self._show_update_error(text, close_button, str(exc)))
                    return
                except UpdateError as exc:
                    self.root.after(0, lambda: self._show_update_error(text, close_button, str(exc)))
                    return
                except Exception as exc:
                    self.root.after(0, lambda: self._show_update_error(text, close_button, str(exc)))
                    return

                def done() -> None:
                    self.update_info = None
                    self.refresh_tray()
                    dialog.destroy()
                    message = f"updated to {result.new_version} - bot restarted"
                    if result.pip_failed and result.pip_error:
                        message += " (dependency install needs attention)"
                    self.show_toast(message)
                    self.check_for_updates(show_dialog=False)

                self.root.after(0, done)

            threading.Thread(target=worker, name="sucklingbot-apply-update", daemon=True).start()

        update_button.configure(command=apply_update)

    def _show_update_error(self, text: tk.Text, close_button: ttk.Button, message: str) -> None:
        text.configure(state=tk.NORMAL)
        text.delete("1.0", tk.END)
        text.insert(tk.END, message or "update failed")
        text.configure(state=tk.DISABLED)
        close_button.configure(text="close", state=tk.NORMAL)

    def _insert_changelog(self, text: tk.Text, changelog: str) -> None:
        text.tag_configure("bold", font=("Segoe UI", 10, "bold"))
        for raw_line in changelog.splitlines():
            line = raw_line.rstrip()
            tag = "bold" if line.startswith("##") or line.startswith("###") else None
            if line.startswith("- "):
                line = "  * " + line[2:]
            text.insert(tk.END, line + "\n", tag)

    def _update_poll_loop(self) -> None:
        while not self._quit_requested:
            minutes = max(1, self.state.check_interval_minutes)
            for _ in range(minutes * 60):
                if self._quit_requested:
                    return
                time.sleep(1)
            self.root.after(0, lambda: self.check_for_updates(show_dialog=False))

    def _build_menu(self) -> Menu:
        snapshot = self.process_manager.snapshot()
        running = snapshot.running
        items: list[MenuItem] = [
            MenuItem(f"sucklingbot v{self.updater.current_version()}", None, enabled=False),
            MenuItem(f"status: {self._status_text(snapshot)}", None, enabled=False),
            Menu.SEPARATOR,
            MenuItem(
                "start bot",
                lambda _icon, _item: self.process_manager.start(reset_crashes=True),
                enabled=not running,
            ),
            MenuItem("stop bot", lambda _icon, _item: self.process_manager.stop(), enabled=running),
            MenuItem("restart bot", lambda _icon, _item: self.process_manager.restart(), enabled=running),
            Menu.SEPARATOR,
        ]

        if self.update_info is not None:
            items.append(
                MenuItem(
                    f"update available: {self.update_info.old_version} -> {self.update_info.new_version}",
                    lambda _icon, _item: self.root.after(0, self.show_update_dialog),
                )
            )
        items.extend(
            [
                MenuItem(
                    "check for updates now",
                    lambda _icon, _item: self.root.after(
                        0,
                        lambda: self.check_for_updates(show_dialog=True),
                    ),
                    enabled=not self._checking_update,
                ),
                Menu.SEPARATOR,
                MenuItem(
                    "show log window",
                    lambda _icon, _item: self.root.after(0, self.log_window.show),
                ),
                MenuItem(
                    "open data folder",
                    lambda _icon, _item: self._open_folder(self.project_root / "data"),
                ),
                MenuItem(
                    "open project folder",
                    lambda _icon, _item: self._open_folder(self.project_root),
                ),
                Menu.SEPARATOR,
                MenuItem(
                    self._startup_label(),
                    lambda _icon, _item: self.root.after(0, self.toggle_launch_on_startup),
                ),
                MenuItem("quit", lambda _icon, _item: self.root.after(0, self.quit)),
            ]
        )
        return Menu(*items)

    def _status_text(self, snapshot) -> str:
        if snapshot.crashed:
            return "crashed"
        if not snapshot.running:
            return "stopped"
        if snapshot.started_at is None:
            return "running"
        delta = datetime.now(timezone.utc) - snapshot.started_at
        total_minutes = max(0, int(delta.total_seconds() // 60))
        hours, minutes = divmod(total_minutes, 60)
        if hours:
            return f"running for {hours}h {minutes}m"
        if minutes == 0:
            return "running for <1m"
        return f"running for {minutes}m"

    def _startup_label(self) -> str:
        return ("[x]" if self.state.launch_on_startup else "[ ]") + " launch on startup"

    def toggle_launch_on_startup(self) -> None:
        enabled = not self.state.launch_on_startup
        try:
            if enabled:
                create_startup_shortcut(self.project_root)
            else:
                remove_startup_shortcut()
        except Exception as exc:
            messagebox.showerror("sucklingbot", f"could not update startup shortcut:\n{exc}")
            return
        self.state.set_launch_on_startup(enabled)
        self.refresh_tray()

    def quit(self) -> None:
        self._quit_requested = True
        self.process_manager.stop()
        self.icon.stop()
        self.root.quit()

    def _open_folder(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
            return
        webbrowser.open(path.as_uri())

    def _load_icon(self, filename: str) -> Image.Image:
        path = self.project_root / "assets" / filename
        if path.exists():
            return Image.open(path)
        return Image.new("RGB", (64, 64), "gray")


def startup_shortcut_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set")
    return (
        Path(appdata)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
        / "sucklingbot.lnk"
    )


def create_startup_shortcut(project_root: Path) -> None:
    shortcut = startup_shortcut_path()
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    target = Path(project_root) / "launch.vbs"
    if not target.exists():
        target = Path(project_root) / "launch.bat"
    command = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$shortcut = $shell.CreateShortcut('{_ps_escape(str(shortcut))}'); "
        f"$shortcut.TargetPath = '{_ps_escape(str(target))}'; "
        f"$shortcut.WorkingDirectory = '{_ps_escape(str(project_root))}'; "
        "$shortcut.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
    )


def remove_startup_shortcut() -> None:
    shortcut = startup_shortcut_path()
    if shortcut.exists():
        shortcut.unlink()


def _ps_escape(value: str) -> str:
    return value.replace("'", "''")
