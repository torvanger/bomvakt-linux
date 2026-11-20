#!/bin/sh
# Bomvakt agent for Linux (systemd): install, update, uninstall or purge.
#
#   curl -fsSL https://bomvakt.com/install.sh | sudo sh
#   sudo bomvakt update        (= install.sh update: never asks anything)
#   curl -fsSL https://bomvakt.com/install.sh | sudo sh -s -- uninstall
#   curl -fsSL https://bomvakt.com/install.sh | sudo sh -s -- purge
#
# From a checkout: sudo sh install.sh [install|update|uninstall|purge]
# uses the files next to this script instead of downloading them.
#
# install = files in place, service started, then `bomvakt setup` (account
# and key) if this server has no key yet and there is a terminal to ask on.
# update = the same without setup; config and key are kept either way.
#
# What it touches, and nothing else:
#   /usr/lib/bomvakt/bomvakt.py              the agent
#   /usr/lib/bomvakt/install.sh              this script (update, offline uninstall)
#   /usr/local/bin/bomvakt                   link to the agent: `sudo bomvakt …`
#   /etc/systemd/system/bomvakt.service      the service
#   /etc/bomvakt/bomvakt.conf                config (kept by uninstall)
#   /etc/bomvakt/key                         API key (kept by uninstall)
#   /var/lib/bomvakt                         state (systemd StateDirectory)
#
# Written one command per line so any line can be run by hand.

set -eu

SRC_URL="${BOMVAKT_SRC:-https://raw.githubusercontent.com/torvanger/bomvakt-linux/main}"
LIB_DIR=/usr/lib/bomvakt
CONF_DIR=/etc/bomvakt
KEY_FILE=/etc/bomvakt/key
BIN_LINK=/usr/local/bin/bomvakt
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

# Is there a terminal to ask questions on? (`curl | sh` has one, cron has not.)
have_tty() {
    if [ -n "${BOMVAKT_NO_SETUP:-}" ]; then
        return 1
    fi
    ( exec </dev/tty ) 2>/dev/null
}

# systemd 247+ hands the key to the service (LoadCredential); older ones
# need the file readable by the service's group adm.
key_perms() {
    SYSTEMD_VER=$(systemctl --version | head -n 1 | awk '{print $2}')
    if [ "${SYSTEMD_VER:-0}" -ge 247 ] 2>/dev/null; then
        chown root:root "$KEY_FILE"
        chmod 0600 "$KEY_FILE"
        return
    fi
    chown root:adm "$KEY_FILE"
    chmod 0640 "$KEY_FILE"
}

do_install() {
    MODE="$1"
    OLD_VERSION=""
    if [ -f "$LIB_DIR/bomvakt.py" ]; then
        OLD_VERSION=$(python3 "$LIB_DIR/bomvakt.py" --version 2>/dev/null || true)
    fi
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
    install -m 0755 "$TMP/bomvakt.py" "$LIB_DIR/bomvakt.py"
    install -m 0755 "$TMP/install.sh" "$LIB_DIR/install.sh"
    install -m 0644 "$TMP/bomvakt.service" "$UNIT"
    ln -sf "$LIB_DIR/bomvakt.py" "$BIN_LINK"
    install -d -m 0755 "$CONF_DIR"
    if [ -f "$CONF_DIR/bomvakt.conf" ]; then
        say "keeping existing $CONF_DIR/bomvakt.conf"
    else
        install -m 0644 "$TMP/bomvakt.conf" "$CONF_DIR/bomvakt.conf"
    fi
    # An empty key file until setup: the service needs the file to exist.
    if [ ! -f "$KEY_FILE" ]; then
        install -m 0600 /dev/null "$KEY_FILE"
    fi
    key_perms

    systemctl daemon-reload
    systemctl enable bomvakt.service
    systemctl restart bomvakt.service
    sleep 2
    if ! systemctl is-active --quiet bomvakt.service; then
        journalctl -u bomvakt.service -n 20 --no-pager
        die "service did not start (log above)"
    fi
    NEW_VERSION=$(python3 "$LIB_DIR/bomvakt.py" --version)
    if [ "$MODE" = update ] && [ "$OLD_VERSION" = "$NEW_VERSION" ]; then
        say "already the newest ($NEW_VERSION), reinstalled and running"
    elif [ "$MODE" = update ] && [ -n "$OLD_VERSION" ]; then
        say "updated: $OLD_VERSION -> $NEW_VERSION, running"
    else
        say "installed and running ($NEW_VERSION)"
    fi

    if [ "$MODE" = install ] && [ ! -s "$KEY_FILE" ]; then
        if have_tty; then
            echo
            python3 "$LIB_DIR/bomvakt.py" setup </dev/tty || say "setup did not finish; run it again: sudo bomvakt setup"
            key_perms
        else
            say "no terminal to ask on; next step: sudo bomvakt setup"
        fi
    fi

    echo
    say "status:    sudo bomvakt status"
    say "log:       journalctl -u bomvakt -f"
    say "config:    $CONF_DIR/bomvakt.conf"
    say "update:    sudo bomvakt update"
    say "uninstall: sudo $LIB_DIR/install.sh uninstall"
}

do_uninstall() {
    if systemctl list-unit-files bomvakt.service >/dev/null 2>&1; then
        systemctl disable --now bomvakt.service 2>/dev/null || true
    fi
    rm -f "$UNIT"
    rm -f "$BIN_LINK"
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
        do_install install
        ;;
    update)
        do_install update
        ;;
    uninstall)
        do_uninstall
        if [ -d "$CONF_DIR" ]; then
            say "config and key kept in $CONF_DIR (remove them with: purge)"
        fi
        ;;
    purge)
        do_purge
        ;;
    *)
        die "usage: install.sh [install|update|uninstall|purge]"
        ;;
esac
