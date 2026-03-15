"""
Terminal + Installer
Android-style GUI installer with Offline and Online modes.

Offline — installs from bundled files, no Termy AI.
Online  — downloads latest files from GitHub, installs Ollama, builds Termy.

Usage:
    sudo python3 installer.py
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# ── Constants ──────────────────────────────────────────────────────────────────

GITHUB_REPO    = "HatD3V/TerminalPlus"
GITHUB_BRANCH  = "main"
GITHUB_RAW     = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}"
GITHUB_ZIP     = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/{GITHUB_BRANCH}.zip"

INSTALL_DIR    = Path("/opt/terminal-plus")
BIN_LINK       = Path("/usr/local/bin/terminal-plus")
DESKTOP_DIR    = Path("/usr/share/applications")
ICON_DIR       = Path("/usr/share/icons/hicolor/128x128/apps")
OLLAMA_URL     = "https://ollama.ai/install.sh"
LOG_PATH       = Path.home() / "terminal_plus_install.log"

APP_FILES      = ["main.py", "store.py", "checker.py", "termy.py", "requirements.txt"]

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ── Colours ────────────────────────────────────────────────────────────────────

BG        = "#121212"
SURFACE   = "#1E1E1E"
SURFACE2  = "#2C2C2C"
PRIMARY   = "#4CAF50"
PRIMARY_D = "#388E3C"
ON_PRI    = "#FFFFFF"
TEXT_HI   = "#FFFFFF"
TEXT_MED  = "#B3FFFFFF"
TEXT_DIS  = "#61FFFFFF"
ERROR     = "#CF6679"
AMBER     = "#FFB300"
BORDER    = "#333333"
TEAL      = "#5DCAA5"
BLUE      = "#2196F3"
BLUE_D    = "#1565C0"

# ── Step dataclass ─────────────────────────────────────────────────────────────

class Step:
    def __init__(self, label: str, detail: str, fn: Callable) -> None:
        self.label  = label
        self.detail = detail
        self.fn     = fn
        self.status = "pending"

# ── Helpers ────────────────────────────────────────────────────────────────────

def _detect_distro() -> tuple[str, str]:
    name, version = "Linux", ""
    try:
        with open("/etc/os-release") as f:
            info = {}
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    info[k] = v.strip('"')
        name    = info.get("NAME", "Linux")
        version = info.get("VERSION_ID", "")
    except OSError:
        pass
    return name, version


def _detect_ram_gb() -> int:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 / 1024)
    except OSError:
        pass
    return 0


def _select_model(ram_gb: int) -> str:
    if   ram_gb >= 48: return "llama3.1:70b"
    elif ram_gb >= 16: return "llama3.1:8b"
    elif ram_gb >= 8:  return "llama3.2:3b"
    else:              return "llama3.2:1b"


def _run(cmd: str, timeout: int = 600) -> str:
    logger.debug("CMD: %s", cmd)
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    for line in result.stdout.splitlines():
        logger.debug("OUT: %s", line)
    for line in result.stderr.splitlines():
        logger.debug("ERR: %s", line)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}\n{result.stderr.strip()}")
    return result.stdout.strip()


def _script_dir() -> Path:
    """Return the directory the installer is running from."""
    if getattr(sys, "frozen", False):
        # Running as PyInstaller bundle
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).parent.resolve()


# ── Shared steps (both modes) ──────────────────────────────────────────────────

def step_check_root(w: "InstallerWorker") -> None:
    if os.geteuid() != 0:
        raise RuntimeError(
            "Installer must be run as root.\n"
            "Re-run with: sudo python3 installer.py"
        )
    logger.info("Running as root.")


def step_check_deps(w: "InstallerWorker") -> None:
    missing = [c for c in ("python3", "pip3") if not shutil.which(c)]
    if missing:
        raise RuntimeError(f"Missing required tools: {', '.join(missing)}")


def step_python_deps(w: "InstallerWorker") -> None:
    req = INSTALL_DIR / "requirements.txt"
    if not req.exists():
        raise RuntimeError("requirements.txt missing from install dir.")
    try:
        _run(f"pip3 install --quiet -r {req}")
    except RuntimeError:
        _run(f"pip3 install --quiet -r {req} --break-system-packages")


def step_create_launcher(w: "InstallerWorker") -> None:
    # If a compiled binary was installed, point directly to it
    compiled_flag = INSTALL_DIR / ".compiled"
    if compiled_flag.exists():
        BIN_LINK.write_text(
            "#!/usr/bin/env bash\nexec /opt/terminal-plus/terminal-plus \"$@\"\n"
        )
    else:
        BIN_LINK.write_text(
            "#!/usr/bin/env bash\nexec python3 /opt/terminal-plus/main.py \"$@\"\n"
        )
    BIN_LINK.chmod(0o755)
    logger.info("Launcher created at %s", BIN_LINK)


def step_desktop_entry(w: "InstallerWorker") -> None:
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    src = w.script_dir / "icon.png"
    icon_name = "terminal-plus"
    if src.exists():
        shutil.copy(src, ICON_DIR / "terminal-plus.png")
    else:
        icon_name = "utilities-terminal"
        logger.warning("No icon.png found, using system fallback.")

    (DESKTOP_DIR / "terminal-plus.desktop").write_text(
        "[Desktop Entry]\nVersion=1.0\nType=Application\n"
        "Name=Terminal +\nGenericName=Terminal Emulator\n"
        "Comment=Smart Linux terminal with AI assistant\n"
        f"Exec={BIN_LINK}\nIcon={icon_name}\nTerminal=false\n"
        "Categories=System;TerminalEmulator;\n"
        "Keywords=terminal;shell;linux;ai;termy;\n"
        "StartupNotify=true\nStartupWMClass=terminal-plus\n"
    )
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(DESKTOP_DIR)], capture_output=True)
    if shutil.which("gtk-update-icon-cache"):
        subprocess.run(["gtk-update-icon-cache", "-f", "-t", "/usr/share/icons/hicolor"], capture_output=True)


# ── Offline steps ──────────────────────────────────────────────────────────────

def step_offline_copy_files(w: "InstallerWorker") -> None:
    """
    Deploy app files from the installer bundle.
    If a compiled 'terminal-plus' binary is bundled, install that.
    Otherwise fall back to copying Python scripts.
    """
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    src_dir = w.script_dir

    # Check for compiled binary first
    compiled = src_dir / "terminal-plus"
    if compiled.exists():
        dest = INSTALL_DIR / "terminal-plus"
        shutil.copy(compiled, dest)
        dest.chmod(0o755)
        logger.info("Installed compiled binary: %s", dest)
        # Mark so the launcher uses the binary directly
        (INSTALL_DIR / ".compiled").write_text("true")
        return

    # Fallback: copy Python scripts
    copied = 0
    for fname in APP_FILES:
        src = src_dir / fname
        if src.exists():
            shutil.copy(src, INSTALL_DIR / fname)
            logger.info("Copied %s", fname)
            copied += 1
        else:
            logger.warning("%s not found in bundle", fname)
    if copied == 0:
        raise RuntimeError("No app files found in bundle. Re-build the executable.")
    (INSTALL_DIR / "main.py").chmod(0o755)


# ── Online steps ───────────────────────────────────────────────────────────────

def step_online_check_internet(w: "InstallerWorker") -> None:
    try:
        urllib.request.urlopen("https://github.com", timeout=10)
        logger.info("Internet connection OK.")
    except Exception as exc:
        raise RuntimeError(f"No internet connection: {exc}")


def step_online_download_files(w: "InstallerWorker") -> None:
    """Download latest app files directly from GitHub."""
    import urllib.request, zipfile, io

    logger.info("Downloading from %s", GITHUB_ZIP)
    with urllib.request.urlopen(GITHUB_ZIP, timeout=60) as resp:
        data = resp.read()

    INSTALL_DIR.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        # Files are inside a folder like TerminalPlus-main/
        prefix = f"TerminalPlus-{GITHUB_BRANCH}/"
        for fname in APP_FILES:
            member = f"{prefix}{fname}"
            try:
                content = zf.read(member)
                dest = INSTALL_DIR / fname
                dest.write_bytes(content)
                logger.info("Downloaded %s", fname)
            except KeyError:
                logger.warning("%s not found in repo archive", fname)

    # Also grab icon if available
    try:
        icon_member = f"{prefix}icon.png"
        icon_data = zf.read(icon_member)
        (INSTALL_DIR / "icon.png").write_bytes(icon_data)
        w.script_dir = INSTALL_DIR   # point desktop step at install dir for icon
        logger.info("Downloaded icon.png")
    except Exception:
        pass

    (INSTALL_DIR / "main.py").chmod(0o755)


def step_online_install_ollama(w: "InstallerWorker") -> None:
    if shutil.which("ollama"):
        logger.info("Ollama already installed.")
        return
    _run(f"curl -fsSL {OLLAMA_URL} | sh", timeout=300)


def step_online_start_ollama(w: "InstallerWorker") -> None:
    result = subprocess.run(["pgrep", "-x", "ollama"], capture_output=True)
    if result.returncode != 0:
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
    logger.info("Ollama running.")


def step_online_pull_model(w: "InstallerWorker") -> None:
    ram_gb = _detect_ram_gb()
    model  = _select_model(ram_gb)
    logger.info("Pulling %s (RAM: %dGB)", model, ram_gb)
    _run(f"ollama pull {model}", timeout=900)


def step_online_build_termy(w: "InstallerWorker") -> None:
    distro_name, distro_version = _detect_distro()
    ram_gb = _detect_ram_gb()
    model  = _select_model(ram_gb)

    modelfile = INSTALL_DIR / "Modelfile"
    modelfile.write_text(
        f"FROM {model}\n\n"
        "SYSTEM \"\"\"\n"
        f"{distro_name} {distro_version} {ram_gb}GB\n\n"
        "You are Termy, a professional Linux terminal assistant built into Terminal +. "
        "You are running on the system described in the first line — always use the distro name, "
        "version, and RAM amount to tailor your responses.\n\n"
        "Your behavior:\n"
        "- Introduce yourself as Termy when greeted.\n"
        "- Always be professional in tone — clear, precise, and respectful.\n"
        "- For simple commands, assist directly. For complex commands, briefly explain what the command does before suggesting it.\n"
        "- When a user types a dangerous command (such as rm -rf / or anything destructive), always ask for confirmation before proceeding.\n"
        "- Always suggest better or alternative commands when they exist, even if the user did not ask.\n"
        "- Remember everything said during the session and use that context to give smarter, more relevant help.\n"
        "- Always factor in the system's RAM when recommending tools, packages, or configurations. Never suggest something that would be unreasonable for the available memory.\n"
        "- If a beginner-level user is detected (unfamiliar commands, basic questions), proactively share relevant Linux tips as you assist them.\n"
        "- If you encounter an error or question you cannot resolve, honestly admit it and suggest the user search for more information (man pages, forums, official docs).\n"
        "- If asked anything unrelated to Linux or terminal usage, politely refuse and redirect the user back to terminal-related topics.\n\n"
        "You only assist with Linux. You are always aware of the user's exact system environment.\n"
        "\"\"\"\n\n"
        "PARAMETER temperature 0.7\n"
        "PARAMETER top_p 0.9\n"
        "PARAMETER num_ctx 4096\n"
    )
    _run(f"ollama create termy -f {modelfile}", timeout=300)


def step_online_verify_termy(w: "InstallerWorker") -> None:
    result = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
    if "termy" not in result.stdout:
        raise RuntimeError("Termy model not found after build.")
    logger.info("Termy verified.")


# ── Step lists ─────────────────────────────────────────────────────────────────

def offline_steps() -> list[Step]:
    return [
        Step("Checking dependencies",    "python3, pip3",                   step_check_deps),
        Step("Copying app files",        "Unpacking bundled files",         step_offline_copy_files),
        Step("Installing Python deps",   "PyQt6 and requirements",          step_python_deps),
        Step("Creating launcher",        "/usr/local/bin/terminal-plus",    step_create_launcher),
        Step("Installing desktop entry", "App icon and launcher shortcut",  step_desktop_entry),
    ]


def online_steps() -> list[Step]:
    return [
        Step("Checking internet",        "Connecting to GitHub",                     step_online_check_internet),
        Step("Checking dependencies",    "python3, pip3, curl",                      step_check_deps),
        Step("Downloading app files",    f"github.com/{GITHUB_REPO}",               step_online_download_files),
        Step("Installing Python deps",   "PyQt6 and requirements",                   step_python_deps),
        Step("Creating launcher",        "/usr/local/bin/terminal-plus",             step_create_launcher),
        Step("Installing desktop entry", "App icon and launcher shortcut",           step_desktop_entry),
        Step("Installing Ollama",        "Local AI runtime",                         step_online_install_ollama),
        Step("Starting Ollama service",  "Launching background service",             step_online_start_ollama),
        Step("Downloading AI model",     "Smartest model for your hardware",         step_online_pull_model),
        Step("Training Termy",           "Building your personal AI assistant",      step_online_build_termy),
        Step("Verifying Termy",          "Confirming model is ready",                step_online_verify_termy),
    ]


# ── Worker thread ──────────────────────────────────────────────────────────────

class InstallerWorker(QThread):
    step_started = pyqtSignal(int, str)
    step_done    = pyqtSignal(int)
    step_failed  = pyqtSignal(int, str)
    all_done     = pyqtSignal(bool)

    def __init__(self, steps: list[Step], script_dir: Path) -> None:
        super().__init__()
        self.steps      = steps
        self.script_dir = script_dir

    def run(self) -> None:
        success = True
        for i, step in enumerate(self.steps):
            self.step_started.emit(i, step.label)
            logger.info("Step %d/%d: %s", i + 1, len(self.steps), step.label)
            try:
                step.fn(self)
                step.status = "done"
                self.step_done.emit(i)
            except Exception as exc:
                step.status = "failed"
                logger.error("Step %d failed: %s", i + 1, exc)
                self.step_failed.emit(i, str(exc))
                success = False
                break

        logger.info("Install %s. Log: %s", "succeeded" if success else "failed", LOG_PATH)
        self.all_done.emit(success)


# ── Step row widget ────────────────────────────────────────────────────────────

class StepRow(QWidget):
    def __init__(self, step: Step) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(14)

        self._circle = QLabel("○")
        self._circle.setFixedWidth(20)
        self._circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._circle)

        col = QVBoxLayout()
        col.setSpacing(2)
        self._label  = QLabel(step.label)
        self._detail = QLabel(step.detail)
        col.addWidget(self._label)
        col.addWidget(self._detail)
        layout.addLayout(col, stretch=1)

        self._status = QLabel("")
        self._status.setFixedWidth(70)
        self._status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._status)

        self._set_state("pending")

    def set_running(self) -> None:
        self._circle.setText("◉")
        self._status.setText("Running…")
        self._set_state("running")

    def set_done(self) -> None:
        self._circle.setText("●")
        self._status.setText("Done")
        self._set_state("done")

    def set_failed(self, error: str) -> None:
        self._circle.setText("✕")
        self._status.setText("Failed")
        self._detail.setText(error[:55] + "…" if len(error) > 55 else error)
        self._set_state("failed")

    def _set_state(self, state: str) -> None:
        c = {
            "pending": (TEXT_DIS, TEXT_DIS,  TEXT_DIS),
            "running": (AMBER,    TEXT_HI,   TEXT_MED),
            "done":    (PRIMARY,  TEXT_HI,   TEXT_MED),
            "failed":  (ERROR,    ERROR,     ERROR),
        }[state]
        self.setStyleSheet(f"""
            QLabel {{ background: transparent; }}
            QLabel:first-child  {{ color: {c[0]}; font-size: 16px; }}
            QLabel:nth-child(2) {{ color: {c[1]}; font-size: 14px; font-weight: 500; }}
            QLabel:nth-child(3) {{ color: {c[2]}; font-size: 12px; }}
            QLabel:last-child   {{ color: {c[0]}; font-size: 12px; }}
        """)
        # Force label colours explicitly
        self._circle.setStyleSheet(f"color: {c[0]}; font-size: 16px; background: transparent;")
        self._label.setStyleSheet(f"color: {c[1]}; font-size: 14px; font-weight: 500; background: transparent;")
        self._detail.setStyleSheet(f"color: {c[2]}; font-size: 12px; background: transparent;")
        self._status.setStyleSheet(f"color: {c[0]}; font-size: 12px; background: transparent;")


# ── Mode selection screen ──────────────────────────────────────────────────────

class ModeScreen(QWidget):
    """Opening screen — choose Offline or Online install."""

    mode_chosen = pyqtSignal(str)   # "offline" or "online"

    def __init__(self) -> None:
        super().__init__()
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 40, 32, 32)
        root.setSpacing(0)

        # Logo / title
        title = QLabel("Terminal +")
        title.setStyleSheet(f"color: {TEXT_HI}; font-size: 28px; font-weight: 500; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        sub = QLabel("Choose your installation type")
        sub.setStyleSheet(f"color: {TEXT_MED}; font-size: 14px; background: transparent;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(sub)

        root.addSpacing(40)

        # Offline card
        offline_card = self._make_card(
            "Offline Install",
            "No internet required",
            [
                "Installs from bundled files",
                "Terminal + fully functional",
                "Termy AI not included",
                "Fastest install",
            ],
            PRIMARY,
            "offline",
        )
        root.addWidget(offline_card)

        root.addSpacing(16)

        # Online card
        online_card = self._make_card(
            "Online Install",
            "Requires internet connection",
            [
                "Downloads latest files from GitHub",
                "Installs Ollama AI runtime",
                "Builds Termy — your personal AI",
                "Full Terminal + experience",
            ],
            BLUE,
            "online",
        )
        root.addWidget(online_card)

        root.addStretch()

        # Footer
        footer = QLabel(f"github.com/{GITHUB_REPO}")
        footer.setStyleSheet(f"color: {TEXT_DIS}; font-size: 11px; background: transparent;")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(footer)

    def _make_card(
        self,
        title: str,
        subtitle: str,
        features: list[str],
        colour: str,
        mode: str,
    ) -> QFrame:
        card = QFrame()
        card.setObjectName("mode-card")
        card.setStyleSheet(f"""
            QFrame#mode-card {{
                background: {SURFACE};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
            QLabel {{ background: transparent; }}
        """)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(6)

        # Header row
        header_row = QHBoxLayout()
        t = QLabel(title)
        t.setStyleSheet(f"color: {colour}; font-size: 16px; font-weight: 500;")
        header_row.addWidget(t)
        header_row.addStretch()

        btn = QPushButton("Select")
        btn.setFixedSize(80, 32)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {colour};
                color: {ON_PRI};
                border: none;
                border-radius: 16px;
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton:hover {{ background: {'#388E3C' if colour == PRIMARY else BLUE_D}; }}
        """)
        btn.clicked.connect(lambda _, m=mode: self.mode_chosen.emit(m))
        header_row.addWidget(btn)
        layout.addLayout(header_row)

        s = QLabel(subtitle)
        s.setStyleSheet(f"color: {TEXT_MED}; font-size: 12px;")
        layout.addWidget(s)

        layout.addSpacing(8)

        for feat in features:
            row = QHBoxLayout()
            dot = QLabel("•")
            dot.setFixedWidth(14)
            dot.setStyleSheet(f"color: {colour}; font-size: 14px;")
            row.addWidget(dot)
            lbl = QLabel(feat)
            lbl.setStyleSheet(f"color: {TEXT_MED}; font-size: 13px;")
            row.addWidget(lbl, stretch=1)
            layout.addLayout(row)

        return card


# ── Install progress screen ────────────────────────────────────────────────────

class ProgressScreen(QWidget):
    """Shows the step-by-step install progress."""

    def __init__(self, mode: str, script_dir: Path) -> None:
        super().__init__()
        self._mode       = mode
        self._script_dir = script_dir
        self._steps      = online_steps() if mode == "online" else offline_steps()
        self._rows: list[StepRow] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Mode badge
        mode_label = QLabel(
            "Online Install  —  with Termy AI"
            if self._mode == "online"
            else "Offline Install  —  no Termy AI"
        )
        colour = BLUE if self._mode == "online" else PRIMARY
        mode_label.setStyleSheet(
            f"color: {colour}; font-size: 13px; font-weight: 500; "
            f"padding: 10px 20px; background: {SURFACE}; border-bottom: 1px solid {BORDER};"
        )
        root.addWidget(mode_label)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, len(self._steps))
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.setStyleSheet(f"""
            QProgressBar {{ background: {SURFACE2}; border: none; border-radius: 0; }}
            QProgressBar::chunk {{ background: {colour}; border-radius: 0; }}
        """)
        root.addWidget(self._progress)

        # Current step label
        self._current_lbl = QLabel("Starting installation…")
        self._current_lbl.setStyleSheet(
            f"color: {TEAL}; font-size: 13px; font-weight: 500; padding: 12px 20px 6px;"
        )
        root.addWidget(self._current_lbl)

        # Step list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {BG}; }}")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        container.setStyleSheet(f"background: {BG};")
        step_layout = QVBoxLayout(container)
        step_layout.setContentsMargins(0, 0, 0, 0)
        step_layout.setSpacing(0)

        for i, step in enumerate(self._steps):
            row = StepRow(step)
            self._rows.append(row)
            step_layout.addWidget(row)
            if i < len(self._steps) - 1:
                div = QFrame()
                div.setFrameShape(QFrame.Shape.HLine)
                div.setStyleSheet(f"color: {BORDER}; background: {BORDER}; max-height: 1px; margin: 0 16px;")
                step_layout.addWidget(div)

        step_layout.addStretch()
        scroll.setWidget(container)
        self._scroll = scroll
        root.addWidget(scroll)

        # Bottom bar
        bottom = QWidget()
        bottom.setStyleSheet(f"background: {SURFACE}; border-top: 1px solid {BORDER};")
        bottom.setFixedHeight(72)
        bot = QHBoxLayout(bottom)
        bot.setContentsMargins(20, 16, 20, 16)

        self._status_lbl = QLabel("Preparing…")
        self._status_lbl.setStyleSheet(f"color: {TEXT_MED}; font-size: 12px;")
        bot.addWidget(self._status_lbl, stretch=1)

        self._action_btn = QPushButton("Install")
        self._action_btn.setFixedSize(120, 40)
        self._action_btn.setStyleSheet(f"""
            QPushButton {{
                background: {colour};
                color: {ON_PRI};
                border: none;
                border-radius: 20px;
                font-size: 14px;
                font-weight: 500;
            }}
            QPushButton:hover {{ background: {'#388E3C' if colour == PRIMARY else BLUE_D}; }}
            QPushButton:disabled {{ background: {SURFACE2}; color: {TEXT_DIS}; }}
        """)
        self._action_btn.clicked.connect(self._start)
        bot.addWidget(self._action_btn)
        root.addWidget(bottom)

    def _start(self) -> None:
        self._action_btn.setEnabled(False)
        self._action_btn.setText("Installing…")
        self._status_lbl.setText("Installation in progress…")

        self._worker = InstallerWorker(self._steps, self._script_dir)
        self._worker.step_started.connect(self._on_step_started)
        self._worker.step_done.connect(self._on_step_done)
        self._worker.step_failed.connect(self._on_step_failed)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.start()

    def _on_step_started(self, i: int, label: str) -> None:
        self._rows[i].set_running()
        self._current_lbl.setText(f"Step {i + 1} of {len(self._steps)}  —  {label}")
        self._progress.setValue(i)
        self._scroll.ensureWidgetVisible(self._rows[i])

    def _on_step_done(self, i: int) -> None:
        self._rows[i].set_done()
        self._progress.setValue(i + 1)

    def _on_step_failed(self, i: int, error: str) -> None:
        self._rows[i].set_failed(error)
        self._current_lbl.setText(f"Failed at: {self._steps[i].label}")
        self._current_lbl.setStyleSheet(
            f"color: {ERROR}; font-size: 13px; font-weight: 500; padding: 12px 20px 6px;"
        )
        self._status_lbl.setText(f"See log: {LOG_PATH}")
        self._action_btn.setText("Failed")

    def _on_all_done(self, success: bool) -> None:
        if success:
            self._current_lbl.setText("Installation complete!")
            self._current_lbl.setStyleSheet(
                f"color: {PRIMARY}; font-size: 13px; font-weight: 500; padding: 12px 20px 6px;"
            )
            self._status_lbl.setText(f"Log saved to {LOG_PATH}")
            self._action_btn.setText("Launch")
            self._action_btn.setEnabled(True)
            self._action_btn.clicked.disconnect()
            self._action_btn.clicked.connect(self._launch)
        else:
            self._action_btn.setText("Retry")
            self._action_btn.setEnabled(True)
            self._action_btn.clicked.disconnect()
            self._action_btn.clicked.connect(self._retry)

    def _launch(self) -> None:
        compiled = INSTALL_DIR / ".compiled"
        if compiled.exists():
            subprocess.Popen([str(INSTALL_DIR / "terminal-plus")])
        else:
            subprocess.Popen(["python3", "/opt/terminal-plus/main.py"])
        QApplication.quit()

    def _retry(self) -> None:
        QApplication.quit()


# ── Main window ────────────────────────────────────────────────────────────────

class InstallerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Terminal + Installer")
        self.setFixedSize(480, 680)
        self._script_dir = _script_dir()
        self._setup_mode_screen()
        self._apply_base_styles()

    def _apply_base_styles(self) -> None:
        self.setStyleSheet(f"QMainWindow, QWidget {{ background: {BG}; color: {TEXT_HI}; }}")

    def _setup_mode_screen(self) -> None:
        # App bar
        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        app_bar = QWidget()
        app_bar.setStyleSheet(f"background: {SURFACE}; border-bottom: 1px solid {BORDER};")
        app_bar.setFixedHeight(56)
        ab = QVBoxLayout(app_bar)
        ab.setContentsMargins(20, 10, 20, 10)
        t = QLabel("Terminal + Installer")
        t.setStyleSheet(f"color: {TEXT_HI}; font-size: 18px; font-weight: 500;")
        ab.addWidget(t)
        root.addWidget(app_bar)

        self._mode_screen = ModeScreen()
        self._mode_screen.mode_chosen.connect(self._on_mode_chosen)
        root.addWidget(self._mode_screen)

        self.setCentralWidget(container)

    def _on_mode_chosen(self, mode: str) -> None:
        progress = ProgressScreen(mode, self._script_dir)

        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        app_bar = QWidget()
        app_bar.setStyleSheet(f"background: {SURFACE}; border-bottom: 1px solid {BORDER};")
        app_bar.setFixedHeight(56)
        ab = QVBoxLayout(app_bar)
        ab.setContentsMargins(20, 10, 20, 10)
        t = QLabel("Terminal + Installer")
        t.setStyleSheet(f"color: {TEXT_HI}; font-size: 18px; font-weight: 500;")
        ab.addWidget(t)
        root.addWidget(app_bar)
        root.addWidget(progress)

        self.setCentralWidget(container)


# ── Sudo elevation ─────────────────────────────────────────────────────────────

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit


class SudoDialog(QDialog):
    """Asks for the sudo password before the installer starts."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Administrator access required")
        self.setFixedWidth(420)
        self.setModal(True)
        self._build_ui()
        self._apply_styles()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 24)

        title = QLabel("Administrator access required")
        title.setStyleSheet(f"color: {PRIMARY}; font-size: 15px; font-weight: 500; background: transparent;")
        layout.addWidget(title)

        msg = QLabel(
            "Terminal + needs root access to install files to\n"
            "/opt/terminal-plus and /usr/share/applications.\n\n"
            "Enter your sudo password to continue."
        )
        msg.setStyleSheet(f"color: {TEXT_MED}; font-size: 13px; background: transparent;")
        layout.addWidget(msg)

        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("Password")
        self.password_input.returnPressed.connect(self.accept)
        layout.addWidget(self.password_input)

        self._error_lbl = QLabel("")
        self._error_lbl.setStyleSheet(f"color: {ERROR}; font-size: 12px; background: transparent;")
        layout.addWidget(self._error_lbl)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Continue")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.password_input.setFocus()

    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QDialog {{ background: {SURFACE}; }}
            QLineEdit {{
                background: {SURFACE2};
                border: 1px solid {BORDER};
                border-radius: 6px;
                color: {TEXT_HI};
                padding: 8px 10px;
                font-size: 13px;
            }}
            QLineEdit:focus {{ border-color: {PRIMARY}; }}
            QPushButton {{
                padding: 6px 20px;
                border-radius: 16px;
                font-size: 13px;
            }}
            QPushButton[text="Continue"] {{
                background: {PRIMARY};
                color: {ON_PRI};
                border: none;
            }}
            QPushButton[text="Cancel"] {{
                background: {SURFACE2};
                color: {TEXT_MED};
                border: none;
            }}
        """)

    def get_password(self) -> str:
        return self.password_input.text()

    def show_error(self, msg: str) -> None:
        self._error_lbl.setText(msg)


def request_sudo_and_relaunch(app: QApplication) -> None:
    """
    If not already root, show a password dialog and re-exec the
    installer via sudo so it runs with root privileges.
    """
    if os.geteuid() == 0:
        return  # Already root, nothing to do

    while True:
        dialog = SudoDialog()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            sys.exit(0)

        password = dialog.get_password()
        if not password:
            dialog.show_error("Password cannot be empty.")
            continue

        # Test the password with a harmless sudo command
        test = subprocess.run(
            ["sudo", "-S", "-k", "true"],
            input=password + "\n",
            capture_output=True,
            text=True,
        )
        if test.returncode != 0:
            dialog.show_error("Incorrect password. Please try again.")
            continue

        # Password is good — re-exec this process as root
        escaped = password.replace("'", "'\\''")
        executable = sys.executable
        args = sys.argv

        os.execvp(
            "sudo",
            ["sudo", "-S", "--"] + [executable] + args,
        )
        # os.execvp replaces the process — code below never runs
        break


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Terminal + Installer")

    font = QFont("Roboto", 10)
    if not font.exactMatch():
        font = QFont("Sans Serif", 10)
    app.setFont(font)

    # Ask for sudo before showing the main window
    request_sudo_and_relaunch(app)

    window = InstallerWindow()
    window.show()
    sys.exit(app.exec())
