#!/usr/bin/env python3
"""Bomvakt agent for Linux servers (https://bomvakt.com).

Version 0.1: follows the web server's access logs and writes a line count
to the journal once per interval. It sends nothing anywhere yet.

Python 3.8+, standard library only. Read the config at /etc/bomvakt/bomvakt.conf.
"""

import argparse
import configparser
import logging
import os
import signal
import sys
import time

VERSION = "0.1.0"
DEFAULT_CONF = "/etc/bomvakt/bomvakt.conf"
DEFAULT_LOGS = "/var/log/nginx/access.log"
POLL_SECONDS = 1.0

log = logging.getLogger("bomvakt")


class Follower:
    """Reads new lines from one log file, like `tail -F`.

    Starts at the end of the file, reopens it after logrotate (new inode or
    the file got shorter), and keeps retrying while it is missing.
    """

    def __init__(self, path):
        self.path = path
        self.fh = None
        self.inode = None
        self.missing_logged = False
        self.partial = ""

    def _open(self, at_end):
        try:
            fh = open(self.path, "r", encoding="utf-8", errors="replace")
        except OSError as exc:
            if not self.missing_logged:
                log.warning("cannot read %s: %s (retrying)", self.path, exc.strerror)
                self.missing_logged = True
            return False
        if at_end:
            fh.seek(0, os.SEEK_END)
        self.fh = fh
        self.inode = os.fstat(fh.fileno()).st_ino
        self.partial = ""
        if self.missing_logged:
            log.info("reading %s again", self.path)
        self.missing_logged = False
        return True

    def _rotated(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return False
        if st.st_ino != self.inode:
            return True
        return st.st_size < self.fh.tell()

    def read_lines(self, first=False):
        if self.fh is None:
            if not self._open(at_end=first):
                return []
        lines = self._drain()
        if self._rotated():
            lines += self._drain()
            self.fh.close()
            self.fh = None
            if self._open(at_end=False):
                lines += self._drain()
        return lines

    def _drain(self):
        data = self.partial + self.fh.read()
        if not data:
            return []
        parts = data.split("\n")
        self.partial = parts.pop()
        return parts

    def close(self):
        if self.fh is not None:
            self.fh.close()
            self.fh = None


def load_config(path):
    cp = configparser.ConfigParser()
    try:
        with open(path, encoding="utf-8") as fh:
            cp.read_file(fh)
    except FileNotFoundError:
        log.warning("no config at %s, using defaults", path)
    except (OSError, configparser.Error) as exc:
        sys.exit("bad config %s: %s" % (path, exc))
    sec = cp["bomvakt"] if cp.has_section("bomvakt") else {}
    logs = [p.strip() for p in sec.get("logs", DEFAULT_LOGS).split(",") if p.strip()]
    try:
        interval = max(10, int(sec.get("interval", "60")))
    except ValueError:
        sys.exit("bad config %s: interval must be a number of seconds" % path)
    return {"logs": logs, "interval": interval}


def run(conf):
    stop = {"now": False}

    def on_signal(signum, _frame):
        stop["now"] = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    followers = [Follower(p) for p in conf["logs"]]
    counts = {f.path: 0 for f in followers}
    for f in followers:
        f.read_lines(first=True)
    log.info("bomvakt %s started, following %s", VERSION, ", ".join(conf["logs"]))

    next_report = time.monotonic() + conf["interval"]
    while not stop["now"]:
        for f in followers:
            counts[f.path] += len(f.read_lines())
        if time.monotonic() >= next_report:
            summary = ", ".join("%s %d" % (p, n) for p, n in counts.items())
            log.info("lines in the last %d s: %s", conf["interval"], summary)
            counts = dict.fromkeys(counts, 0)
            next_report += conf["interval"]
        time.sleep(POLL_SECONDS)

    for f in followers:
        f.close()
    log.info("bomvakt stopped")


def main():
    ap = argparse.ArgumentParser(description="Bomvakt agent")
    ap.add_argument("--conf", default=DEFAULT_CONF, help="config file")
    ap.add_argument("--version", action="version", version="bomvakt " + VERSION)
    args = ap.parse_args()
    # The journal adds its own timestamp.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    run(load_config(args.conf))


if __name__ == "__main__":
    main()
