"""
Terminal + — GUI Package Store
Detects the system package manager, queries available packages,
and presents them in a categorised browsable UI inside a tab.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# ── Colours (mirrors main.py) ──────────────────────────────────────────────────

BG_MAIN   = "#0d0d0d"
BG_CARD   = "#141414"
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

# ── Categories ────────────────────────────────────────────────────────────────

CATEGORIES: dict[str, list[str]] = {
    "System":      ["htop", "neofetch", "lsof", "strace", "sysstat", "dstat", "iotop", "ncdu", "tree"],
    "Development": ["git", "vim", "neovim", "gcc", "make", "cmake", "python3", "nodejs", "rust", "go"],
    "Network":     ["curl", "wget", "nmap", "netcat", "traceroute", "iperf3", "tcpdump", "openssh"],
    "Media":       ["ffmpeg", "vlc", "mpv", "imagemagick", "gimp", "handbrake"],
    "Security":    ["gnupg", "openssl", "fail2ban", "ufw", "clamav", "rkhunter"],
    "Utilities":   ["zip", "unzip", "tar", "rsync", "screen", "tmux", "fzf", "ripgrep", "bat", "jq"],
}

ALL_PACKAGES = [pkg for pkgs in CATEGORIES.values() for pkg in pkgs]


# ── Data ───────────────────────────────────────────────────────────────────────

@dataclass
class PackageInfo:
    name: str
    description: str = ""
    version: str = ""
    size: str = ""
    installed: bool = False
    category: str = ""


# ── Package manager detection ─────────────────────────────────────────────────

def detect_package_manager() -> str | None:
    """Return the first available package manager on this system."""
    for pm in ("apt", "dnf", "pacman", "zypper", "xbps-install"):
        if shutil.which(pm):
            return pm
    return None


def query_packages(names: list[str], pm: str) -> list[PackageInfo]:
    """
    Query info for a list of package names using the system package manager.
    Falls back gracefully if a package isn't found.
    """
    results: list[PackageInfo] = []
    for name in names:
        info = _query_single(name, pm)
        results.append(info)
    return results


def _query_single(name: str, pm: str) -> PackageInfo:
    """Query one package. Returns a PackageInfo with whatever fields we can fill."""
    try:
        if pm == "apt":
            return _query_apt(name)
        elif pm == "dnf":
            return _query_dnf(name)
        elif pm == "pacman":
            return _query_pacman(name)
        elif pm == "zypper":
            return _query_zypper(name)
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        logger.debug("Could not query %s via %s: %s", name, pm, exc)

    return PackageInfo(name=name, description="No description available.")


def _is_installed(name: str, pm: str) -> bool:
    try:
        if pm == "apt":
            out = subprocess.check_output(
                ["dpkg", "-s", name], stderr=subprocess.DEVNULL, text=True
            )
            return "Status: install ok installed" in out
        elif pm == "dnf":
            result = subprocess.run(
                ["rpm", "-q", name], capture_output=True, text=True
            )
            return result.returncode == 0
        elif pm == "pacman":
            result = subprocess.run(
                ["pacman", "-Q", name], capture_output=True, text=True
            )
            return result.returncode == 0
        elif pm == "zypper":
            result = subprocess.run(
                ["rpm", "-q", name], capture_output=True, text=True
            )
            return result.returncode == 0
    except OSError:
        pass
    return False


def _query_apt(name: str) -> PackageInfo:
    out = subprocess.check_output(
        ["apt-cache", "show", name], stderr=subprocess.DEVNULL, text=True
    )
    info: dict[str, str] = {}
    for line in out.splitlines():
        if ": " in line:
            k, _, v = line.partition(": ")
            info.setdefault(k.strip(), v.strip())

    return PackageInfo(
        name=name,
        description=info.get("Description", "No description available."),
        version=info.get("Version", ""),
        size=_format_size(info.get("Installed-Size", "")),
        installed=_is_installed(name, "apt"),
    )


def _query_dnf(name: str) -> PackageInfo:
    out = subprocess.check_output(
        ["dnf", "info", name], stderr=subprocess.DEVNULL, text=True
    )
    info: dict[str, str] = {}
    for line in out.splitlines():
        if " : " in line:
            k, _, v = line.partition(" : ")
            info.setdefault(k.strip(), v.strip())

    return PackageInfo(
        name=name,
        description=info.get("Summary", "No description available."),
        version=info.get("Version", ""),
        size=info.get("Size", ""),
        installed=_is_installed(name, "dnf"),
    )


def _query_pacman(name: str) -> PackageInfo:
    out = subprocess.check_output(
        ["pacman", "-Si", name], stderr=subprocess.DEVNULL, text=True
    )
    info: dict[str, str] = {}
    for line in out.splitlines():
        if " : " in line:
            k, _, v = line.partition(" : ")
            info.setdefault(k.strip(), v.strip())

    return PackageInfo(
        name=name,
        description=info.get("Description", "No description available."),
        version=info.get("Version", ""),
        size=info.get("Installed Size", ""),
        installed=_is_installed(name, "pacman"),
    )


def _query_zypper(name: str) -> PackageInfo:
    out = subprocess.check_output(
        ["zypper", "info", name], stderr=subprocess.DEVNULL, text=True
    )
    info: dict[str, str] = {}
    for line in out.splitlines():
        if ": " in line:
            k, _, v = line.partition(": ")
            info.setdefault(k.strip(), v.strip())

    return PackageInfo(
        name=name,
        description=info.get("Summary", "No description available."),
        version=info.get("Version", ""),
        size=info.get("Installed Size", ""),
        installed=_is_installed(name, "zypper"),
    )


def _format_size(raw: str) -> str:
    """Convert apt's kB integer string to a human-readable size."""
    try:
        kb = int(raw)
        if kb >= 1024:
            return f"{kb / 1024:.1f} MB"
        return f"{kb} kB"
    except (ValueError, TypeError):
        return raw


# ── Background loader ─────────────────────────────────────────────────────────

class PackageLoader(QThread):
    """Loads package info for a category in the background."""

    packages_ready = pyqtSignal(list)   # list[PackageInfo]
    error_occurred = pyqtSignal(str)

    def __init__(self, names: list[str], pm: str) -> None:
        super().__init__()
        self.names = names
        self.pm = pm

    def run(self) -> None:
        try:
            packages = query_packages(self.names, self.pm)
            self.packages_ready.emit(packages)
        except Exception as exc:
            logger.exception("Package load failed")
            self.error_occurred.emit(str(exc))


# ── Install worker ────────────────────────────────────────────────────────────

class InstallWorker(QThread):
    """Runs the package install command and streams output."""

    output_ready = pyqtSignal(str)
    done = pyqtSignal(bool)   # success

    def __init__(self, name: str, pm: str, password: str) -> None:
        super().__init__()
        self.name = name
        self.pm = pm
        self.password = password

    def run(self) -> None:
        pm_cmds = {
            "apt":    f"apt-get install -y {self.name}",
            "dnf":    f"dnf install -y {self.name}",
            "pacman": f"pacman -S --noconfirm {self.name}",
            "zypper": f"zypper install -y {self.name}",
        }
        cmd = pm_cmds.get(self.pm, "")
        if not cmd:
            self.output_ready.emit(f"Unsupported package manager: {self.pm}")
            self.done.emit(False)
            return

        escaped = self.password.replace("'", "'\\''")
        full_cmd = f"echo '{escaped}' | sudo -S {cmd}"

        try:
            proc = subprocess.Popen(
                full_cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for line in proc.stdout or []:
                clean = line.rstrip()
                if clean:
                    self.output_ready.emit(clean)
            proc.wait()
            self.done.emit(proc.returncode == 0)
        except OSError as exc:
            logger.exception("Install failed for %s", self.name)
            self.output_ready.emit(f"Error: {exc}")
            self.done.emit(False)


# ── Package card widget ───────────────────────────────────────────────────────

class PackageCard(QFrame):
    """A single package row showing name, description, version, size, and install button."""

    install_requested = pyqtSignal(str)   # package name
    remove_requested  = pyqtSignal(str)

    def __init__(self, pkg: PackageInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.pkg = pkg
        self.setObjectName("pkg-card")
        self._build_ui()
        self._apply_styles()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        # Left: name + description
        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        name_lbl = QLabel(self.pkg.name)
        name_lbl.setObjectName("pkg-name")
        text_col.addWidget(name_lbl)

        desc_lbl = QLabel(self.pkg.description)
        desc_lbl.setObjectName("pkg-desc")
        desc_lbl.setWordWrap(True)
        text_col.addWidget(desc_lbl)

        layout.addLayout(text_col, stretch=1)

        # Middle: version + size badges
        meta_col = QVBoxLayout()
        meta_col.setSpacing(4)
        meta_col.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        if self.pkg.version:
            ver_lbl = QLabel(f"v{self.pkg.version}")
            ver_lbl.setObjectName("pkg-meta")
            meta_col.addWidget(ver_lbl)

        if self.pkg.size:
            size_lbl = QLabel(self.pkg.size)
            size_lbl.setObjectName("pkg-meta")
            meta_col.addWidget(size_lbl)

        layout.addLayout(meta_col)

        # Right: action button
        self.action_btn = QPushButton(
            "Remove" if self.pkg.installed else "Install"
        )
        self.action_btn.setObjectName(
            "btn-remove" if self.pkg.installed else "btn-install"
        )
        self.action_btn.setFixedWidth(80)
        self.action_btn.clicked.connect(self._on_action)
        layout.addWidget(self.action_btn)

    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QFrame#pkg-card {{
                background: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 8px;
            }}
            QLabel {{
                background: transparent;
            }}
            QLabel#pkg-name {{
                color: {TEXT};
                font-size: 13px;
                font-weight: bold;
            }}
            QLabel#pkg-desc {{
                color: {MUTED};
                font-size: 12px;
            }}
            QLabel#pkg-meta {{
                color: {MUTED};
                font-size: 11px;
            }}
            QPushButton#btn-install {{
                background: {GREEN_BTN};
                color: {GREEN_TXT};
                border: none;
                border-radius: 4px;
                padding: 5px 0;
                font-size: 12px;
            }}
            QPushButton#btn-install:hover {{
                background: #1D9E75;
            }}
            QPushButton#btn-remove {{
                background: transparent;
                color: {RED_ERR};
                border: 1px solid {RED_ERR};
                border-radius: 4px;
                padding: 5px 0;
                font-size: 12px;
            }}
            QPushButton#btn-remove:hover {{
                background: #2a1010;
            }}
        """)

    def _on_action(self) -> None:
        if self.pkg.installed:
            self.remove_requested.emit(self.pkg.name)
        else:
            self.install_requested.emit(self.pkg.name)

    def set_loading(self, loading: bool) -> None:
        self.action_btn.setEnabled(not loading)
        self.action_btn.setText("…" if loading else ("Remove" if self.pkg.installed else "Install"))

    def mark_installed(self, installed: bool) -> None:
        self.pkg.installed = installed
        self.action_btn.setText("Remove" if installed else "Install")
        self.action_btn.setObjectName("btn-remove" if installed else "btn-install")
        self._apply_styles()


# ── Store pane ────────────────────────────────────────────────────────────────

class StorPane(QWidget):
    """
    The full package store tab.
    Left sidebar: category list.
    Right panel: package cards for the selected category.
    """

    sudo_password_needed = pyqtSignal(str, object)   # pkg name, callback

    def __init__(self, pm: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.pm = pm
        self._cards: list[PackageCard] = []
        self._loader: PackageLoader | None = None
        self._install_worker: InstallWorker | None = None
        self._build_ui()
        self._apply_styles()
        self._select_category("System")

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top bar ──
        top_bar = QWidget()
        top_bar.setObjectName("store-topbar")
        top_bar.setFixedHeight(48)
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(16, 0, 16, 0)

        title = QLabel("Package Store")
        title.setObjectName("store-title")
        top_layout.addWidget(title)

        top_layout.addStretch()

        pm_badge = QLabel(f"via  {self.pm}")
        pm_badge.setObjectName("pm-badge")
        top_layout.addWidget(pm_badge)

        root.addWidget(top_bar)

        # ── Splitter: sidebar + content ──
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setObjectName("store-splitter")

        # Sidebar
        sidebar = QWidget()
        sidebar.setObjectName("store-sidebar")
        sidebar.setFixedWidth(160)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(8, 12, 8, 12)
        sidebar_layout.setSpacing(2)

        cat_title = QLabel("Categories")
        cat_title.setObjectName("cat-title")
        sidebar_layout.addWidget(cat_title)

        self._cat_buttons: dict[str, QPushButton] = {}
        for cat in CATEGORIES:
            btn = QPushButton(cat)
            btn.setObjectName("cat-btn")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _, c=cat: self._select_category(c))
            sidebar_layout.addWidget(btn)
            self._cat_buttons[cat] = btn

        sidebar_layout.addStretch()
        splitter.addWidget(sidebar)

        # Content area
        content = QWidget()
        content.setObjectName("store-content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 16, 16, 16)
        content_layout.setSpacing(12)

        # Status / loading label
        self._status_lbl = QLabel("Loading packages…")
        self._status_lbl.setObjectName("store-status")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content_layout.addWidget(self._status_lbl)

        # Scrollable package list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("pkg-scroll")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._pkg_container = QWidget()
        self._pkg_layout = QVBoxLayout(self._pkg_container)
        self._pkg_layout.setContentsMargins(0, 0, 0, 0)
        self._pkg_layout.setSpacing(8)
        self._pkg_layout.addStretch()

        scroll.setWidget(self._pkg_container)
        content_layout.addWidget(scroll)

        # Install output log
        self._install_log = QLabel("")
        self._install_log.setObjectName("install-log")
        self._install_log.setWordWrap(True)
        content_layout.addWidget(self._install_log)

        splitter.addWidget(content)
        splitter.setSizes([160, 600])
        root.addWidget(splitter)

    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QWidget {{
                background: {BG_MAIN};
            }}
            QLabel {{
                background: transparent;
            }}
            QWidget#store-topbar {{
                background: {BG_HEADER};
                border-bottom: 1px solid {BORDER};
            }}
            QLabel#store-title {{
                color: {TEAL};
                font-size: 14px;
                font-weight: bold;
            }}
            QLabel#pm-badge {{
                color: {MUTED};
                font-size: 12px;
                background: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 4px;
                padding: 2px 8px;
            }}
            QWidget#store-sidebar {{
                background: {BG_HEADER};
                border-right: 1px solid {BORDER};
            }}
            QLabel#cat-title {{
                color: {MUTED};
                font-size: 11px;
                padding: 4px 4px 8px 4px;
            }}
            QPushButton#cat-btn {{
                background: transparent;
                color: {MUTED};
                border: none;
                border-radius: 6px;
                padding: 7px 10px;
                text-align: left;
                font-size: 13px;
            }}
            QPushButton#cat-btn:hover {{
                background: {BORDER};
                color: {TEXT};
            }}
            QPushButton#cat-btn:checked {{
                background: {BG_MAIN};
                color: {TEAL};
                font-weight: bold;
            }}
            QScrollArea#pkg-scroll {{
                border: none;
                background: transparent;
            }}
            QScrollBar:vertical {{
                background: {BG_MAIN};
                width: 6px;
            }}
            QScrollBar::handle:vertical {{
                background: {BORDER};
                border-radius: 3px;
            }}
            QLabel#store-status {{
                color: {MUTED};
                font-size: 13px;
                padding: 40px;
            }}
            QLabel#install-log {{
                color: {TEAL};
                font-size: 11px;
                font-family: Monospace;
                padding: 4px 0;
            }}
            QSplitter#store-splitter::handle {{
                background: {BORDER};
            }}
        """)

    # ── Category selection ─────────────────────────────────────────────────────

    def _select_category(self, category: str) -> None:
        # Update sidebar button states
        for cat, btn in self._cat_buttons.items():
            btn.setChecked(cat == category)

        self._current_category = category
        self._clear_packages()
        self._status_lbl.setText(f"Loading {category}…")
        self._status_lbl.show()

        names = CATEGORIES.get(category, [])
        self._loader = PackageLoader(names, self.pm)
        self._loader.packages_ready.connect(self._on_packages_ready)
        self._loader.error_occurred.connect(self._on_load_error)
        self._loader.start()

    def _clear_packages(self) -> None:
        for card in self._cards:
            self._pkg_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

    def _on_packages_ready(self, packages: list[PackageInfo]) -> None:
        self._status_lbl.hide()

        # Tag category
        cat = getattr(self, "_current_category", "")
        for pkg in packages:
            pkg.category = cat

        for pkg in packages:
            card = PackageCard(pkg)
            card.install_requested.connect(self._on_install_requested)
            card.remove_requested.connect(self._on_remove_requested)
            # Insert before the stretch
            self._pkg_layout.insertWidget(self._pkg_layout.count() - 1, card)
            self._cards.append(card)

    def _on_load_error(self, msg: str) -> None:
        self._status_lbl.setText(f"Could not load packages: {msg}")

    # ── Install / remove ───────────────────────────────────────────────────────

    def _on_install_requested(self, name: str) -> None:
        self.sudo_password_needed.emit(name, self._do_install)

    def _on_remove_requested(self, name: str) -> None:
        self.sudo_password_needed.emit(name, self._do_remove)

    def _do_install(self, name: str, password: str) -> None:
        card = self._card_for(name)
        if card:
            card.set_loading(True)

        self._install_log.setText(f"Installing {name}…")
        self._install_worker = InstallWorker(name, self.pm, password)
        self._install_worker.output_ready.connect(
            lambda line: self._install_log.setText(line)
        )
        self._install_worker.done.connect(lambda ok: self._on_install_done(name, ok))
        self._install_worker.start()

    def _do_remove(self, name: str, password: str) -> None:
        card = self._card_for(name)
        if card:
            card.set_loading(True)

        pm_cmds = {
            "apt":    f"apt-get remove -y {name}",
            "dnf":    f"dnf remove -y {name}",
            "pacman": f"pacman -R --noconfirm {name}",
            "zypper": f"zypper remove -y {name}",
        }
        cmd = pm_cmds.get(self.pm, "")
        escaped = password.replace("'", "'\\''")
        full_cmd = f"echo '{escaped}' | sudo -S {cmd}"

        self._install_log.setText(f"Removing {name}…")
        self._install_worker = InstallWorker(name, self.pm, password)
        self._install_worker.output_ready.connect(
            lambda line: self._install_log.setText(line)
        )
        self._install_worker.done.connect(lambda ok: self._on_remove_done(name, ok))
        self._install_worker.start()

    def _on_install_done(self, name: str, success: bool) -> None:
        card = self._card_for(name)
        if card:
            card.set_loading(False)
            card.mark_installed(success)
        msg = f"{name} installed successfully." if success else f"Failed to install {name}."
        self._install_log.setText(msg)
        logger.info("Install %s: success=%s", name, success)

    def _on_remove_done(self, name: str, success: bool) -> None:
        card = self._card_for(name)
        if card:
            card.set_loading(False)
            card.mark_installed(not success)
        msg = f"{name} removed." if success else f"Failed to remove {name}."
        self._install_log.setText(msg)

    def _card_for(self, name: str) -> PackageCard | None:
        return next((c for c in self._cards if c.pkg.name == name), None)
