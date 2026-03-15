#!/usr/bin/env bash
# build_executable.sh — Compile Terminal + and its installer into native Linux binaries
#
# Usage: ./build_executable.sh
#
# What this does:
#   1. Compiles Terminal + (main.py + modules) with Nuitka → real native binary
#   2. Compiles the installer (installer.py) with PyInstaller → bundles the Terminal + binary inside
#
# Output:
#   dist/TerminalPlus-Installer   ← double-click to install
#   dist/terminal-plus            ← the compiled app (also embedded in the installer)
#
# Requires: python3, pip3, gcc, patchelf

set -euo pipefail
IFS=$'\n\t'

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly DIST_DIR="${SCRIPT_DIR}/dist"
readonly BUILD_DIR="${SCRIPT_DIR}/build"
readonly APP_BINARY="terminal-plus"
readonly INSTALLER_BINARY="TerminalPlus-Installer"

log_info()  { echo -e "\e[32m[INFO]\e[0m  $*"; }
log_warn()  { echo -e "\e[33m[WARN]\e[0m  $*" >&2; }
log_error() { echo -e "\e[31m[ERROR]\e[0m $*" >&2; }
log_step()  { echo -e "\n\e[36m──────────────────────────────────────\e[0m"; echo -e "\e[36m  ▶  $*\e[0m"; echo -e "\e[36m──────────────────────────────────────\e[0m"; }

# ── Checks ─────────────────────────────────────────────────────────────────────

print_banner() {
    echo ""
    echo -e "\e[32m  Terminal +  —  Build System\e[0m"
    echo -e "\e[90m  Compiles Terminal + into native Linux binaries\e[0m"
    echo ""
}

check_deps() {
    log_step "Checking system dependencies"
    local missing=()
    for cmd in python3 pip3 gcc patchelf; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done

    # Check for Python development headers
    if ! python3 -c "import sysconfig; import os; h=sysconfig.get_path('include'); exit(0 if os.path.exists(h+'/Python.h') else 1)" 2>/dev/null; then
        missing+=("python3-devel")
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing: ${missing[*]}"
        log_error "Install them first:"
        log_error "  Fedora:  sudo dnf install python3-devel gcc patchelf"
        log_error "  Ubuntu:  sudo apt install python3-dev gcc patchelf"
        log_error "  Arch:    sudo pacman -S python gcc patchelf"
        exit 1
    fi
    log_info "System dependencies OK."
}

# ── Python package installs ────────────────────────────────────────────────────

pip_install() {
    local pkg="$1"
    pip3 install --quiet "${pkg}" 2>/dev/null \
        || pip3 install --quiet "${pkg}" --break-system-packages 2>/dev/null \
        || { log_error "Could not install ${pkg}"; exit 1; }
}

install_python_deps() {
    log_step "Installing Python build dependencies"

    log_info "Installing PyQt6..."
    pip_install "PyQt6"

    log_info "Installing Nuitka (compiles Terminal + to native binary)..."
    pip_install "nuitka[onefile]"

    log_info "Installing zstandard (required for Nuitka onefile compression)..."
    pip_install "zstandard"

    log_info "Installing PyInstaller (packages the installer)..."
    pip_install "pyinstaller"

    log_info "Installing ordered-set (Nuitka dependency)..."
    pip_install "ordered-set"

    log_info "All Python build deps installed."
}

# ── Step 1: Compile Terminal + with Nuitka ─────────────────────────────────────

compile_app() {
    log_step "Compiling Terminal + with Nuitka"
    log_info "This will take a few minutes — Nuitka compiles Python to C then to a binary."

    mkdir -p "${DIST_DIR}"

    local nuitka_args=(
        --onefile
        --enable-plugin=pyqt6
        --output-dir="${DIST_DIR}"
        --output-filename="${APP_BINARY}"
        --remove-output
        --assume-yes-for-downloads
        --quiet
    )

    # Include all module files
    for f in store.py checker.py termy.py; do
        [[ -f "${SCRIPT_DIR}/${f}" ]] && nuitka_args+=("--include-data-files=${SCRIPT_DIR}/${f}=./")
    done

    # Icon
    if [[ -f "${SCRIPT_DIR}/icon.png" ]]; then
        nuitka_args+=("--linux-icon=${SCRIPT_DIR}/icon.png")
        log_info "Using icon.png"
    fi

    nuitka_args+=("${SCRIPT_DIR}/main.py")

    python3 -m nuitka "${nuitka_args[@]}" \
        || { log_error "Nuitka compilation failed."; exit 1; }

    chmod +x "${DIST_DIR}/${APP_BINARY}"
    log_info "Terminal + compiled → ${DIST_DIR}/${APP_BINARY}"
}

# ── Step 2: Bundle installer with PyInstaller ──────────────────────────────────

build_installer() {
    log_step "Building installer with PyInstaller"
    log_info "Bundling Terminal + binary + all app files into the installer..."

    local args=(
        --onefile
        --windowed
        --name "${INSTALLER_BINARY}"
        --distpath "${DIST_DIR}"
        --workpath "${BUILD_DIR}"
        --specpath "${SCRIPT_DIR}"
        --noconfirm
        --clean
    )

    # Embed icon
    if [[ -f "${SCRIPT_DIR}/icon.png" ]]; then
        args+=("--icon=${SCRIPT_DIR}/icon.png")
    fi

    # Bundle the compiled Terminal + binary
    if [[ -f "${DIST_DIR}/${APP_BINARY}" ]]; then
        args+=("--add-data=${DIST_DIR}/${APP_BINARY}:.")
        log_info "Bundling compiled terminal-plus binary"
    else
        log_warn "Compiled binary not found — installer will fall back to Python scripts."
    fi

    # Bundle all app Python files (used by online installer and as fallback)
    for f in store.py checker.py termy.py requirements.txt; do
        if [[ -f "${SCRIPT_DIR}/${f}" ]]; then
            args+=("--add-data=${SCRIPT_DIR}/${f}:.")
        fi
    done

    # Bundle icon for desktop entry installation
    [[ -f "${SCRIPT_DIR}/icon.png" ]] && args+=("--add-data=${SCRIPT_DIR}/icon.png:.")

    args+=("${SCRIPT_DIR}/installer.py")

    python3 -m PyInstaller "${args[@]}" \
        || { log_error "PyInstaller failed."; exit 1; }

    chmod +x "${DIST_DIR}/${INSTALLER_BINARY}"
    log_info "Installer built → ${DIST_DIR}/${INSTALLER_BINARY}"
}

# ── Update installer.py to deploy the compiled binary ─────────────────────────

patch_installer_for_binary() {
    log_step "Patching installer to deploy compiled binary"

    # The installer's offline step needs to copy the compiled binary
    # instead of Python scripts. We write a small flag file the installer
    # checks at runtime.
    echo "compiled" > "${DIST_DIR}/.build_type"
    log_info "Build type flag written."
}

# ── Cleanup ────────────────────────────────────────────────────────────────────

cleanup() {
    log_step "Cleaning up build files"
    rm -rf "${BUILD_DIR}" \
           "${SCRIPT_DIR}/${INSTALLER_BINARY}.spec" \
           "${SCRIPT_DIR}/main.spec"
    log_info "Build files removed."
}

# ── Result ─────────────────────────────────────────────────────────────────────

print_result() {
    local installer="${DIST_DIR}/${INSTALLER_BINARY}"
    local app="${DIST_DIR}/${APP_BINARY}"

    local installer_size app_size
    installer_size=$(du -sh "${installer}" 2>/dev/null | cut -f1 || echo "?")
    app_size=$(du -sh "${app}" 2>/dev/null | cut -f1 || echo "?")

    echo ""
    echo -e "\e[32m  ✓ Build complete!\e[0m"
    echo ""
    echo -e "  \e[90mInstaller:\e[0m   ${installer}  (${installer_size})"
    echo -e "  \e[90mApp binary:\e[0m  ${app}  (${app_size})"
    echo ""
    echo -e "\e[36m  To distribute Terminal +:\e[0m"
    echo -e "  Share just:  \e[33mdist/TerminalPlus-Installer\e[0m"
    echo ""
    echo -e "\e[36m  To install:\e[0m"
    echo -e "  Right-click → Run as Program"
    echo -e "  Or:  \e[33msudo ./TerminalPlus-Installer\e[0m"
    echo ""
    echo -e "\e[36m  After install, Terminal + appears in your app launcher.\e[0m"
    echo -e "\e[36m  No Python needed on the target machine.\e[0m"
    echo ""
}

# ── Main ───────────────────────────────────────────────────────────────────────

main() {
    print_banner
    check_deps
    install_python_deps
    compile_app
    build_installer
    patch_installer_for_binary
    cleanup
    print_result
}

main "$@"
