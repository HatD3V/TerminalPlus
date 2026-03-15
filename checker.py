"""
Terminal + — Live Error Checker
Analyses a command string as the user types and returns
structured warnings shown inline under the input bar.
"""

from __future__ import annotations

import difflib
import logging
import os
import shlex
import shutil
from dataclasses import dataclass
from enum import Enum, auto

logger = logging.getLogger(__name__)


# ── Severity ───────────────────────────────────────────────────────────────────

class Severity(Enum):
    INFO    = auto()   # grey  — suggestion
    WARNING = auto()   # amber — possible mistake
    DANGER  = auto()   # red   — destructive / irreversible


# ── Result ─────────────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    severity: Severity
    message: str
    suggestion: str = ""


# ── Known command sets ─────────────────────────────────────────────────────────

# Commands that always need sudo
SUDO_REQUIRED: set[str] = {
    "apt", "apt-get", "dnf", "yum", "pacman", "zypper",
    "systemctl", "journalctl", "fdisk", "parted", "mkfs",
    "mount", "umount", "ip", "iptables", "ufw", "visudo",
    "useradd", "userdel", "usermod", "groupadd", "passwd",
    "chown", "chmod",
}

# Patterns that are outright dangerous
DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    ("rm -rf /",        "This will delete your entire filesystem."),
    ("rm -rf /*",       "This will delete everything in root."),
    ("rm -rf ~",        "This will delete your entire home directory."),
    ("rm -rf ~/",       "This will delete your entire home directory."),
    ("rm -fr /",        "This will delete your entire filesystem."),
    ("> /dev/sda",      "This will overwrite your entire disk."),
    ("dd if=/dev/zero of=/dev/sd", "This will wipe a disk device."),
    ("mkfs",            "This will format a partition — all data will be lost."),
    ("chmod -R 777 /",  "Setting 777 on / is a critical security risk."),
    (":(){ :|:& };:",   "Fork bomb detected — this will crash your system."),
    ("mv / /dev/null",  "This will attempt to destroy your filesystem."),
]

# Common typos → correct command
TYPO_MAP: dict[str, str] = {
    "gti":      "git",
    "sl":       "ls",
    "dc":       "cd",
    "cd..":     "cd ..",
    "grpe":     "grep",
    "greo":     "grep",
    "cta":      "cat",
    "claer":    "clear",
    "whcih":    "which",
    "sudp":     "sudo",
    "suod":     "sudo",
    "pyhton":   "python3",
    "pyhton3":  "python3",
    "pytohn":   "python3",
    "pthon":    "python3",
    "ndoe":     "node",
    "noed":     "node",
    "mkdit":    "mkdir",
    "mkdri":    "mkdir",
    "toucH":    "touch",
    "touhc":    "touch",
    "nanp":     "nano",
    "viim":     "vim",
    "vmi":      "vim",
    "systemclt":"systemctl",
    "systemcl": "systemctl",
    "ssytemctl":"systemctl",
    "atp":      "apt",
    "apt-gtet": "apt-get",
    "dng":      "dnf",
}

# Known flags per command (used to catch wrong flags)
KNOWN_FLAGS: dict[str, set[str]] = {
    "ls":    {"-l", "-a", "-h", "-R", "-t", "-S", "-r", "-la", "-lah", "-lh", "--color", "--help"},
    "rm":    {"-r", "-f", "-rf", "-fr", "-v", "-i", "--force", "--recursive", "--help"},
    "cp":    {"-r", "-R", "-f", "-v", "-i", "-p", "-u", "--help"},
    "mv":    {"-f", "-i", "-v", "-u", "--help"},
    "mkdir": {"-p", "-v", "-m", "--parents", "--help"},
    "chmod": {"-R", "-v", "-c", "--recursive", "--help"},
    "chown": {"-R", "-v", "-c", "--recursive", "--help"},
    "grep":  {"-r", "-R", "-i", "-v", "-n", "-l", "-c", "-e", "-E", "-w", "--help"},
    "find":  {"-name", "-type", "-size", "-mtime", "-exec", "-delete", "--help"},
    "tar":   {"-c", "-x", "-v", "-f", "-z", "-j", "-J", "-t", "--help",
              "-czf", "-xzf", "-cjf", "-xjf", "-cvf", "-xvf"},
    "git":   {"clone", "commit", "push", "pull", "status", "log", "diff",
              "branch", "checkout", "merge", "rebase", "init", "add",
              "reset", "stash", "fetch", "remote", "--help"},
    "curl":  {"-o", "-O", "-L", "-s", "-v", "-X", "-H", "-d", "-u",
              "--output", "--silent", "--verbose", "--help"},
    "wget":  {"-O", "-q", "-r", "-c", "-b", "--output-document", "--help"},
    "ssh":   {"-p", "-i", "-L", "-R", "-v", "-o", "-N", "--help"},
    "apt":   {"install", "remove", "update", "upgrade", "search",
              "show", "purge", "autoremove", "--help", "-y"},
    "dnf":   {"install", "remove", "update", "upgrade", "search",
              "info", "clean", "autoremove", "--help", "-y"},
    "pacman":{"-S", "-R", "-U", "-Q", "-Ss", "-Si", "-Sy",
              "-Su", "-Syu", "--noconfirm", "--help"},
    "systemctl": {"start", "stop", "restart", "status", "enable",
                  "disable", "reload", "list-units", "--help"},
}


# ── Checker ────────────────────────────────────────────────────────────────────

def check(command: str) -> list[CheckResult]:
    """
    Run all checks on a raw command string.
    Returns a list of CheckResult ordered by severity (DANGER first).
    """
    cmd = command.strip()
    if not cmd:
        return []

    results: list[CheckResult] = []

    try:
        tokens = shlex.split(cmd)
    except ValueError:
        # Unclosed quote etc. — not an error worth flagging yet
        return []

    if not tokens:
        return []

    base = tokens[0]
    args = tokens[1:]

    results += _check_dangerous(cmd)
    results += _check_typo(base)
    results += _check_not_installed(base)
    results += _check_sudo_required(base, cmd)
    results += _check_flags(base, args)

    # Sort: DANGER → WARNING → INFO
    order = {Severity.DANGER: 0, Severity.WARNING: 1, Severity.INFO: 2}
    results.sort(key=lambda r: order[r.severity])

    return results


# ── Individual checks ──────────────────────────────────────────────────────────

def _check_dangerous(cmd: str) -> list[CheckResult]:
    results = []
    for pattern, reason in DANGEROUS_PATTERNS:
        if pattern in cmd:
            results.append(CheckResult(
                severity=Severity.DANGER,
                message=f"Dangerous command: {reason}",
                suggestion="Are you sure? This cannot be undone.",
            ))
    return results


def _check_typo(base: str) -> list[CheckResult]:
    # Exact match in typo map
    if base in TYPO_MAP:
        correct = TYPO_MAP[base]
        return [CheckResult(
            severity=Severity.WARNING,
            message=f"'{base}' is not a recognised command.",
            suggestion=f"Did you mean '{correct}'?",
        )]

    # Fuzzy match against known commands if command not found on PATH
    if not shutil.which(base):
        known = list(TYPO_MAP.values()) + list(KNOWN_FLAGS.keys())
        close = difflib.get_close_matches(base, known, n=1, cutoff=0.75)
        if close:
            return [CheckResult(
                severity=Severity.WARNING,
                message=f"'{base}' not found — possible typo.",
                suggestion=f"Did you mean '{close[0]}'?",
            )]

    return []


def _check_not_installed(base: str) -> list[CheckResult]:
    # Skip sudo itself and shell built-ins
    builtins = {"cd", "clear", "echo", "export", "exit", "source",
                "alias", "unset", "set", "sudo", "which"}
    if base in builtins:
        return []

    # Already flagged as a typo — don't double-report
    if base in TYPO_MAP:
        return []

    if not shutil.which(base):
        return [CheckResult(
            severity=Severity.WARNING,
            message=f"'{base}' is not installed or not in PATH.",
            suggestion=f"Install it first, or check the spelling.",
        )]

    return []


def _check_sudo_required(base: str, cmd: str) -> list[CheckResult]:
    # Already has sudo — no issue
    if base == "sudo":
        return []

    if base in SUDO_REQUIRED:
        return [CheckResult(
            severity=Severity.WARNING,
            message=f"'{base}' usually requires sudo.",
            suggestion=f"Try: sudo {cmd}",
        )]

    return []


def _check_flags(base: str, args: list[str]) -> list[CheckResult]:
    known = KNOWN_FLAGS.get(base)
    if not known:
        return []

    results = []
    for arg in args:
        if not arg.startswith("-"):
            continue
        # Allow chained short flags like -lah by checking subsets
        if arg in known:
            continue
        # Expand combined short flags e.g. -lah → -l -a -h
        if len(arg) > 2 and not arg.startswith("--"):
            sub_flags = {f"-{c}" for c in arg[1:]}
            single_flags = {f for f in known if len(f) == 2}
            if sub_flags.issubset(single_flags):
                continue
        results.append(CheckResult(
            severity=Severity.INFO,
            message=f"Unrecognised flag '{arg}' for '{base}'.",
            suggestion=f"Run '{base} --help' to see valid options.",
        ))

    return results
