#!/usr/bin/env bash
# install.sh — System-wide installer for Terminal +
# Usage: sudo ./install.sh
# Requires: python3, pip3, curl
# Uninstall: sudo ./install.sh --uninstall

set -euo pipefail
IFS=$'\n\t'

readonly APP_ID="terminal-plus"
readonly INSTALL_DIR="/opt/${APP_ID}"
readonly BIN_LINK="/usr/local/bin/${APP_ID}"
readonly DESKTOP_FILE="/usr/share/applications/${APP_ID}.desktop"
readonly ICON_DIR="/usr/share/icons/hicolor/128x128/apps"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly MODELFILE_PATH="${INSTALL_DIR}/Modelfile"
readonly OLLAMA_INSTALL_SCRIPT="https://ollama.ai/install.sh"

log_info()  { echo -e "\e[32m[INFO]\e[0m  $*"; }
log_warn()  { echo -e "\e[33m[WARN]\e[0m  $*" >&2; }
log_error() { echo -e "\e[31m[ERROR]\e[0m $*" >&2; }
log_step()  { echo -e "\n\e[36m──────────────────────────────────\e[0m"; echo -e "\e[36m  $*\e[0m"; echo -e "\e[36m──────────────────────────────────\e[0m"; }

# ── System detection ───────────────────────────────────────────────────────────

detect_distro_name() {
    [[ -f /etc/os-release ]] && source /etc/os-release && echo "${NAME:-Linux}" || echo "Linux"
}

detect_distro_version() {
    [[ -f /etc/os-release ]] && source /etc/os-release && echo "${VERSION_ID:-}" || echo ""
}

detect_ram_gb() {
    local kb
    kb=$(awk '/MemTotal/ { print $2 }' /proc/meminfo 2>/dev/null || echo 0)
    echo $(( (kb + 524288) / 1048576 ))
}

# ── Model selection ────────────────────────────────────────────────────────────
# Pick the smartest model the machine can actually run.
# llama3.1:70b = best quality, needs ~48GB RAM
# llama3.1:8b  = great quality, needs ~8GB RAM
# llama3.2:3b  = good, needs ~4GB RAM
# llama3.2:1b  = lightweight fallback

select_model() {
    local ram_gb="$1"
    if   (( ram_gb >= 48 )); then echo "llama3.1:70b"
    elif (( ram_gb >= 16 )); then echo "llama3.1:8b"
    elif (( ram_gb >= 8  )); then echo "llama3.2:3b"
    else                          echo "llama3.2:1b"
    fi
}

# ── Checks ─────────────────────────────────────────────────────────────────────

check_root() {
    [[ "$EUID" -eq 0 ]] || { log_error "Run as root: sudo ./install.sh"; exit 1; }
}

check_deps() {
    local missing=()
    for cmd in python3 pip3 curl; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    [[ ${#missing[@]} -eq 0 ]] || { log_error "Missing: ${missing[*]}"; exit 1; }
    log_info "Dependencies OK"
}

# ── Ollama ─────────────────────────────────────────────────────────────────────

install_ollama() {
    if command -v ollama &>/dev/null; then
        log_info "Ollama already installed."
        return 0
    fi
    log_step "Installing Ollama"
    curl -fsSL "${OLLAMA_INSTALL_SCRIPT}" | sh \
        || { log_error "Ollama install failed. Visit https://ollama.ai to install manually."; exit 1; }
    log_info "Ollama installed."
}

ensure_ollama_running() {
    if ! pgrep -x ollama &>/dev/null; then
        log_info "Starting Ollama service..."
        ollama serve &>/dev/null &
        sleep 3
    fi
}

# ── Termy model ────────────────────────────────────────────────────────────────

build_termy_modelfile() {
    local distro_name="$1"
    local distro_version="$2"
    local ram_gb="$3"
    local base_model="$4"

    log_info "Writing Modelfile..."
    mkdir -p "${INSTALL_DIR}"

    cat > "${MODELFILE_PATH}" <<EOF
FROM ${base_model}

SYSTEM """
${distro_name} ${distro_version} ${ram_gb}GB

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
"""

PARAMETER temperature 0.7
PARAMETER top_p 0.9
PARAMETER num_ctx 4096
EOF

    log_info "Modelfile written."
}

pull_base_model() {
    local base_model="$1"
    log_step "Pulling base model: ${base_model}"
    log_info "This may take a few minutes depending on your connection..."
    ollama pull "${base_model}" \
        || { log_error "Failed to pull ${base_model}. Check your internet connection."; exit 1; }
    log_info "Base model ready."
}

create_termy_model() {
    log_step "Training Termy on your system"
    ollama create termy -f "${MODELFILE_PATH}" \
        || { log_error "Failed to create Termy model."; exit 1; }
    log_info "Termy model created."
}

verify_termy() {
    log_info "Verifying Termy..."
    local response
    response=$(ollama run termy "Introduce yourself in one sentence." 2>/dev/null | head -1 || echo "")
    if [[ -n "${response}" ]]; then
        log_info "Termy says: ${response}"
    else
        log_warn "Could not verify — test manually with: ollama run termy"
    fi
}

# ── App install ────────────────────────────────────────────────────────────────

install_files() {
    log_info "Installing files to ${INSTALL_DIR}..."
    mkdir -p "${INSTALL_DIR}"
    for f in main.py store.py checker.py requirements.txt; do
        [[ -f "${SCRIPT_DIR}/${f}" ]] && cp "${SCRIPT_DIR}/${f}" "${INSTALL_DIR}/${f}" \
            || log_warn "${f} not found in ${SCRIPT_DIR} — skipping."
    done
    chmod 755 "${INSTALL_DIR}/main.py"
}

install_python_deps() {
    log_info "Installing Python dependencies..."
    pip3 install --quiet -r "${INSTALL_DIR}/requirements.txt" \
        || { log_error "pip install failed."; exit 1; }
    log_info "Python dependencies installed."
}

create_launcher() {
    log_info "Creating launcher at ${BIN_LINK}..."
    cat > "${BIN_LINK}" <<'EOF'
#!/usr/bin/env bash
exec python3 /opt/terminal-plus/main.py "$@"
EOF
    chmod +x "${BIN_LINK}"
}

install_icon() {
    mkdir -p "${ICON_DIR}"
    if [[ -f "${SCRIPT_DIR}/icon.png" ]]; then
        cp "${SCRIPT_DIR}/icon.png" "${ICON_DIR}/${APP_ID}.png"
        log_info "Icon installed."
    else
        log_warn "No icon.png — drop a 128x128 icon.png next to install.sh to add one."
    fi
}

install_desktop_file() {
    log_info "Installing .desktop file..."
    local icon_name="${APP_ID}"
    [[ -f "${ICON_DIR}/${APP_ID}.png" ]] || icon_name="utilities-terminal"
    mkdir -p /usr/share/applications
    cat > "${DESKTOP_FILE}" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Terminal +
GenericName=Terminal Emulator
Comment=Smart Linux terminal with AI assistant and package store
Exec=${BIN_LINK}
Icon=${icon_name}
Terminal=false
Categories=System;TerminalEmulator;
Keywords=terminal;shell;linux;ai;termy;
StartupNotify=true
StartupWMClass=terminal-plus
EOF
    chmod 644 "${DESKTOP_FILE}"
}

refresh_db() {
    command -v update-desktop-database &>/dev/null \
        && update-desktop-database /usr/share/applications 2>/dev/null || true
    command -v gtk-update-icon-cache &>/dev/null \
        && gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
}

# ── Uninstall ──────────────────────────────────────────────────────────────────

uninstall() {
    log_info "Uninstalling Terminal +..."
    rm -rf "${INSTALL_DIR}"
    rm -f "${BIN_LINK}" "${DESKTOP_FILE}" "${ICON_DIR}/${APP_ID}.png"
    if command -v ollama &>/dev/null; then
        log_info "Removing Termy model..."
        ollama rm termy 2>/dev/null || true
    fi
    refresh_db
    log_info "Done."
    exit 0
}

# ── Main ───────────────────────────────────────────────────────────────────────

print_banner() {
    echo ""
    echo -e "\e[32m  Terminal +  —  Installer\e[0m"
    echo -e "\e[90m  Smart Linux terminal with AI assistant\e[0m"
    echo ""
}

main() {
    print_banner
    [[ "${1:-}" == "--uninstall" ]] && { check_root; uninstall; }

    check_root
    check_deps

    # Detect system
    local distro_name distro_version ram_gb base_model
    distro_name=$(detect_distro_name)
    distro_version=$(detect_distro_version)
    ram_gb=$(detect_ram_gb)
    base_model=$(select_model "${ram_gb}")

    echo -e "  \e[90mDistro:\e[0m  ${distro_name} ${distro_version}"
    echo -e "  \e[90mRAM:\e[0m     ${ram_gb}GB"
    echo -e "  \e[90mModel:\e[0m   ${base_model}  ← smartest model for your hardware"
    echo ""

    log_step "Installing Terminal +"
    install_files
    install_python_deps
    create_launcher
    install_icon
    install_desktop_file
    refresh_db

    log_step "Setting up Termy AI"
    install_ollama
    ensure_ollama_running
    build_termy_modelfile "${distro_name}" "${distro_version}" "${ram_gb}" "${base_model}"
    pull_base_model "${base_model}"
    create_termy_model
    verify_termy

    echo ""
    echo -e "\e[32m  ✓ Terminal + installed!\e[0m"
    echo -e "\e[32m  ✓ Termy trained on ${distro_name} ${distro_version} · ${ram_gb}GB\e[0m"
    echo ""
    log_info "Launch with:  terminal-plus"
    log_info "Or find it in your app launcher."
    echo ""
}

main "$@"
