#!/usr/bin/env bash
# build_termy.sh — Build and train the Termy AI model for Terminal +
# Usage: ./build_termy.sh
# Requires: ollama, curl
# Run this any time you want to rebuild Termy (e.g. after a hardware upgrade)

set -euo pipefail
IFS=$'\n\t'

readonly MODELFILE_PATH="/tmp/termy_modelfile"
readonly OLLAMA_INSTALL_SCRIPT="https://ollama.ai/install.sh"

# ── Colours ────────────────────────────────────────────────────────────────────

log_info()  { echo -e "\e[32m[INFO]\e[0m  $*"; }
log_warn()  { echo -e "\e[33m[WARN]\e[0m  $*" >&2; }
log_error() { echo -e "\e[31m[ERROR]\e[0m $*" >&2; }
log_step()  { echo -e "\n\e[36m──────────────────────────────────\e[0m"; echo -e "\e[36m  $*\e[0m"; echo -e "\e[36m──────────────────────────────────\e[0m"; }

# ── System detection ───────────────────────────────────────────────────────────

detect_distro_name() {
    if [[ -f /etc/os-release ]]; then
        local name
        name=$(grep -oP '(?<=^NAME=).*' /etc/os-release | tr -d '"')
        echo "${name:-Linux}"
    else
        echo "Linux"
    fi
}

detect_distro_version() {
    if [[ -f /etc/os-release ]]; then
        local ver
        ver=$(grep -oP '(?<=^VERSION_ID=).*' /etc/os-release | tr -d '"')
        echo "${ver:-}"
    else
        echo ""
    fi
}

detect_ram_gb() {
    local kb
    kb=$(awk '/MemTotal/ { print $2 }' /proc/meminfo 2>/dev/null || echo 0)
    echo $(( (kb + 524288) / 1048576 ))
}

# ── Model selection ────────────────────────────────────────────────────────────

select_model() {
    local ram_gb="$1"
    if   (( ram_gb >= 48 )); then echo "llama3.1:70b"
    elif (( ram_gb >= 16 )); then echo "llama3.1:8b"
    elif (( ram_gb >= 8  )); then echo "llama3.2:3b"
    else                          echo "llama3.2:1b"
    fi
}

# ── Ollama ─────────────────────────────────────────────────────────────────────

install_ollama() {
    if command -v ollama &>/dev/null; then
        log_info "Ollama is already installed."
        return 0
    fi

    log_step "Installing Ollama"
    if ! command -v curl &>/dev/null; then
        log_error "curl is required to install Ollama. Install it and re-run."
        exit 1
    fi

    curl -fsSL "${OLLAMA_INSTALL_SCRIPT}" | sh \
        || { log_error "Ollama installation failed. Visit https://ollama.ai to install manually."; exit 1; }
    log_info "Ollama installed successfully."
}

ensure_ollama_running() {
    if ! pgrep -x ollama &>/dev/null; then
        log_info "Starting Ollama service in background..."
        ollama serve &>/dev/null &
        local attempts=0
        while ! pgrep -x ollama &>/dev/null && (( attempts < 10 )); do
            sleep 1
            (( attempts++ ))
        done
        if ! pgrep -x ollama &>/dev/null; then
            log_error "Ollama failed to start. Try running 'ollama serve' manually."
            exit 1
        fi
        log_info "Ollama service started."
    else
        log_info "Ollama is already running."
    fi
}

# ── Modelfile ──────────────────────────────────────────────────────────────────

write_modelfile() {
    local distro_name="$1"
    local distro_version="$2"
    local ram_gb="$3"
    local base_model="$4"

    log_info "Writing Modelfile to ${MODELFILE_PATH}..."

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

# ── Build ──────────────────────────────────────────────────────────────────────

pull_base_model() {
    local base_model="$1"
    log_step "Pulling base model: ${base_model}"
    log_info "This may take a few minutes depending on your connection speed..."
    ollama pull "${base_model}" \
        || { log_error "Failed to pull ${base_model}. Check your internet connection."; exit 1; }
    log_info "Base model pulled."
}

build_model() {
    log_step "Building Termy model"

    # Remove old termy model if it exists
    if ollama list 2>/dev/null | grep -q "^termy"; then
        log_info "Removing existing Termy model..."
        ollama rm termy 2>/dev/null || true
    fi

    ollama create termy -f "${MODELFILE_PATH}" \
        || { log_error "Failed to create Termy model."; exit 1; }
    log_info "Termy model built."
}

verify_model() {
    log_step "Verifying Termy"
    log_info "Sending test prompt..."
    local response
    response=$(ollama run termy "Introduce yourself in one sentence." 2>/dev/null | head -1 || echo "")
    if [[ -n "${response}" ]]; then
        log_info "Termy says: ${response}"
    else
        log_warn "No response received — test manually with: ollama run termy"
    fi
}

cleanup() {
    rm -f "${MODELFILE_PATH}"
}

# ── Main ───────────────────────────────────────────────────────────────────────

print_banner() {
    echo ""
    echo -e "\e[32m  Terminal +  —  Termy Model Builder\e[0m"
    echo -e "\e[90m  Trains a custom AI assistant for your exact hardware\e[0m"
    echo ""
}

main() {
    print_banner
    trap cleanup EXIT

    # Detect system
    local distro_name distro_version ram_gb base_model
    distro_name=$(detect_distro_name)
    distro_version=$(detect_distro_version)
    ram_gb=$(detect_ram_gb)
    base_model=$(select_model "${ram_gb}")

    echo -e "  \e[90mDistro:\e[0m   ${distro_name} ${distro_version}"
    echo -e "  \e[90mRAM:\e[0m      ${ram_gb}GB"
    echo -e "  \e[90mModel:\e[0m    ${base_model}  ← smartest model for your hardware"
    echo ""

    install_ollama
    ensure_ollama_running
    write_modelfile "${distro_name}" "${distro_version}" "${ram_gb}" "${base_model}"
    pull_base_model "${base_model}"
    build_model
    verify_model

    echo ""
    echo -e "\e[32m  ✓ Termy is ready!\e[0m"
    echo -e "\e[90m  Trained on: ${distro_name} ${distro_version} · ${ram_gb}GB RAM\e[0m"
    echo -e "\e[90m  Base model: ${base_model}\e[0m"
    echo ""
    echo -e "  Test anytime:  \e[36mollama run termy\e[0m"
    echo ""
}

main "$@"
