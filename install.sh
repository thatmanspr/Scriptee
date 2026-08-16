#!/usr/bin/env bash
# Installs Scriptee and its prerequisites on any Linux distro, and puts a
# real `scriptee` command on your PATH (no more `python3 scriptee.py`).
#
# Scriptee itself is pure Python (curses is stdlib) plus reportlab for
# `:pdf` export, so there's nothing distro- or architecture-specific about
# it -- it runs the same on x86_64 and ARM64/aarch64. reportlab ships
# prebuilt wheels for both, so `pip install` below just works on either;
# nothing here is Arch- or x86-only anymore.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${SCRIPTEE_INSTALL_DIR:-$HOME/.local/bin}"
TARGET="$INSTALL_DIR/scriptee"

echo "==> Detected: $(uname -s) / $(uname -m)"

# --------------------------------------------------------------------------
# Make sure a usable python3 + pip exist. Prefer the system package manager
# (covers cases where python3 is present but pip isn't, which is common on
# Debian/Ubuntu); if we don't recognize the package manager, fall back to
# just checking whether python3/pip are already on PATH and telling the
# user what to install if not.
# --------------------------------------------------------------------------
install_with_system_pm() {
    if command -v pacman >/dev/null 2>&1; then
        echo "==> Arch-based system detected (pacman)..."
        sudo pacman -Sy --needed --noconfirm python python-pip
    elif command -v apt-get >/dev/null 2>&1; then
        echo "==> Debian/Ubuntu-based system detected (apt)..."
        sudo apt-get update -y
        sudo apt-get install -y python3 python3-pip python3-venv
    elif command -v dnf >/dev/null 2>&1; then
        echo "==> Fedora/RHEL-based system detected (dnf)..."
        sudo dnf install -y python3 python3-pip
    elif command -v zypper >/dev/null 2>&1; then
        echo "==> openSUSE detected (zypper)..."
        sudo zypper --non-interactive install python3 python3-pip
    elif command -v apk >/dev/null 2>&1; then
        echo "==> Alpine detected (apk)..."
        sudo apk add --no-cache python3 py3-pip
    else
        return 1
    fi
    return 0
}

echo "==> Checking Python (need 3.10+, ideally 3.11+ for built-in TOML support)..."
if ! command -v python3 >/dev/null 2>&1 || ! python3 -m pip --version >/dev/null 2>&1; then
    if ! install_with_system_pm; then
        echo "    Couldn't detect a known package manager (pacman/apt/dnf/zypper/apk)."
        echo "    Please install python3 and pip3 yourself, then re-run this script."
        if ! command -v python3 >/dev/null 2>&1; then
            exit 1
        fi
    fi
fi

PYVER=$(python3 -c 'import sys; print(sys.version_info[:2])')
echo "    Found Python $PYVER"

PIP_INSTALL="python3 -m pip install --user --break-system-packages"
# --break-system-packages is a no-op on distros/pip versions that predate
# PEP 668 externally-managed environments and don't recognize the flag;
# guard for that so this doesn't fail on older setups.
if ! python3 -m pip install --help 2>/dev/null | grep -q -- "--break-system-packages"; then
    PIP_INSTALL="python3 -m pip install --user"
fi

if ! python3 -c "import sys; exit(0 if sys.version_info >= (3, 11) else 1)"; then
    echo "==> Python < 3.11 detected, installing tomli for config parsing..."
    $PIP_INSTALL tomli
fi

echo "==> Installing reportlab (for :pdf export)..."
$PIP_INSTALL reportlab

echo "==> Setting up config..."
mkdir -p ~/.config/scriptee
if [ ! -f ~/.config/scriptee/config.toml ]; then
    cp "$SCRIPT_DIR/config.toml" ~/.config/scriptee/config.toml
    echo "    Wrote default config to ~/.config/scriptee/config.toml"
else
    echo "    Existing config found, leaving it untouched."
fi

echo "==> Installing the scriptee command to $TARGET ..."
mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_DIR/scriptee.py" "$TARGET"
chmod +x "$TARGET"

echo ""
echo "Done. Run it with:"
echo "    scriptee"
echo ""

# Make sure $INSTALL_DIR is actually on PATH; if not, tell the user how to
# fix it for their specific shell instead of leaving them with a dangling
# "optional: put it on your PATH" footnote.
case ":$PATH:" in
    *":$INSTALL_DIR:"*)
        ;;
    *)
        echo "NOTE: $INSTALL_DIR is not on your PATH yet, so 'scriptee' won't"
        echo "resolve until you add it. Pick the line for your shell:"
        echo ""
        echo "  fish:        fish_add_path $INSTALL_DIR"
        echo "  bash:        echo 'export PATH=\"$INSTALL_DIR:\$PATH\"' >> ~/.bashrc"
        echo "  zsh:         echo 'export PATH=\"$INSTALL_DIR:\$PATH\"' >> ~/.zshrc"
        echo ""
        echo "then restart your shell (or 'source' the file)."
        ;;
esac
