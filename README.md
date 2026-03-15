# Terminal +

A smart, modern Linux terminal emulator built with Python and PyQt6. Terminal + runs as a real native desktop app — no existing terminal needed to launch it.

---

## Features

- **Real desktop app** — opens its own native window via PyQt6, shows in your app launcher
- **Tabbed interface** — run multiple sessions side by side with Ctrl+T
- **Sudo modal** — a clean password dialog pops up automatically for any `sudo` command
- **Command history** — navigate previous commands with the up/down arrow keys
- **Auto system detection** — reads your distro name, version, and installed RAM on launch
- **AI assistant (Termy)** — a locally-run Ollama model trained specifically for your system *(coming soon)*
- **Real-time error checker** — catches bad commands before you run them *(coming soon)*
- **GUI package store** — browse and install packages without typing `dnf`/`apt`/`pacman` *(coming soon)*

---

## Screenshots

> Coming soon — drop your screenshots here!

---

## Requirements

- Linux (any distro)
- Python 3.10+
- pip3
- A desktop environment (GNOME, KDE, XFCE, etc.)

---

## Installation

### Quick install (recommended)

```bash
git clone https://github.com/yourusername/terminal-plus.git
cd terminal-plus
sudo ./install.sh
```

This will:
1. Copy the app to `/opt/terminal-plus/`
2. Install PyQt6 via pip
3. Create a `/usr/local/bin/terminal-plus` launcher
4. Add a `.desktop` file so it appears in your app menu

Then find **Terminal +** in your app launcher, or run:

```bash
terminal-plus
```

### Uninstall

```bash
sudo ./install.sh --uninstall
```

---

## Running without installing

```bash
pip3 install PyQt6
python3 main.py
```

---

## Keyboard shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+T` | New tab |
| `Ctrl+W` | Close current tab |
| `Ctrl+L` | Clear output |
| `Ctrl+C` | Interrupt running command |
| `↑ / ↓`  | Navigate command history |

---

## Adding a custom icon

Drop a `128x128` PNG named `icon.png` next to `install.sh` before running the installer. It will be placed in `/usr/share/icons/hicolor/128x128/apps/` and used as the app launcher icon.

If no icon is provided, Terminal + falls back to your system's default terminal icon.

---

## Project structure

```
terminal-plus/
├── main.py           # Main application (PyQt6)
├── install.sh        # System-wide installer
├── requirements.txt  # Python dependencies
├── icon.png          # App icon (optional, 128x128)
└── README.md
```

---

## Roadmap

- [x] Native PyQt6 desktop window
- [x] Tabbed terminal sessions
- [x] Sudo password modal
- [x] Command history navigation
- [x] Auto distro + RAM detection
- [ ] Real-time command error checker
- [ ] Termy — local AI assistant (Ollama)
- [ ] GUI package store (APT / DNF / Pacman / Zypper / Flatpak)
- [ ] Installer wizard with model training

---

## How Termy works

During installation, Terminal + reads your system info:

```
Arch Linux 6.8.0 16GB
```

This gets injected into a custom Ollama model system prompt alongside a pre-written description. The result is an AI assistant that knows exactly what distro and hardware it's running on — so it never gives you the wrong package manager or recommends something your machine can't handle.

---

## Contributing

Pull requests are welcome! If you find a bug or want to suggest a feature, open an issue.

---

## License

MIT License — see `LICENSE` for details.
