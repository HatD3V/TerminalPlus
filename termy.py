"""
Terminal + — Termy Chat Panel
A bottom drawer that expands up, talks to the locally-running
Termy Ollama model, and highlights any command Termy suggests
with a one-click copy button.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from typing import Callable

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# ── Colours ────────────────────────────────────────────────────────────────────

BG_MAIN    = "#0d0d0d"
BG_DRAWER  = "#111111"
BG_CARD    = "#141414"
BG_HEADER  = "#181818"
BORDER     = "#2a2a2a"
TEAL       = "#5DCAA5"
TEXT       = "#e0e0e0"
MUTED      = "#888888"
AMBER      = "#EF9F27"
GREEN_BTN  = "#0F6E56"
GREEN_TXT  = "#9FE1CB"
RED_ERR    = "#E24B4A"

# ── Termy availability check ───────────────────────────────────────────────────

def is_termy_ready() -> bool:
    """Return True if the termy Ollama model is installed and Ollama is running."""
    if not shutil.which("ollama"):
        return False
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "termy" in result.stdout
    except (subprocess.TimeoutExpired, OSError):
        return False


# ── Ollama worker ──────────────────────────────────────────────────────────────

class OllamaWorker(QThread):
    """Streams a response from the termy model token by token."""

    token_ready    = pyqtSignal(str)
    response_done  = pyqtSignal(str)   # full response text
    error_occurred = pyqtSignal(str)

    def __init__(self, prompt: str, history: list[dict]) -> None:
        super().__init__()
        self.prompt = prompt
        self.history = history

    def run(self) -> None:
        try:
            import json

            messages = self.history + [{"role": "user", "content": self.prompt}]

            proc = subprocess.Popen(
                ["ollama", "run", "termy", "--format", ""],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )

            # Send the full conversation as a single prompt with context
            full_prompt = self._build_prompt(messages)
            stdout, _ = proc.communicate(input=full_prompt, timeout=120)

            full_response = stdout.strip()
            self.response_done.emit(full_response)

        except subprocess.TimeoutExpired:
            logger.warning("Termy timed out")
            self.error_occurred.emit("Termy timed out. Try again.")
        except OSError as exc:
            logger.exception("Ollama error")
            self.error_occurred.emit(f"Could not reach Termy: {exc}")

    @staticmethod
    def _build_prompt(messages: list[dict]) -> str:
        """Format conversation history into a single prompt string."""
        lines = []
        for msg in messages:
            role = "User" if msg["role"] == "user" else "Termy"
            lines.append(f"{role}: {msg['content']}")
        return "\n".join(lines) + "\nTermy:"


# ── Command bubble ─────────────────────────────────────────────────────────────

COMMAND_PATTERN = re.compile(
    r"```(?:bash|sh|shell)?\n?(.*?)```|`([^`\n]+)`",
    re.DOTALL,
)


def extract_commands(text: str) -> list[str]:
    """Pull all code blocks and inline code from a response."""
    commands = []
    for match in COMMAND_PATTERN.finditer(text):
        cmd = (match.group(1) or match.group(2) or "").strip()
        if cmd:
            commands.append(cmd)
    return commands


class CommandBubble(QFrame):
    """A highlighted card showing a suggested command with a copy button."""

    run_requested = pyqtSignal(str)

    def __init__(self, command: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.command = command
        self.setObjectName("cmd-bubble")
        self._build_ui()
        self._apply_styles()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        prompt = QLabel("$")
        prompt.setObjectName("cmd-prompt")
        layout.addWidget(prompt)

        cmd_label = QLabel(self.command)
        cmd_label.setObjectName("cmd-text")
        cmd_label.setFont(QFont("Monospace", 12))
        cmd_label.setWordWrap(True)
        layout.addWidget(cmd_label, stretch=1)

        copy_btn = QPushButton("Copy")
        copy_btn.setObjectName("cmd-copy")
        copy_btn.setFixedWidth(54)
        copy_btn.clicked.connect(self._copy)
        layout.addWidget(copy_btn)

        run_btn = QPushButton("Run")
        run_btn.setObjectName("cmd-run")
        run_btn.setFixedWidth(48)
        run_btn.clicked.connect(lambda: self.run_requested.emit(self.command))
        layout.addWidget(run_btn)

    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QFrame#cmd-bubble {{
                background: #0a1a10;
                border: 1px solid #1a3a20;
                border-radius: 6px;
            }}
            QLabel {{ background: transparent; }}
            QLabel#cmd-prompt {{
                color: {TEAL};
                font-family: Monospace;
                font-size: 13px;
                font-weight: bold;
            }}
            QLabel#cmd-text {{
                color: {GREEN_TXT};
                font-size: 12px;
            }}
            QPushButton#cmd-copy {{
                background: transparent;
                color: {MUTED};
                border: 1px solid {BORDER};
                border-radius: 4px;
                font-size: 11px;
                padding: 3px 0;
            }}
            QPushButton#cmd-copy:hover {{
                color: {TEXT};
                border-color: {MUTED};
            }}
            QPushButton#cmd-run {{
                background: {GREEN_BTN};
                color: {GREEN_TXT};
                border: none;
                border-radius: 4px;
                font-size: 11px;
                padding: 3px 0;
            }}
            QPushButton#cmd-run:hover {{
                background: #1D9E75;
            }}
        """)

    def _copy(self) -> None:
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.command)


# ── Chat message widget ────────────────────────────────────────────────────────

class ChatMessage(QFrame):
    """A single message bubble — user or Termy."""

    run_requested = pyqtSignal(str)

    def __init__(
        self,
        text: str,
        role: str,       # "user" or "termy"
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.role = role
        self.setObjectName(f"msg-{role}")
        self._build_ui(text)
        self._apply_styles()

    def _build_ui(self, text: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # Role label
        role_label = QLabel("You" if self.role == "user" else "Termy")
        role_label.setObjectName("msg-role")
        layout.addWidget(role_label)

        # Strip code blocks from the text display, show plain text
        display_text = COMMAND_PATTERN.sub("", text).strip()
        if display_text:
            body = QLabel(display_text)
            body.setObjectName("msg-body")
            body.setWordWrap(True)
            layout.addWidget(body)

        # Command bubbles for any detected commands
        commands = extract_commands(text)
        for cmd in commands:
            bubble = CommandBubble(cmd)
            bubble.run_requested.connect(self.run_requested)
            layout.addWidget(bubble)

    def _apply_styles(self) -> None:
        is_user = self.role == "user"
        bg = "#1a1a2e" if is_user else BG_CARD
        border = "#2a2a4e" if is_user else BORDER
        role_color = AMBER if is_user else TEAL

        self.setStyleSheet(f"""
            QFrame#msg-{self.role} {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            QLabel {{ background: transparent; }}
            QLabel#msg-role {{
                color: {role_color};
                font-size: 11px;
                font-weight: bold;
            }}
            QLabel#msg-body {{
                color: {TEXT};
                font-size: 13px;
                line-height: 1.5;
            }}
        """)


# ── Specs update worker ────────────────────────────────────────────────────────

class SpecsUpdateWorker(QThread):
    """
    Re-detects distro + RAM, rewrites the Modelfile at
    /opt/terminal-plus/Modelfile, and runs ollama create termy.
    """

    progress = pyqtSignal(str)
    done     = pyqtSignal(bool, str)   # success, message

    MODELFILE_PATH = "/opt/terminal-plus/Modelfile"

    def run(self) -> None:
        try:
            distro_name    = self._detect_distro_name()
            distro_version = self._detect_distro_version()
            ram_gb         = self._detect_ram_gb()
            base_model     = self._current_base_model()

            self.progress.emit(
                f"Detected: {distro_name} {distro_version} · {ram_gb}GB RAM"
            )

            self._write_modelfile(distro_name, distro_version, ram_gb, base_model)
            self.progress.emit("Modelfile updated.")

            self.progress.emit("Rebuilding Termy model (this may take a minute)…")
            result = subprocess.run(
                ["ollama", "create", "termy", "-f", self.MODELFILE_PATH],
                capture_output=True,
                text=True,
                timeout=300,
            )

            if result.returncode == 0:
                self.done.emit(
                    True,
                    f"Termy updated! Now knows about "
                    f"{distro_name} {distro_version} · {ram_gb}GB RAM.",
                )
            else:
                self.done.emit(False, f"Rebuild failed: {result.stderr.strip()}")

        except subprocess.TimeoutExpired:
            self.done.emit(False, "Timed out rebuilding Termy. Try running build_termy.sh manually.")
        except OSError as exc:
            logger.exception("Specs update failed")
            self.done.emit(False, f"Error: {exc}")

    @staticmethod
    def _detect_distro_name() -> str:
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("NAME="):
                        return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
        return "Linux"

    @staticmethod
    def _detect_distro_version() -> str:
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("VERSION_ID="):
                        return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
        return ""

    @staticmethod
    def _detect_ram_gb() -> int:
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return round(kb / 1024 / 1024)
        except OSError:
            pass
        return 0

    @staticmethod
    def _current_base_model() -> str:
        """Read the FROM line out of the existing Modelfile, or pick a sensible default."""
        try:
            with open(SpecsUpdateWorker.MODELFILE_PATH) as f:
                for line in f:
                    if line.strip().upper().startswith("FROM "):
                        return line.strip().split(None, 1)[1].strip()
        except OSError:
            pass
        return "llama3.1:8b"

    def _write_modelfile(
        self,
        distro_name: str,
        distro_version: str,
        ram_gb: int,
        base_model: str,
    ) -> None:
        content = f"""FROM {base_model}

SYSTEM \"\"\"
{distro_name} {distro_version} {ram_gb}GB

You are Termy, a professional Linux terminal assistant built into Terminal +. You are running on the system described in the first line — always use the distro name, version, and RAM amount to tailor your responses.

Your behavior:
- Introduce yourself as Termy when greeted.
- Always be professional in tone — clear, precise, and respectful.
- For simple commands, assist directly. For complex commands, briefly explain what the command does before suggesting it.
- When a user types a dangerous command (such as rm -rf / or anything destructive), always ask for confirmation before proceeding.
- Always suggest better or alternative commands when they exist, even if the user did not ask.
- Remember everything said during the session and use that context to give smarter, more relevant help.
- Always factor in the system's RAM when recommending tools, packages, or configurations. Never suggest something that would be unreasonable for the available memory.
- If a beginner-level user is detected (unfamiliar commands, basic questions), proactively share relevant Linux tips as you assist them.
- If you encounter an error or question you cannot resolve, honestly admit it and suggest the user search for more information (man pages, forums, official docs).
- If asked anything unrelated to Linux or terminal usage, politely refuse and redirect the user back to terminal-related topics.

You only assist with Linux. You are always aware of the user's exact system environment.
\"\"\"

PARAMETER temperature 0.7
PARAMETER top_p 0.9
PARAMETER num_ctx 4096
"""
        with open(self.MODELFILE_PATH, "w") as f:
            f.write(content)


# ── Termy drawer ───────────────────────────────────────────────────────────────

class TermyDrawer(QWidget):
    """
    Bottom drawer that expands upward to show the Termy chat.
    Collapses to a slim header bar when closed.
    """

    run_command_requested = pyqtSignal(str)

    COLLAPSED_H = 36
    EXPANDED_H  = 340

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._expanded = False
        self._history: list[dict] = []
        self._worker: OllamaWorker | None = None
        self._termy_ready = is_termy_ready()
        self._build_ui()
        self._apply_styles()
        self.setFixedHeight(self.COLLAPSED_H)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header bar (always visible) ──
        header = QWidget()
        header.setObjectName("termy-header")
        header.setFixedHeight(self.COLLAPSED_H)
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 12, 0)
        header_layout.setSpacing(8)

        self._toggle_icon = QLabel("▲")
        self._toggle_icon.setObjectName("termy-arrow")
        header_layout.addWidget(self._toggle_icon)

        title = QLabel("Termy")
        title.setObjectName("termy-title")
        header_layout.addWidget(title)

        if not self._termy_ready:
            status = QLabel("not installed — run build_termy.sh first")
            status.setObjectName("termy-status-warn")
            header_layout.addWidget(status)
        else:
            status = QLabel("ready")
            status.setObjectName("termy-status-ok")
            header_layout.addWidget(status)

        header_layout.addStretch()

        update_btn = QPushButton("Update Specs")
        update_btn.setObjectName("termy-update")
        update_btn.setFixedWidth(100)
        update_btn.setToolTip("Re-train Termy with your current hardware specs")
        update_btn.clicked.connect(self._update_specs)
        header_layout.addWidget(update_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("termy-clear")
        clear_btn.setFixedWidth(48)
        clear_btn.clicked.connect(self._clear_chat)
        header_layout.addWidget(clear_btn)

        header.mousePressEvent = lambda _: self.toggle()
        layout.addWidget(header)

        # ── Expandable body ──
        self._body = QWidget()
        self._body.setObjectName("termy-body")
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        # Scroll area for messages
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("termy-scroll")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._msg_container = QWidget()
        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.setContentsMargins(10, 10, 10, 10)
        self._msg_layout.setSpacing(8)
        self._msg_layout.addStretch()

        scroll.setWidget(self._msg_container)
        body_layout.addWidget(scroll)
        self._scroll = scroll

        # Input row
        input_row = QWidget()
        input_row.setObjectName("termy-input-row")
        input_row.setFixedHeight(44)
        input_layout = QHBoxLayout(input_row)
        input_layout.setContentsMargins(10, 6, 10, 6)
        input_layout.setSpacing(8)

        self._input = QLineEdit()
        self._input.setObjectName("termy-input")
        self._input.setPlaceholderText("Ask Termy anything about Linux…")
        self._input.returnPressed.connect(self._send)
        self._input.setEnabled(self._termy_ready)
        input_layout.addWidget(self._input)

        self._send_btn = QPushButton("Ask")
        self._send_btn.setObjectName("termy-send")
        self._send_btn.setFixedWidth(52)
        self._send_btn.clicked.connect(self._send)
        self._send_btn.setEnabled(self._termy_ready)
        input_layout.addWidget(self._send_btn)

        body_layout.addWidget(input_row)
        self._body.hide()
        layout.addWidget(self._body)

    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QWidget {{
                background: {BG_DRAWER};
            }}
            QLabel {{ background: transparent; }}
            QWidget#termy-header {{
                background: {BG_HEADER};
                border-top: 1px solid {BORDER};
            }}
            QLabel#termy-arrow {{
                color: {TEAL};
                font-size: 10px;
            }}
            QLabel#termy-title {{
                color: {TEAL};
                font-size: 13px;
                font-weight: bold;
            }}
            QLabel#termy-status-ok {{
                color: {MUTED};
                font-size: 11px;
            }}
            QLabel#termy-status-warn {{
                color: {AMBER};
                font-size: 11px;
            }}
            QPushButton#termy-clear {{
                background: transparent;
                color: {MUTED};
                border: 1px solid {BORDER};
                border-radius: 4px;
                font-size: 11px;
                padding: 2px 0;
            }}
            QPushButton#termy-clear:hover {{
                color: {TEXT};
            }}
            QPushButton#termy-update {{
                background: transparent;
                color: {TEAL};
                border: 1px solid {TEAL};
                border-radius: 4px;
                font-size: 11px;
                padding: 2px 0;
            }}
            QPushButton#termy-update:hover {{
                background: {GREEN_BTN};
                color: {GREEN_TXT};
            }}
            QPushButton#termy-update:disabled {{
                color: {MUTED};
                border-color: {BORDER};
            }}
            QScrollArea#termy-scroll {{
                border: none;
                background: {BG_MAIN};
            }}
            QScrollBar:vertical {{
                background: {BG_MAIN};
                width: 6px;
            }}
            QScrollBar::handle:vertical {{
                background: {BORDER};
                border-radius: 3px;
            }}
            QWidget#termy-input-row {{
                background: {BG_HEADER};
                border-top: 1px solid {BORDER};
            }}
            QLineEdit#termy-input {{
                background: {BG_MAIN};
                border: 1px solid {BORDER};
                border-radius: 4px;
                color: {TEXT};
                font-size: 13px;
                padding: 4px 8px;
            }}
            QLineEdit#termy-input:focus {{
                border-color: {TEAL};
            }}
            QPushButton#termy-send {{
                background: {GREEN_BTN};
                color: {GREEN_TXT};
                border: none;
                border-radius: 4px;
                font-size: 12px;
                padding: 4px 0;
            }}
            QPushButton#termy-send:hover {{
                background: #1D9E75;
            }}
            QPushButton#termy-send:disabled {{
                background: {BORDER};
                color: {MUTED};
            }}
        """)

    # ── Toggle ─────────────────────────────────────────────────────────────────

    def toggle(self) -> None:
        self._expanded = not self._expanded
        if self._expanded:
            self._body.show()
            self.setFixedHeight(self.EXPANDED_H)
            self._toggle_icon.setText("▼")
            self._input.setFocus()
        else:
            self._body.hide()
            self.setFixedHeight(self.COLLAPSED_H)
            self._toggle_icon.setText("▲")

    def expand(self) -> None:
        if not self._expanded:
            self.toggle()

    def collapse(self) -> None:
        if self._expanded:
            self.toggle()

    # ── Chat ───────────────────────────────────────────────────────────────────

    def _send(self) -> None:
        if not self._termy_ready:
            return

        text = self._input.text().strip()
        if not text:
            return

        self._input.clear()
        self._input.setEnabled(False)
        self._send_btn.setEnabled(False)

        self._add_message(text, "user")
        self._history.append({"role": "user", "content": text})

        # Show thinking indicator
        self._thinking = self._add_thinking()

        self._worker = OllamaWorker(text, self._history[:-1])
        self._worker.response_done.connect(self._on_response)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    def _on_response(self, response: str) -> None:
        self._remove_thinking()
        self._add_message(response, "termy")
        self._history.append({"role": "assistant", "content": response})
        self._input.setEnabled(True)
        self._send_btn.setEnabled(True)
        self._input.setFocus()
        self._scroll_to_bottom()

    def _on_error(self, error: str) -> None:
        self._remove_thinking()
        self._add_error(error)
        self._input.setEnabled(True)
        self._send_btn.setEnabled(True)

    def _add_message(self, text: str, role: str) -> None:
        msg = ChatMessage(text, role)
        msg.run_requested.connect(self.run_command_requested)
        self._msg_layout.insertWidget(self._msg_layout.count() - 1, msg)
        self._scroll_to_bottom()

    def _add_thinking(self) -> QLabel:
        lbl = QLabel("Termy is thinking…")
        lbl.setObjectName("termy-thinking")
        lbl.setStyleSheet(f"color: {MUTED}; font-size: 12px; padding: 8px 10px; background: transparent;")
        self._msg_layout.insertWidget(self._msg_layout.count() - 1, lbl)
        self._scroll_to_bottom()
        return lbl

    def _remove_thinking(self) -> None:
        if hasattr(self, "_thinking") and self._thinking:
            self._thinking.deleteLater()
            self._thinking = None

    def _add_error(self, error: str) -> None:
        lbl = QLabel(f"Error: {error}")
        lbl.setStyleSheet(f"color: {RED_ERR}; font-size: 12px; padding: 8px 10px; background: transparent;")
        lbl.setWordWrap(True)
        self._msg_layout.insertWidget(self._msg_layout.count() - 1, lbl)
        self._scroll_to_bottom()

    def _clear_chat(self) -> None:
        self._history.clear()
        for i in reversed(range(self._msg_layout.count() - 1)):
            item = self._msg_layout.itemAt(i)
            if item and item.widget():
                item.widget().deleteLater()

    def _update_specs(self) -> None:
        """Re-detect hardware specs, rewrite the Modelfile, and rebuild Termy."""
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            "Update Termy specs",
            "This will re-train Termy with your current hardware specs.\n\n"
            "It may take a few minutes. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Disable button and show progress
        self._find_update_btn().setEnabled(False)
        self._add_system_msg("Updating Termy with your current specs…")

        self._specs_worker = SpecsUpdateWorker()
        self._specs_worker.progress.connect(self._add_system_msg)
        self._specs_worker.done.connect(self._on_specs_updated)
        self._specs_worker.start()

    def _on_specs_updated(self, success: bool, message: str) -> None:
        self._add_system_msg(message)
        btn = self._find_update_btn()
        btn.setEnabled(True)
        if success:
            self._termy_ready = True
            self._input.setEnabled(True)
            self._send_btn.setEnabled(True)

    def _find_update_btn(self) -> QPushButton:
        return self.findChild(QPushButton, "termy-update")

    def _add_system_msg(self, text: str) -> None:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {TEAL}; font-size: 11px; padding: 4px 10px; "
            f"background: transparent; font-style: italic;"
        )
        lbl.setWordWrap(True)
        self._msg_layout.insertWidget(self._msg_layout.count() - 1, lbl)
        self._scroll_to_bottom()
        if not self._expanded:
            self.expand()

    def _scroll_to_bottom(self) -> None:
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
