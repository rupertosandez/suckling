"""Git-based update checks and update application for the launcher."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
from typing import Callable


LogCallback = Callable[[str], None]
NO_WINDOW_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class UpdateError(Exception):
    """Base class for update failures shown in the launcher UI."""


class DirtyWorkingTreeError(UpdateError):
    """Raised when local changes would make an automatic update unsafe."""


class PullFailedError(UpdateError):
    """Raised when `git pull` fails."""


@dataclass(frozen=True)
class UpdateInfo:
    old_version: str
    new_version: str
    old_sha: str
    new_sha: str
    changelog_excerpt: str
    compare_url: str


@dataclass(frozen=True)
class UpdateResult:
    new_version: str
    pip_failed: bool = False
    pip_error: str | None = None


class Updater:
    def __init__(self, project_root: Path, log: LogCallback | None = None) -> None:
        self.project_root = Path(project_root)
        self.log = log or (lambda _line: None)

    def check_for_update(self) -> UpdateInfo | None:
        fetch = self._run(["git", "fetch", "origin", "main"], timeout=30)
        if fetch.returncode != 0:
            self.log(f"[launcher] update check failed: {fetch.stderr.strip()}")
            return None

        head = self._git_stdout(["git", "rev-parse", "HEAD"])
        remote = self._git_stdout(["git", "rev-parse", "origin/main"])
        if not head or not remote or head == remote:
            return None

        if self._run(["git", "merge-base", "--is-ancestor", remote, head], timeout=30).returncode == 0:
            return None
        if self._run(["git", "merge-base", "--is-ancestor", head, remote], timeout=30).returncode != 0:
            self.log("[launcher] update check found diverged local and remote branches")
            return None

        remote_version_py = self._git_stdout(["git", "show", "origin/main:version.py"])
        new_version = parse_version(remote_version_py)
        if not new_version:
            self.log("[launcher] update check found origin/main but could not parse version.py")
            return None

        current_version = self.current_version()
        remote_changelog = self._git_stdout(["git", "show", "origin/main:CHANGELOG.md"])
        excerpt = extract_changelog_range(
            remote_changelog,
            current_version=current_version,
            new_version=new_version,
        )
        compare_url = f"https://github.com/rupertosandez/suckling/compare/{head}...{remote}"
        return UpdateInfo(
            old_version=current_version,
            new_version=new_version,
            old_sha=head,
            new_sha=remote,
            changelog_excerpt=excerpt,
            compare_url=compare_url,
        )

    def apply_update(self, process_manager, update_info: UpdateInfo | None = None) -> UpdateResult:
        status = self._git_stdout(["git", "status", "--porcelain"])
        if status.strip():
            raise DirtyWorkingTreeError(
                "you have local changes - please commit or stash before updating"
            )

        before_requirements = self._read_requirements()
        target_info = update_info or self.check_for_update()
        if target_info is None:
            return UpdateResult(new_version=self.current_version())

        process_manager.stop()

        pull = self._run_streaming(["git", "pull", "--ff-only", "origin", "main"])
        if pull.returncode != 0:
            process_manager.start()
            raise PullFailedError(pull.stderr.strip() or "git pull failed")

        after_requirements = self._read_requirements()
        pip_failed = False
        pip_error: str | None = None
        if before_requirements != after_requirements:
            python_exe = str(process_manager.resolve_python())
            pip = self._run_streaming([python_exe, "-m", "pip", "install", "-r", "requirements.txt"])
            if pip.returncode != 0:
                pip_failed = True
                pip_error = pip.stderr.strip() or "pip install failed"
                self.log(f"[launcher] dependency install failed: {pip_error}")

        process_manager.start()

        new_version = self.current_version()
        return UpdateResult(
            new_version=new_version or target_info.new_version,
            pip_failed=pip_failed,
            pip_error=pip_error,
        )

    def current_version(self) -> str:
        path = self.project_root / "version.py"
        if not path.exists():
            return "unknown"
        return parse_version(path.read_text(encoding="utf-8")) or "unknown"

    def _read_requirements(self) -> str:
        path = self.project_root / "requirements.txt"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def _git_stdout(self, command: list[str]) -> str:
        result = self._run(command, timeout=30)
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def _run(self, command: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                cwd=self.project_root,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout,
                check=False,
                creationflags=NO_WINDOW_FLAGS,
            )
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(
                command,
                returncode=124,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "command timed out",
            )

    def _run_streaming(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.log(f"[launcher] running: {' '.join(command)}")
        process = subprocess.Popen(
            command,
            cwd=self.project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW_FLAGS,
        )

        output: list[str] = []
        if process.stdout is not None:
            for line in process.stdout:
                text = line.rstrip("\r\n")
                output.append(text)
                self.log(text)
        return_code = process.wait()
        combined = "\n".join(output)
        return subprocess.CompletedProcess(command, return_code, combined, combined)


def parse_version(source: str) -> str | None:
    match = re.search(r'VERSION\s*=\s*["\']([^"\']+)["\']', source)
    if not match:
        return None
    return match.group(1)


def extract_changelog_range(changelog: str, current_version: str, new_version: str) -> str:
    entries = list(_iter_changelog_entries(changelog))
    if not entries:
        return "no changelog details found."

    selected: list[str] = []
    collecting = False
    for entry_version, body in entries:
        if entry_version == new_version:
            collecting = True
        if collecting:
            if entry_version == current_version:
                break
            selected.append(body.rstrip())

    if not selected:
        return "no changelog details found."
    return "\n\n".join(selected).strip()


def _iter_changelog_entries(changelog: str):
    header_pattern = re.compile(r"^## \[([^\]]+)\].*$", re.MULTILINE)
    matches = list(header_pattern.finditer(changelog))
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(changelog)
        yield match.group(1), changelog[start:end].strip()
