"""
Terminal + — PyQt6 desktop terminal emulator.
A real native window with tabs, sudo modal, and dark sleek UI.
"""

from __future__ import annotations

import logging
import os
import pty
import re
import select
import signal
import subprocess
import threading
from dataclasses import dataclass, field

from PyQt6.QtCore import QThread, pyqtSignal, Qt, QProcess
from PyQt6.QtGui import QColor, QFont, QKeySequence, QPalette, QShortcut, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTabBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from checker import check, Severity
from store import StorPane, detect_package_manager
from termy import TermyDrawer

logger = logging.getLogger(__name__)

# ── Colours ────────────────────────────────────────────────────────────────────

BG_MAIN   = "#0d0d0d"
BG_BAR    = "#111111"
BG_HEADER = "#181818"
BORDER    = "#2a2a2a"
TEAL      = "#5DCAA5"
TEXT      = "#e0e0e0"
MUTED     = "#888888"
RED_ERR   = "#E24B4A"
GREEN_BTN = "#0F6E56"
GREEN_TXT = "#9FE1CB"
AMBER     = "#EF9F27"

ANSI_ESCAPE = re.compile(
    r"\x1b\[[0-9;]*[mGKHFABCDJsu]"   # CSI sequences (colour, cursor)
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC sequences ]...BEL or ST
    r"|\x1b[=>NOP\\]"                 # single-char escapes
    r"|\x1b[()][AB012]"               # charset designations
    r"|\r"                            # carriage returns
    r"|\x00"                          # null bytes
)

# OSC sequences that don't end with BEL — catch ]NNNN;...\ style (sudo/dnf shell integration)
OSC_LOOSE = re.compile(r"\x1b?\][\d]+;[^\n]*\\?")


# ── System info ────────────────────────────────────────────────────────────────

def detect_system_info() -> tuple[str, str, int]:
    """Read distro name, version, and installed RAM from /etc/os-release and /proc/meminfo."""
    distro_name, distro_version, ram_gb = "Linux", "", 0

    try:
        with open("/etc/os-release") as fh:
            info = {
                k: v.strip('"')
                for line in fh
                if "=" in line
                for k, v in [line.strip().split("=", 1)]
            }
        distro_name = info.get("NAME", "Linux").strip('"')
        distro_version = info.get("VERSION_ID", "").strip('"')
    except OSError as exc:
        logger.warning("Could not read /etc/os-release: %s", exc)

    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    ram_gb = round(int(line.split()[1]) / 1024 / 1024)
                    break
    except OSError as exc:
        logger.warning("Could not read /proc/meminfo: %s", exc)

    return distro_name, distro_version, ram_gb


# ── Worker thread ──────────────────────────────────────────────────────────────

@dataclass
class TerminalSession:
    """Per-tab state."""
    cwd: str = field(default_factory=os.getcwd)
    history: list[str] = field(default_factory=list)
    history_index: int = -1


class CommandWorker(QThread):
    """Runs a shell command in a PTY and emits output line by line."""

    output_ready = pyqtSignal(str)
    command_done = pyqtSignal(int)   # exit code
    yn_prompt    = pyqtSignal(str)   # emitted when a Y/N question is detected

    # Patterns that indicate an interactive Y/N prompt
    YN_PATTERN = re.compile(
        r"\[y/n\]|\[Y/N\]|\[yes/no\]|\(y/n\)|\(Y/N\)|"
        r"Is this ok\s*\?|Continue\s*\?|Proceed\s*\?|Are you sure\s*\?|"
        r"Do you want|Overwrite\?|Remove\?|Replace\?|"
        r"\[Y\]es|\[N\]o|yes/no|y/N|Y/n",
        re.IGNORECASE,
    )

    def __init__(self, cmd: str, cwd: str) -> None:
        super().__init__()
        self.cmd = cmd
        self.cwd = cwd
        self._proc: subprocess.Popen | None = None
        self._master_fd: int | None = None
        self._waiting_for_yn = False

    def send_input(self, text: str) -> None:
        """Write a response into the running process stdin via the PTY."""
        if self._master_fd is not None:
            try:
                os.write(self._master_fd, (text + "\n").encode())
                self._waiting_for_yn = False
            except OSError as exc:
                logger.warning("Could not write to PTY: %s", exc)

    def run(self) -> None:
        try:
            master_fd, slave_fd = pty.openpty()
            self._master_fd = master_fd
            self._proc = subprocess.Popen(
                self.cmd,
                shell=True,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=self.cwd,
                env={**os.environ, "TERM": "xterm-256color"},
                close_fds=True,
            )
            os.close(slave_fd)

            buf = b""
            idle_ticks = 0  # counts consecutive 50ms timeouts with no new data

            while True:
                try:
                    ready, _, _ = select.select([master_fd], [], [], 0.05)
                except (ValueError, OSError):
                    break

                if ready:
                    idle_ticks = 0
                    try:
                        chunk = os.read(master_fd, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    buf += chunk

                    # Flush all complete lines
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        clean = self._clean(line.decode("utf-8", errors="replace"))
                        if not clean:
                            continue
                        if self.YN_PATTERN.search(clean) and not self._waiting_for_yn:
                            self._waiting_for_yn = True
                            self.yn_prompt.emit(clean)
                        else:
                            self.output_ready.emit(clean)

                    # Check the partial buffer — prompts often have no trailing newline.
                    # After 3 idle ticks (~150ms) with no new data, inspect what's sitting
                    # in the buffer. If it looks like a Y/N prompt, surface it.
                    partial = self._clean(buf.decode("utf-8", errors="replace"))
                    if partial and self.YN_PATTERN.search(partial) and not self._waiting_for_yn:
                        self._waiting_for_yn = True
                        buf = b""
                        self.yn_prompt.emit(partial)

                else:
                    idle_ticks += 1

                    # Check partial buffer after brief idle — catches prompts with no newline
                    if idle_ticks == 3 and buf:
                        partial = self._clean(buf.decode("utf-8", errors="replace"))
                        if partial:
                            if self.YN_PATTERN.search(partial) and not self._waiting_for_yn:
                                self._waiting_for_yn = True
                                buf = b""
                                self.yn_prompt.emit(partial)
                            else:
                                self.output_ready.emit(partial)
                                buf = b""

                    if self._proc.poll() is not None:
                        break

            # Flush any remaining buffer content
            if buf:
                clean = self._clean(buf.decode("utf-8", errors="replace"))
                if clean:
                    self.output_ready.emit(clean)

            try:
                os.close(master_fd)
            except OSError:
                pass
            self._master_fd = None

            self._proc.wait()
            self.command_done.emit(self._proc.returncode)

        except OSError as exc:
            logger.exception("PTY error running command: %s", self.cmd)
            self.output_ready.emit(f"[error] {exc}")
            self.command_done.emit(1)

    @staticmethod
    def _clean(text: str) -> str:
        """Strip all escape sequences from a line of output."""
        result = ANSI_ESCAPE.sub("", text)
        result = OSC_LOOSE.sub("", result)
        return result.strip()

    def terminate_process(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGINT)
            except (OSError, ProcessLookupError):
                self._proc.terminate()


# ── Sudo dialog ────────────────────────────────────────────────────────────────

class SudoDialog(QDialog):
    """Modal dialog that collects the sudo password."""

    def __init__(self, command: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("sudo authentication")
        self.setFixedWidth(420)
        self.setModal(True)
        self._build_ui(command)
        self._apply_styles()

    def _build_ui(self, command: str) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel("sudo authentication")
        title.setObjectName("sudo-title")
        layout.addWidget(title)

        cmd_label = QLabel(f"Command:  {command}")
        cmd_label.setObjectName("sudo-cmd")
        cmd_label.setWordWrap(True)
        layout.addWidget(cmd_label)

        layout.addWidget(QLabel("Password:"))

        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("Enter your password…")
        layout.addWidget(self.password_input)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Confirm")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.password_input.returnPressed.connect(self.accept)

    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QDialog {{
                background: #1a1a1a;
            }}
            QLabel {{
                color: {TEXT};
                font-size: 13px;
            }}
            QLabel#sudo-title {{
                color: {TEAL};
                font-size: 14px;
                font-weight: bold;
            }}
            QLabel#sudo-cmd {{
                color: {MUTED};
                font-size: 12px;
            }}
            QLineEdit {{
                background: {BG_MAIN};
                border: 1px solid {BORDER};
                border-radius: 4px;
                color: {TEXT};
                padding: 6px 8px;
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border: 1px solid {TEAL};
            }}
            QPushButton {{
                padding: 6px 16px;
                border-radius: 4px;
                font-size: 13px;
            }}
            QPushButton[text="Confirm"] {{
                background: {GREEN_BTN};
                color: {GREEN_TXT};
                border: none;
            }}
            QPushButton[text="Cancel"] {{
                background: {BORDER};
                color: {MUTED};
                border: none;
            }}
        """)

    def get_password(self) -> str:
        return self.password_input.text()


# ── Pre-run confirmation dialog ───────────────────────────────────────────────

# Package manager actions that need confirmation
_PM_ACTIONS = {
    "install": ("Installing", "install"),
    "update":  ("Updating",   "update"),
    "upgrade": ("Upgrading",  "upgrade"),
    "remove":  ("Removing",   "remove"),
    "purge":   ("Purging",    "purge"),
    "autoremove": ("Auto-removing", "autoremove"),
    "reinstall":  ("Reinstalling",  "reinstall"),
    "-S":  ("Installing", "install"),   # pacman
    "-R":  ("Removing",   "remove"),    # pacman
    "-U":  ("Upgrading",  "upgrade"),   # pacman
    "-Syu":("Upgrading",  "upgrade"),   # pacman full upgrade
    "-Su": ("Upgrading",  "upgrade"),
    "-Sy": ("Syncing",    "sync"),
}

_PACKAGE_MANAGERS = {"apt", "apt-get", "dnf", "yum", "pacman", "zypper", "pip", "pip3", "flatpak", "snap"}


def parse_pkg_action(cmd: str) -> tuple[str, str, list[str]] | None:
    """
    Parse a command and return (action_label, verb, packages) if it's a
    package manager install/update/remove. Returns None if no match.
    """
    parts = cmd.strip().split()
    # Strip leading 'sudo'
    if parts and parts[0] == "sudo":
        parts = parts[1:]
    if len(parts) < 2:
        return None

    pm = parts[0]
    if pm not in _PACKAGE_MANAGERS:
        return None

    # Find the action token (first non-flag arg after the pm)
    for token in parts[1:]:
        if token in _PM_ACTIONS:
            label, verb = _PM_ACTIONS[token]
            # Packages are everything after the action token that doesn't start with -
            idx = parts.index(token)
            packages = [p for p in parts[idx + 1:] if not p.startswith("-")]
            return label, verb, packages

    return None


def _inject_yes_flag(cmd: str) -> str:
    """
    Inject the appropriate auto-yes flag for the detected package manager
    so it doesn't prompt again after the user already confirmed in Terminal +.
    """
    parts = cmd.strip().split()
    base = parts[0] if parts else ""
    sudo_prefix = ""

    if base == "sudo" and len(parts) > 1:
        sudo_prefix = "sudo "
        parts = parts[1:]
        base = parts[0]

    yes_flags = {
        "apt":     "-y",
        "apt-get": "-y",
        "dnf":     "-y",
        "yum":     "-y",
        "zypper":  "-y",
        "pacman":  "--noconfirm",
        "flatpak": "-y",
        "snap":    "",   # snap doesn't have a -y flag
        "pip":     "",
        "pip3":    "",
    }

    flag = yes_flags.get(base, "")
    if not flag or flag in cmd:
        return sudo_prefix + " ".join(parts)

    # Insert flag right after the action verb
    for i, token in enumerate(parts[1:], start=1):
        if token in _PM_ACTIONS:
            parts.insert(i + 1, flag)
            break

    return sudo_prefix + " ".join(parts)


class ConfirmDialog(QDialog):
    """Pre-run confirmation dialog for package installs, updates, and removals."""

    def __init__(self, action_label: str, packages: list[str], cmd: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Confirm action")
        self.setFixedWidth(440)
        self.setModal(True)
        self._build_ui(action_label, packages, cmd)
        self._apply_styles()

    def _build_ui(self, action_label: str, packages: list[str], cmd: str) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel(f"{action_label} packages")
        title.setObjectName("confirm-title")
        layout.addWidget(title)

        if packages:
            pkg_text = ",  ".join(packages) if len(packages) <= 6 else ", ".join(packages[:6]) + f"  + {len(packages) - 6} more"
            pkg_label = QLabel(pkg_text)
            pkg_label.setObjectName("confirm-pkgs")
            pkg_label.setWordWrap(True)
            layout.addWidget(pkg_label)
        else:
            all_label = QLabel("All pending packages")
            all_label.setObjectName("confirm-pkgs")
            layout.addWidget(all_label)

        cmd_label = QLabel(f"Command:  {cmd}")
        cmd_label.setObjectName("confirm-cmd")
        cmd_label.setWordWrap(True)
        layout.addWidget(cmd_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Yes, proceed")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QDialog {{ background: #1a1a1a; }}
            QLabel {{ color: {TEXT}; font-size: 13px; background: transparent; }}
            QLabel#confirm-title {{
                color: {AMBER};
                font-size: 14px;
                font-weight: bold;
            }}
            QLabel#confirm-pkgs {{
                color: {TEXT};
                font-size: 13px;
                background: #0d0d0d;
                border: 1px solid {BORDER};
                border-radius: 4px;
                padding: 6px 8px;
            }}
            QLabel#confirm-cmd {{
                color: {MUTED};
                font-size: 12px;
            }}
            QPushButton {{
                padding: 6px 16px;
                border-radius: 4px;
                font-size: 13px;
            }}
            QPushButton[text="Yes, proceed"] {{
                background: {GREEN_BTN};
                color: {GREEN_TXT};
                border: none;
            }}
            QPushButton[text="Cancel"] {{
                background: {BORDER};
                color: {MUTED};
                border: none;
            }}
        """)

class TerminalPane(QWidget):
    """A single terminal tab: output area + prompt bar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = TerminalSession()
        self._worker: CommandWorker | None = None
        self._build_ui()
        self._apply_styles()
        self._print_welcome()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Output area
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont("Monospace", 12))
        self.output.setObjectName("output")
        layout.addWidget(self.output)

        # Prompt bar
        prompt_bar = QWidget()
        prompt_bar.setObjectName("prompt-bar")
        prompt_layout = QHBoxLayout(prompt_bar)
        prompt_layout.setContentsMargins(10, 4, 10, 4)
        prompt_layout.setSpacing(6)

        self.cwd_label = QLabel(self._format_cwd())
        self.cwd_label.setObjectName("cwd-label")
        prompt_layout.addWidget(self.cwd_label)

        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText("Enter command…")
        self.cmd_input.setObjectName("cmd-input")
        self.cmd_input.returnPressed.connect(self._on_submit)
        self.cmd_input.textChanged.connect(self._on_text_changed)
        prompt_layout.addWidget(self.cmd_input)

        layout.addWidget(prompt_bar)

        # Inline error bar (hidden by default)
        self.error_bar = QLabel("")
        self.error_bar.setObjectName("error-bar")
        self.error_bar.setWordWrap(True)
        self.error_bar.hide()
        layout.addWidget(self.error_bar)

        # Y/N confirmation bar (hidden by default)
        self.yn_bar = QWidget()
        self.yn_bar.setObjectName("yn-bar")
        yn_layout = QHBoxLayout(self.yn_bar)
        yn_layout.setContentsMargins(12, 6, 12, 6)
        yn_layout.setSpacing(8)

        self.yn_label = QLabel("")
        self.yn_label.setObjectName("yn-label")
        self.yn_label.setWordWrap(True)
        yn_layout.addWidget(self.yn_label, stretch=1)

        self.yn_yes_btn = QPushButton("Yes")
        self.yn_yes_btn.setObjectName("yn-yes")
        self.yn_yes_btn.setFixedWidth(64)
        self.yn_yes_btn.clicked.connect(lambda: self._send_yn("y"))
        yn_layout.addWidget(self.yn_yes_btn)

        self.yn_no_btn = QPushButton("No")
        self.yn_no_btn.setObjectName("yn-no")
        self.yn_no_btn.setFixedWidth(64)
        self.yn_no_btn.clicked.connect(lambda: self._send_yn("n"))
        yn_layout.addWidget(self.yn_no_btn)

        self.yn_bar.hide()
        layout.addWidget(self.yn_bar)

    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QTextEdit#output {{
                background: {BG_MAIN};
                color: {TEXT};
                border: none;
                padding: 8px 12px;
            }}
            QWidget#prompt-bar {{
                background: {BG_BAR};
                border-top: 1px solid {BORDER};
            }}
            QLabel#cwd-label {{
                color: {TEAL};
                font-family: Monospace;
                font-size: 13px;
                font-weight: bold;
                background: transparent;
            }}
            QLineEdit#cmd-input {{
                background: transparent;
                border: none;
                color: {TEXT};
                font-family: Monospace;
                font-size: 13px;
                padding: 4px 0;
            }}
            QLabel#error-bar {{
                font-size: 12px;
                padding: 4px 12px;
                border-radius: 0;
            }}
            QLabel#error-bar[severity="danger"] {{
                background: #2a0a0a;
                color: {RED_ERR};
                border-top: 1px solid #5a1a1a;
            }}
            QLabel#error-bar[severity="warning"] {{
                background: #1e1800;
                color: {AMBER};
                border-top: 1px solid #3a3000;
            }}
            QLabel#error-bar[severity="info"] {{
                background: #0a1a1a;
                color: {TEAL};
                border-top: 1px solid #1a3a3a;
            }}
            QWidget#yn-bar {{
                background: #111a11;
                border-top: 1px solid #1a3a1a;
            }}
            QLabel#yn-label {{
                color: {TEXT};
                font-size: 12px;
                background: transparent;
            }}
            QPushButton#yn-yes {{
                background: {GREEN_BTN};
                color: {GREEN_TXT};
                border: none;
                border-radius: 4px;
                padding: 5px 0;
                font-size: 12px;
            }}
            QPushButton#yn-yes:hover {{
                background: #1D9E75;
            }}
            QPushButton#yn-no {{
                background: transparent;
                color: {RED_ERR};
                border: 1px solid {RED_ERR};
                border-radius: 4px;
                padding: 5px 0;
                font-size: 12px;
            }}
            QPushButton#yn-no:hover {{
                background: #2a1010;
            }}
        """)

    def _format_cwd(self) -> str:
        home = os.path.expanduser("~")
        return self.session.cwd.replace(home, "~") + " ❯"

    def _print_welcome(self) -> None:
        self._append_html(
            f'<span style="color:{TEAL};font-weight:bold;">Terminal +</span>'
            f' <span style="color:{MUTED};">— ready. Type a command below.</span>'
        )

    def _append_html(self, html: str) -> None:
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        self.output.insertHtml(html + "<br>")
        self.output.moveCursor(QTextCursor.MoveOperation.End)

    def _append_text(self, text: str) -> None:
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        self.output.insertPlainText(text + "\n")
        self.output.moveCursor(QTextCursor.MoveOperation.End)

    # ── Command handling ───────────────────────────────────────────────────────

    def _on_text_changed(self, text: str) -> None:
        """Run the error checker live as the user types."""
        if not text.strip():
            self.error_bar.hide()
            return

        results = check(text)
        if not results:
            self.error_bar.hide()
            return

        top = results[0]

        if top.severity == Severity.DANGER:
            sev_label = "danger"
            icon = "✕"
        elif top.severity == Severity.WARNING:
            sev_label = "warning"
            icon = "⚠"
        else:
            sev_label = "info"
            icon = "ℹ"

        msg = f"{icon}  {top.message}"
        if top.suggestion:
            msg += f"  —  {top.suggestion}"

        self.error_bar.setText(msg)
        self.error_bar.setProperty("severity", sev_label)
        # Force Qt to re-apply the dynamic property stylesheet
        self.error_bar.style().unpolish(self.error_bar)
        self.error_bar.style().polish(self.error_bar)
        self.error_bar.show()

    def _on_submit(self) -> None:
        raw = self.cmd_input.text().strip()
        if not raw:
            return
        self.cmd_input.clear()
        self.error_bar.hide()

        # History
        if not self.session.history or self.session.history[-1] != raw:
            self.session.history.append(raw)
        self.session.history_index = len(self.session.history)

        # Echo command
        home = os.path.expanduser("~")
        display_cwd = self.session.cwd.replace(home, "~")
        self._append_html(
            f'<span style="color:{TEAL};">{display_cwd}</span>'
            f' <span style="color:{TEXT};">❯ {raw}</span>'
        )

        # Built-ins
        if self._handle_builtin(raw):
            return

        # Pre-run confirmation for package installs/updates/removes
        result = parse_pkg_action(raw)
        if result:
            action_label, verb, packages = result
            dialog = ConfirmDialog(action_label, packages, raw, parent=self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self._append_html(f'<span style="color:{MUTED};">Cancelled.</span>')
                return
            # Auto-inject -y so the package manager doesn't prompt again mid-run
            raw = _inject_yes_flag(raw)

        # Sudo intercept
        if raw.strip().split()[0] == "sudo":
            self._handle_sudo(raw)
            return

        self._run(raw)

    def _handle_builtin(self, cmd: str) -> bool:
        parts = cmd.split()
        if not parts:
            return False

        if parts[0] == "cd":
            target = parts[1] if len(parts) > 1 else os.path.expanduser("~")
            try:
                new_path = os.path.realpath(
                    os.path.join(self.session.cwd, target)
                    if not os.path.isabs(target) else target
                )
                os.chdir(new_path)
                self.session.cwd = new_path
                self.cwd_label.setText(self._format_cwd())
            except FileNotFoundError:
                self._append_html(f'<span style="color:{RED_ERR};">cd: {target}: No such file or directory</span>')
            except PermissionError:
                self._append_html(f'<span style="color:{RED_ERR};">cd: {target}: Permission denied</span>')
            return True

        if parts[0] == "clear":
            self.output.clear()
            return True

        return False

    def _handle_sudo(self, cmd: str) -> None:
        dialog = SudoDialog(cmd, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._append_html(f'<span style="color:{MUTED};">sudo cancelled.</span>')
            return

        password = dialog.get_password()
        if not password:
            self._append_html(f'<span style="color:{MUTED};">sudo cancelled — no password entered.</span>')
            return

        parts = cmd.strip().split(None, 1)
        rest = parts[1] if len(parts) > 1 else ""
        escaped = password.replace("'", "'\\''")
        sudo_cmd = f"echo '{escaped}' | sudo -S {rest}"
        self._run(sudo_cmd)

    def _run(self, cmd: str) -> None:
        self.cmd_input.setEnabled(False)

        self._worker = CommandWorker(cmd, self.session.cwd)
        self._worker.output_ready.connect(self._append_text)
        self._worker.yn_prompt.connect(self._on_yn_prompt)
        self._worker.command_done.connect(self._on_command_done)
        self._worker.start()

    def _on_yn_prompt(self, question: str) -> None:
        """Show the Y/N bar when the running process asks a question."""
        self._append_html(
            f'<span style="color:{AMBER};">❯ {question}</span>'
        )
        self.yn_label.setText(question)
        self.yn_bar.show()
        self.cmd_input.setEnabled(False)

    def _send_yn(self, answer: str) -> None:
        """Send Y or N into the running process and hide the bar."""
        self.yn_bar.hide()
        self._append_html(
            f'<span style="color:{TEAL};">{"Yes" if answer == "y" else "No"}</span>'
        )
        if self._worker:
            self._worker.send_input(answer)

    def _on_command_done(self, exit_code: int) -> None:
        if exit_code != 0:
            logger.debug("Command exited with code %d", exit_code)
        self.yn_bar.hide()
        self._append_text("")
        self.cmd_input.setEnabled(True)
        self.cmd_input.setFocus()
        self.cwd_label.setText(self._format_cwd())
        if self._worker:
            self._worker._waiting_for_yn = False

    def interrupt(self) -> None:
        if self._worker:
            self._worker.terminate_process()

    def keyPressEvent(self, event) -> None:
        """Handle up/down arrow for history navigation."""
        key = event.key()
        if not self.session.history:
            super().keyPressEvent(event)
            return

        if key == Qt.Key.Key_Up:
            self.session.history_index = max(0, self.session.history_index - 1)
            self.cmd_input.setText(self.session.history[self.session.history_index])
        elif key == Qt.Key.Key_Down:
            self.session.history_index = min(
                len(self.session.history), self.session.history_index + 1
            )
            text = (
                self.session.history[self.session.history_index]
                if self.session.history_index < len(self.session.history)
                else ""
            )
            self.cmd_input.setText(text)
        else:
            super().keyPressEvent(event)


# ── Main window ────────────────────────────────────────────────────────────────

class TerminalPlusWindow(QMainWindow):
    """The main Terminal + window."""

    def __init__(self, distro_name: str, distro_version: str, ram_gb: int) -> None:
        super().__init__()
        self.distro_name = distro_name
        self.distro_version = distro_version
        self.ram_gb = ram_gb

        self.setWindowTitle("Terminal +")
        self.resize(900, 620)
        self._build_ui()
        self._apply_styles()
        self._setup_shortcuts()
        self._add_tab()

        logger.info(
            "Terminal + started — %s %s · %dGB RAM",
            distro_name, distro_version, ram_gb,
        )

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header bar
        header = QWidget()
        header.setObjectName("header-bar")
        header.setFixedHeight(30)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 12, 0)

        title_label = QLabel("Terminal +")
        title_label.setObjectName("header-title")
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        info_label = QLabel(
            f"{self.distro_name} {self.distro_version}  ·  {self.ram_gb}GB RAM"
        )
        info_label.setObjectName("header-info")
        header_layout.addWidget(info_label)

        # Store launcher button
        store_btn = QPushButton("Package Store")
        store_btn.setObjectName("store-btn")
        store_btn.clicked.connect(self._open_store)
        header_layout.addWidget(store_btn)

        # Termy toggle button
        termy_btn = QPushButton("Termy")
        termy_btn.setObjectName("termy-btn")
        termy_btn.clicked.connect(self._toggle_termy)
        header_layout.addWidget(termy_btn)

        layout.addWidget(header)

        # Tab widget
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setObjectName("tab-widget")
        self.tabs.tabCloseRequested.connect(self._close_tab)

        new_tab_btn = QPushButton("+")
        new_tab_btn.setObjectName("new-tab-btn")
        new_tab_btn.setFixedSize(28, 24)
        new_tab_btn.clicked.connect(self._add_tab)
        self.tabs.setCornerWidget(new_tab_btn)

        layout.addWidget(self.tabs)

        # Termy bottom drawer
        self._termy_drawer = TermyDrawer()
        self._termy_drawer.run_command_requested.connect(self._run_termy_command)
        layout.addWidget(self._termy_drawer)

        # Footer shortcuts hint
        footer = QWidget()
        footer.setObjectName("footer-bar")
        footer.setFixedHeight(22)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(12, 0, 12, 0)
        footer_layout.setSpacing(20)
        for hint in ["Ctrl+T  New tab", "Ctrl+W  Close tab", "Ctrl+L  Clear", "Ctrl+C  Interrupt", "Ctrl+K  Termy"]:
            lbl = QLabel(hint)
            lbl.setObjectName("footer-hint")
            footer_layout.addWidget(lbl)
        footer_layout.addStretch()
        layout.addWidget(footer)

    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QMainWindow {{
                background: {BG_MAIN};
            }}
            QWidget#central-widget, QWidget#tab-container {{
                background: {BG_MAIN};
            }}
            QLabel {{
                background: transparent;
            }}
            QWidget#header-bar {{
                background: {BG_HEADER};
                border-bottom: 1px solid {BORDER};
            }}
            QLabel#header-title {{
                color: {TEAL};
                font-size: 13px;
                font-weight: bold;
                background: transparent;
            }}
            QLabel#header-info {{
                color: {MUTED};
                font-size: 12px;
                background: transparent;
            }}
            QTabWidget#tab-widget::pane {{
                border: none;
                background: {BG_MAIN};
            }}
            QTabBar::tab {{
                background: {BG_HEADER};
                color: {MUTED};
                padding: 5px 16px;
                border: none;
                border-right: 1px solid {BORDER};
                font-size: 12px;
                min-width: 80px;
            }}
            QTabBar::tab:selected {{
                background: {BG_MAIN};
                color: {TEXT};
                border-bottom: 2px solid {TEAL};
            }}
            QTabBar::tab:hover {{
                color: {TEXT};
            }}
            QPushButton#new-tab-btn {{
                background: {BG_HEADER};
                color: {TEAL};
                border: none;
                font-size: 16px;
                font-weight: bold;
            }}
            QPushButton#new-tab-btn:hover {{
                background: {BORDER};
            }}
            QWidget#footer-bar {{
                background: {BG_HEADER};
                border-top: 1px solid {BORDER};
            }}
            QLabel#footer-hint {{
                color: #444;
                font-size: 11px;
                font-family: Monospace;
            }}
            QPushButton#store-btn {{
                background: transparent;
                color: {TEAL};
                border: 1px solid {TEAL};
                border-radius: 4px;
                padding: 3px 10px;
                font-size: 12px;
            }}
            QPushButton#store-btn:hover {{
                background: {GREEN_BTN};
                color: {GREEN_TXT};
            }}
            QPushButton#termy-btn {{
                background: transparent;
                color: {AMBER};
                border: 1px solid {AMBER};
                border-radius: 4px;
                padding: 3px 10px;
                font-size: 12px;
            }}
            QPushButton#termy-btn:hover {{
                background: #2a1e00;
                color: {AMBER};
            }}
        """)

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+T"), self).activated.connect(self._add_tab)
        QShortcut(QKeySequence("Ctrl+W"), self).activated.connect(
            lambda: self._close_tab(self.tabs.currentIndex())
        )
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(self._clear_current)
        QShortcut(QKeySequence("Ctrl+C"), self).activated.connect(self._interrupt_current)
        QShortcut(QKeySequence("Ctrl+K"), self).activated.connect(self._toggle_termy)

    # ── Tab management ─────────────────────────────────────────────────────────

    def _toggle_termy(self) -> None:
        self._termy_drawer.toggle()

    def _run_termy_command(self, cmd: str) -> None:
        """Send a command suggested by Termy into the active terminal pane."""
        pane = self._current_pane()
        if pane:
            pane.cmd_input.setText(cmd)
            pane.cmd_input.setFocus()
            # Collapse Termy drawer so the terminal is visible
            self._termy_drawer.collapse()

    def _add_tab(self) -> None:
        pane = TerminalPane()
        index = self.tabs.addTab(pane, f"Terminal {self.tabs.count() + 1}")
        self.tabs.setCurrentIndex(index)
        pane.cmd_input.setFocus()
        logger.info("Opened new tab %d", index)

    def _open_store(self) -> None:
        """Open the package store in a new tab, or focus it if already open."""
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == "Package Store":
                self.tabs.setCurrentIndex(i)
                return

        pm = detect_package_manager()
        if not pm:
            QMessageBox.warning(
                self,
                "No package manager found",
                "Terminal + could not detect a supported package manager.\n"
                "Supported: apt, dnf, pacman, zypper.",
            )
            return

        store = StorPane(pm)
        store.sudo_password_needed.connect(self._handle_store_sudo)
        index = self.tabs.addTab(store, "Package Store")
        self.tabs.setCurrentIndex(index)
        logger.info("Opened package store tab using %s", pm)

    def _handle_store_sudo(self, pkg_name: str, callback: object) -> None:
        """Show sudo dialog for store install/remove actions."""
        dialog = SudoDialog(f"sudo action for {pkg_name}", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        password = dialog.get_password()
        if password and callable(callback):
            callback(pkg_name, password)

    def _close_tab(self, index: int) -> None:
        if self.tabs.count() <= 1:
            return
        self.tabs.removeTab(index)
        logger.info("Closed tab %d", index)

    def _current_pane(self) -> TerminalPane | None:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, TerminalPane) else None

    def _clear_current(self) -> None:
        pane = self._current_pane()
        if pane:
            pane.output.clear()

    def _interrupt_current(self) -> None:
        pane = self._current_pane()
        if pane:
            pane.interrupt()


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    distro_name, distro_version, ram_gb = detect_system_info()

    app = QApplication([])
    app.setApplicationName("Terminal +")
    app.setApplicationVersion("1.0.0")

    window = TerminalPlusWindow(
        distro_name=distro_name,
        distro_version=distro_version,
        ram_gb=ram_gb,
    )
    window.show()
    app.exec()
