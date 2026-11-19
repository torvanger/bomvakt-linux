#!/bin/sh
# Bomvakt agent for Linux (systemd): install, uninstall or purge.
#
#   curl -fsSL https://bomvakt.com/install.sh | sudo sh
#   curl -fsSL https://bomvakt.com/install.sh | sudo sh -s -- uninstall
#   curl -fsSL https://bomvakt.com/install.sh | sudo sh -s -- purge
#
# From a checkout: sudo sh install.sh [install|uninstall|purge]
# uses the files next to this script instead of downloading them.
#
# What it touches, and nothing else:
#   /usr/lib/bomvakt/bomvakt.py              the agent
#   /usr/lib/bomvakt/install.sh              this script (offline uninstall)
#   /etc/systemd/system/bomvakt.service      the service
#   /etc/bomvakt/bomvakt.conf                config (kept by uninstall)
#   /var/lib/bomvakt                         state (systemd StateDirectory)
#
# Written one command per line so any line can be run by hand.

set -eu

SRC_URL="${BOMVAKT_SRC:-https://raw.githubusercontent.com/torvanger/bomvakt-linux/main}"
LIB_DIR=/usr/lib/bomvakt
CONF_DIR=/etc/bomvakt
UNIT=/etc/systemd/system/bomvakt.service
STATE_DIR=/var/lib/bomvakt
ACTION="${1:-install}"

say() {
    printf 'bomvakt: %s\n' "$*"
}

die() {
    printf 'bomvakt: %s\n' "$*" >&2
    exit 1
}

fetch() {
    # fetch <file> <dest>: copy from the checkout, or download.
    if [ -n "${SRC_DIR:-}" ]; then
        cp "$SRC_DIR/$1" "$2"
        return
    fi
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --retry 2 -o "$2" "$SRC_URL/$1"
        return
    fi
    if command -v wget >/dev/null 2>&1; then
        wget -q -O "$2" "$SRC_URL/$1"
        return
    fi
    die "needs curl or wget"
}

need_python() {
    if command -v python3 >/dev/null 2>&1; then
        python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' && return
        die "python3 is older than 3.8"
    fi
    say "installing python3"
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update -qq
        apt-get install -y -qq python3
        return
    fi
    if command -v dnf >/dev/null 2>&1; then
        dnf install -y -q python3
        return
    fi
    die "python3 not found; install Python 3.8+ and run again"
}

do_install() {
    need_python
    TMP=$(mktemp -d)
    trap 'rm -rf "$TMP"' EXIT
    fetch bomvakt.py "$TMP/bomvakt.py"
    fetch bomvakt.service "$TMP/bomvakt.service"
    fetch bomvakt.conf "$TMP/bomvakt.conf"
    fetch install.sh "$TMP/install.sh"
    python3 -m py_compile "$TMP/bomvakt.py"
    rm -rf "$TMP/__pycache__"

    install -d -m 0755 "$LIB_DIR"
    install -m 0644 "$TMP/bomvakt.py" "$LIB_DIR/bomvakt.py"
    install -m 0755 "$TMP/install.sh" "$LIB_DIR/install.sh"
    install -m 0644 "$TMP/bomvakt.service" "$UNIT"
    install -d -m 0755 "$CONF_DIR"
    if [ -f "$CONF_DIR/bomvakt.conf" ]; then
        say "keeping existing $CONF_DIR/bomvakt.conf"
    else
        install -m 0644 "$TMP/bomvakt.conf" "$CONF_DIR/bomvakt.conf"
    fi

    systemctl daemon-reload
    systemctl enable bomvakt.service
    systemctl restart bomvakt.service
    sleep 2
    if ! systemctl is-active --quiet bomvakt.service; then
        journalctl -u bomvakt.service -n 20 --no-pager
        die "service did not start (log above)"
    fi
    say "installed and running ($(python3 "$LIB_DIR/bomvakt.py" --version))"
    say "log:       journalctl -u bomvakt -f"
    say "config:    $CONF_DIR/bomvakt.conf"
    say "uninstall: sudo $LIB_DIR/install.sh uninstall"
}

do_uninstall() {
    if systemctl list-unit-files bomvakt.service >/dev/null 2>&1; then
        systemctl disable --now bomvakt.service 2>/dev/null || true
    fi
    rm -f "$UNIT"
    systemctl daemon-reload
    systemctl reset-failed bomvakt.service 2>/dev/null || true
    rm -rf "$LIB_DIR"
    rm -rf "$STATE_DIR"
    rm -rf /var/lib/private/bomvakt
    say "uninstalled"
}

do_purge() {
    do_uninstall
    rm -rf "$CONF_DIR"
    say "config removed"
}

[ "$(id -u)" -eq 0 ] || die "run as root (sudo)"
command -v systemctl >/dev/null 2>&1 || die "needs systemd"

# Run from a checkout: use the files next to this script.
# Piped into sh, $0 is "sh", so the files are downloaded.
# (The copy in /usr/lib/bomvakt has no unit file beside it, so it downloads.)
case "$0" in
    *install.sh)
        HERE=$(cd "$(dirname "$0")" && pwd)
        if [ -f "$HERE/bomvakt.service" ]; then
            SRC_DIR=$HERE
        fi
        ;;
esac

case "$ACTION" in
    install)
        do_install
        ;;
    uninstall)
        do_uninstall
        if [ -d "$CONF_DIR" ]; then
            say "config kept in $CONF_DIR (remove it with: purge)"
        fi
        ;;
    purge)
        do_purge
        ;;
    *)
        die "usage: install.sh [install|uninstall|purge]"
        ;;
esac
