#!/usr/bin/env python3
"""Bomvakt agent for Linux servers (https://bomvakt.com).

Follows the web server's access logs and sends every line to Bomvakt, so
you can see who hits the server (bots that never run JavaScript included)
and decide what to ban.

    bomvakt setup              create an account, or use an existing key
    bomvakt add-domain NAME    add a domain or IP address to the account
                               (--name "Shown name" sets its display name)
    bomvakt name DOMAIN [TEXT] set or clear a site's display name
    bomvakt password           set the password for the web login (bomvakt.com/konto),
                               and turn two-step login off if the phone is lost
    bomvakt status             account, domains, logs, shared logs, last send
    bomvakt logs               give each website its own access log, like certbot:
                               shows the change, backs up, tests, reloads (--undo)
    bomvakt rules              fetch the rules made at bomvakt.com/konto/regler and
                               apply what this agent supports (nginx rate limits,
                               robots.txt);
                               a timer runs it every 5 minutes (--dry-run, --force)
    bomvakt update             install the latest version (keeps config and key)
    bomvakt parse FILE         show how log lines are read (no sending)
    bomvakt run                what the service runs

Python 3.8+, standard library only. Config: /etc/bomvakt/bomvakt.conf.
"""

import argparse
import calendar
import configparser
import gzip
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "0.6.2"
DEFAULT_CONF = "/etc/bomvakt/bomvakt.conf"
DEFAULT_KEY = "/etc/bomvakt/key"
DEFAULT_SERVER = "https://bomvakt.com"
DEFAULT_LOGS = "/var/log/nginx/access.log"
INSTALLER = "/usr/lib/bomvakt/install.sh"
SRC_URL = os.environ.get("BOMVAKT_SRC") or "https://raw.githubusercontent.com/torvanger/bomvakt-linux/main"

POLL_SECONDS = 1.0
FLUSH_SECONDS = 5.0       # send at least this often while there are lines
FLUSH_LINES = 2000        # or as soon as this many are waiting
MAX_BATCH = 5000          # the server's limit per request
MAX_BUFFER = 200000       # lines kept while the server cannot be reached
MAX_READ = 4 << 20        # bytes read per log and second
MAX_CATCHUP = 100 << 20   # resume after a restart only if this little is unread
MAX_BACKOFF = 300.0
KEY_RE = re.compile(r"^bv_[0-9a-f]{40}$")
USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")

log = logging.getLogger("bomvakt")


# ---------------------------------------------------------------- log lines

# nginx/Apache "combined", optionally with Apache's vhost_combined prefix
# ("example.com:443 "). Quoted fields may hold escaped quotes (\" or \x22).
LINE_RE = re.compile(
    r'^(?:(?P<vhost>\S+) )?(?P<ip>\S+) \S+ (?:\S+) \[(?P<time>[^\]]+)\] '
    r'"(?P<req>(?:[^"\\]|\\.)*)" (?P<status>\d{3}) (?P<bytes>\d+|-)'
    r'(?: "(?P<ref>(?:[^"\\]|\\.)*)" "(?P<ua>(?:[^"\\]|\\.)*)")?'
)
REQ_RE = re.compile(r"^([A-Z]{1,16}) (\S+)(?: ([A-Z]{2,8}/[0-9.]{1,4}))?$")
MONTHS = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
_time_cache = {}


def parse_time(s):
    """'20/Nov/2026:13:05:01 +0100' -> unix time, or None."""
    t = _time_cache.get(s)
    if t is not None:
        return t
    try:
        day, mon, rest = s.split("/", 2)
        year, hh, mm, rest = rest.split(":", 3)
        ss, tz = rest.split(" ")
        t = calendar.timegm((int(year), MONTHS[mon], int(day), int(hh), int(mm), int(ss), 0, 0, 0))
        sign = -1 if tz[0] == "+" else 1
        t += sign * (int(tz[1:3]) * 3600 + int(tz[3:5]) * 60)
    except (ValueError, KeyError, IndexError):
        return None
    if len(_time_cache) > 1000:
        _time_cache.clear()
    _time_cache[s] = t
    return t


def parse_line(line, domain):
    """One log line -> the list the server expects, or None.

    [domain, unix_ts, ip, method, request, protocol, status, bytes, referer, ua]
    A vhost in the line (Apache vhost_combined) wins over the log's domain.
    A request line that is not "METHOD target PROTO" (TLS sent to port 80,
    scanners' junk) is kept whole with an empty method: that is a signal too.
    """
    m = LINE_RE.match(line)
    if not m:
        return None
    ts = parse_time(m.group("time"))
    if ts is None:
        return None
    vhost = m.group("vhost")
    if vhost:
        host = vhost.rsplit(":", 1)[0] if vhost.count(":") == 1 else vhost
        if "." in host:
            domain = host.lower()
    req = m.group("req")
    rm = REQ_RE.match(req)
    method, target, proto = (rm.group(1), rm.group(2), rm.group(3) or "") if rm else ("", req, "")
    b = m.group("bytes")
    ref = m.group("ref") or ""
    ua = m.group("ua") or ""
    return [
        domain, ts, m.group("ip"), method, target[:2048], proto,
        int(m.group("status")), 0 if b == "-" else int(b),
        "" if ref == "-" else ref[:512], "" if ua == "-" else ua[:512],
    ]


# ----------------------------------------------------------------- following

class Follower:
    """Reads new lines from one log file, like `tail -F`.

    Reopens the file after logrotate (new inode, or the file got shorter) and
    keeps retrying while it is missing. `pos` is the offset just after the
    last complete line handed out, so a restart can resume there.
    """

    def __init__(self, path, domain):
        self.path = path
        self.domain = domain
        self.fh = None
        self.inode = None
        self.pos = 0
        self.partial = b""
        self.missing_logged = False

    def open(self, resume=None):
        """resume: None = start at the end, or (inode, pos) from the state file."""
        try:
            fh = open(self.path, "rb")
        except OSError as exc:
            if not self.missing_logged:
                log.warning("cannot read %s: %s (retrying)", self.path, exc.strerror)
                self.missing_logged = True
            return False
        st = os.fstat(fh.fileno())
        pos = st.st_size
        if resume is not None:
            inode, saved = resume
            if inode == st.st_ino and saved <= st.st_size:
                if st.st_size - saved <= MAX_CATCHUP:
                    pos = saved
                else:
                    log.warning("%s: %d MB unread since the last run, starting at the end",
                                self.path, (st.st_size - saved) >> 20)
            elif inode != st.st_ino and st.st_size <= MAX_CATCHUP:
                pos = 0   # rotated while we were stopped: the new file is all unread
        fh.seek(pos)
        self.fh = fh
        self.inode = st.st_ino
        self.pos = pos
        self.partial = b""
        if self.missing_logged:
            log.info("reading %s again", self.path)
        self.missing_logged = False
        return True

    def reopen_new(self):
        try:
            fh = open(self.path, "rb")
        except OSError:
            return False
        self.fh = fh
        self.inode = os.fstat(fh.fileno()).st_ino
        self.pos = 0
        self.partial = b""
        return True

    def _rotated(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return False
        if st.st_ino != self.inode:
            return True
        return st.st_size < self.pos + len(self.partial)

    def read_lines(self):
        if self.fh is None and not self.open():
            return []
        lines = self._drain()
        if len(lines) == 0 and self._rotated():
            # Finish the old file, then start the new one from the top.
            while True:
                more = self._drain()
                if not more:
                    break
                lines += more
            self.fh.close()
            self.fh = None
            if self.reopen_new():
                lines += self._drain()
        return lines

    def _drain(self):
        data = self.fh.read(MAX_READ)
        if not data:
            return []
        data = self.partial + data
        parts = data.split(b"\n")
        self.partial = parts.pop()
        self.pos = self.fh.tell() - len(self.partial)
        return [p.decode("utf-8", "replace") for p in parts if p]

    def close(self):
        if self.fh is not None:
            self.fh.close()
            self.fh = None


# --------------------------------------------------------------------- config

def load_config(path):
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str   # keep log paths as written
    try:
        with open(path, encoding="utf-8") as fh:
            cp.read_file(fh)
    except FileNotFoundError:
        log.warning("no config at %s, using defaults", path)
    except (OSError, configparser.Error) as exc:
        sys.exit("bad config %s: %s" % (path, exc))
    sec = cp["bomvakt"] if cp.has_section("bomvakt") else {}
    domain = sec.get("domain", "").strip().lower()
    logs = {}
    for p in sec.get("logs", DEFAULT_LOGS).split(","):
        if p.strip():
            logs[p.strip()] = domain
    # [logs] path = domain: a log of its own domain (one vhost per log).
    if cp.has_section("logs"):
        for p, d in cp.items("logs"):
            logs[p.strip()] = d.strip().lower() or domain
    try:
        interval = max(10, int(sec.get("interval", "300")))
    except ValueError:
        sys.exit("bad config %s: interval must be a number of seconds" % path)
    server = os.environ.get("BOMVAKT_SERVER") or sec.get("server", DEFAULT_SERVER).strip() or DEFAULT_SERVER
    rules = sec.get("rules", "on").strip().lower()
    if rules not in ("on", "off"):
        sys.exit("bad config %s: rules must be on or off" % path)
    return {"logs": logs, "domain": domain, "interval": interval, "server": server.rstrip("/"), "rules": rules}


def set_conf_value(path, key, value):
    """Set `key = value` in [bomvakt], keeping every comment and line as is."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except FileNotFoundError:
        lines = ["[bomvakt]"]
    section = None
    insert_at = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].strip()
            if section == "bomvakt":
                insert_at = i + 1
            continue
        if section == "bomvakt" and re.match(r"^%s\s*[=:]" % re.escape(key), s):
            lines[i] = "%s = %s" % (key, value)
            break
    else:
        if insert_at is None:
            lines += ["", "[bomvakt]"]
            insert_at = len(lines)
        lines.insert(insert_at, "%s = %s" % (key, value))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def read_key(key_file):
    """The API key: systemd's credential when run as the service, else the file."""
    paths = []
    cred = os.environ.get("CREDENTIALS_DIRECTORY")
    if cred:
        paths.append(os.path.join(cred, "key"))
    paths.append(key_file)
    for p in paths:
        try:
            with open(p, encoding="ascii", errors="replace") as fh:
                k = fh.read().strip()
        except OSError:
            continue
        if k:
            return k
    return ""


def write_key(key_file, key):
    d = os.path.dirname(key_file)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = key_file + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(key + "\n")
    # Old systemd without LoadCredential: the service reads the file via group adm.
    try:
        st = os.stat(key_file)
        os.chown(tmp, st.st_uid, st.st_gid)
        os.chmod(tmp, st.st_mode & 0o777)
    except OSError:
        pass
    os.replace(tmp, key_file)


# ------------------------------------------------------------------------ api

class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code


def api(server, path, body=None, key="", timeout=20, gz=False):
    """Call the Bomvakt API. Returns the JSON reply or raises ApiError.
    status 0 = no answer (network, DNS, TLS)."""
    data = None
    headers = {"User-Agent": "bomvakt-linux/" + VERSION, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
        if gz:
            data = gzip.compress(data, 6)
            headers["Content-Encoding"] = "gzip"
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(server + path, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read().decode("utf-8"))
            raise ApiError(e.code, j.get("code", "http"), j.get("error", "HTTP %d" % e.code))
        except (ValueError, AttributeError):
            raise ApiError(e.code, "http", "HTTP %d from %s" % (e.code, server))
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        reason = getattr(e, "reason", e)
        raise ApiError(0, "network", "cannot reach %s: %s" % (server, reason))
    except ValueError:
        raise ApiError(0, "bad_reply", "the server did not answer JSON")


def newer(latest, mine):
    def parts(v):
        return [int(x) if x.isdigit() else 0 for x in str(v).split(".")]
    return parts(latest) > parts(mine)


# ------------------------------------------------------------------------ run

class Sender:
    """Buffers parsed lines and posts them in gzip batches."""

    def __init__(self, server, key):
        self.server = server
        self.key = key
        self.buf = []
        self.next_try = 0.0
        self.backoff = 0.0
        self.last_flush = time.monotonic()
        self.sent = 0
        self.dropped = 0
        self.warned_domains = set()
        self.told_update = False
        self.key_bad = False
        self.last_ok = None
        self.last_error = ""

    def add(self, rows):
        self.buf.extend(rows)
        if len(self.buf) > MAX_BUFFER:
            lost = len(self.buf) - MAX_BUFFER
            del self.buf[:lost]
            self.dropped += lost
            log.warning("buffer full: dropped the %d oldest lines", lost)

    def due(self):
        now = time.monotonic()
        if not self.buf or now < self.next_try:
            return False
        return len(self.buf) >= FLUSH_LINES or now - self.last_flush >= FLUSH_SECONDS

    def flush(self):
        """Send what is buffered. True when the buffer is empty afterwards."""
        self.last_flush = time.monotonic()
        while self.buf:
            batch = self.buf[:MAX_BATCH]
            try:
                r = api(self.server, "/api/ingest", {"agent": VERSION, "host": socket.gethostname(), "lines": batch},
                        key=self.key, gz=True)
            except ApiError as e:
                return self._failed(e, len(batch))
            del self.buf[:len(batch)]
            self.sent += int(r.get("stored", 0))
            self.dropped += int(r.get("dropped", 0))
            self.backoff = 0.0
            self.last_ok = time.time()
            self.last_error = ""
            if self.key_bad:
                log.info("the key works again")
                self.key_bad = False
            for d in r.get("unknown_domains", []):
                if d not in self.warned_domains:
                    self.warned_domains.add(d)
                    log.warning("lines for %s are dropped: it is not on your account (sudo bomvakt add-domain %s)", d, d)
            latest = r.get("agent_latest")
            if latest and not self.told_update and newer(latest, VERSION):
                self.told_update = True
                log.info("bomvakt %s is available (this is %s): sudo bomvakt update", latest, VERSION)
        return True

    def _failed(self, e, n):
        self.last_error = str(e)
        if e.status in (400, 413):
            # The server will never take this batch: drop it, keep the rest.
            del self.buf[:n]
            self.dropped += n
            log.error("server refused %d lines: %s", n, e)
            return not self.buf
        if e.status == 401:
            if not self.key_bad:
                log.error("the server does not accept the key (%s); run: sudo bomvakt setup", e)
                self.key_bad = True
            delay = MAX_BACKOFF
        else:
            self.backoff = min(MAX_BACKOFF, max(5.0, self.backoff * 2))
            delay = self.backoff
            log.warning("send failed, %d lines waiting, retry in %d s: %s", len(self.buf), delay, e)
        self.next_try = time.monotonic() + delay
        return False


class State:
    """Where each log was read up to, and the last send: survives restarts."""

    def __init__(self):
        d = os.environ.get("STATE_DIRECTORY", "").split(":")[0]
        self.path = os.path.join(d, "state.json") if d else ""
        self.files = {}
        self.data = {}
        if self.path:
            try:
                with open(self.path, encoding="utf-8") as fh:
                    self.data = json.load(fh)
                self.files = {p: tuple(v) for p, v in self.data.get("files", {}).items()}
            except (OSError, ValueError, TypeError):
                pass

    def save(self, followers, sender, safe):
        if not self.path:
            return
        if safe:
            for f in followers:
                if f.fh is not None:
                    self.files[f.path] = (f.inode, f.pos)
        out = {
            "version": VERSION, "saved": int(time.time()),
            "files": self.files,
            "sent_since_start": sender.sent, "dropped_since_start": sender.dropped,
            "waiting": len(sender.buf), "last_ok": sender.last_ok and int(sender.last_ok),
            "last_error": sender.last_error,
        }
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(out, fh)
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("cannot save state: %s", e)


def run(conf, key_file, conf_path=DEFAULT_CONF):
    stop = {"now": False}

    def on_signal(signum, _frame):
        stop["now"] = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    key = read_key(key_file)
    if key and not KEY_RE.match(key):
        log.error("the key in %s is not a Bomvakt key; run: sudo bomvakt setup", key_file)
        key = ""
    if not key:
        log.warning("no key yet: lines are counted, not sent. Run: sudo bomvakt setup")

    found = fix_default_log(conf_path, conf)
    if found:
        log.info("%s does not exist: following %s instead (saved in %s)", DEFAULT_LOGS, found, conf_path)

    state = State()
    sender = Sender(conf["server"], key)
    followers = [Follower(p, d) for p, d in conf["logs"].items()]
    for f in followers:
        f.open(resume=state.files.get(f.path))
        if not f.domain:
            log.warning("%s has no domain: set domain in the config (or run sudo bomvakt setup)", f.path)
    log.info("bomvakt %s started, server %s, following %s", VERSION, conf["server"],
             ", ".join("%s (%s)" % (f.path, f.domain or "no domain") for f in followers))

    counts = dict.fromkeys(conf["logs"], 0)
    bad = 0
    sent_at_report = 0
    next_report = time.monotonic() + conf["interval"]
    while not stop["now"]:
        for f in followers:
            lines = f.read_lines()
            counts[f.path] += len(lines)
            if not key or not f.domain:
                continue
            rows = []
            for line in lines:
                row = parse_line(line, f.domain)
                if row is None:
                    bad += 1
                else:
                    rows.append(row)
            sender.add(rows)
        if key and sender.due():
            empty = sender.flush()
            state.save(followers, sender, safe=empty)
        if time.monotonic() >= next_report:
            summary = ", ".join("%s %d" % (p, n) for p, n in counts.items())
            log.info("last %d s: lines %s; sent %d, waiting %d%s", conf["interval"], summary,
                     sender.sent - sent_at_report, len(sender.buf),
                     ", unreadable %d" % bad if bad else "")
            if bad:
                log.warning("some lines are not in the combined log format; see: bomvakt parse FILE")
            counts = dict.fromkeys(counts, 0)
            bad = 0
            sent_at_report = sender.sent
            next_report += conf["interval"]
            if not key:
                state.save(followers, sender, safe=True)
        time.sleep(POLL_SECONDS)

    empty = True
    if key and sender.buf:
        empty = sender.flush()
    state.save(followers, sender, safe=empty or not key)
    for f in followers:
        f.close()
    log.info("bomvakt stopped")

# -------------------------------------------------------------------- rules
#
# The rules made at bomvakt.com/konto/regler (GET /api/rules), applied by
# `bomvakt rules` as root from bomvakt-rules.timer: the log-following
# service has no rights to change the system, and keeps it that way.
# Everything written is a file of our own (conf.d); nothing in the web
# server's own config is touched. Supported so far: nginx rate limits, and
# robots.txt where the site has a path set on bomvakt.com.

RATE_DIR = "/etc/nginx/conf.d"
RATE_PREFIX = "bomvakt-"
RATE_MARK = "# Bomvakt rate limits"
RULES_STATE = "/var/lib/bomvakt-rules/state.json"
ROBOTS_BACKUP = "/var/lib/bomvakt-rules/robots-backup"
ROBOTS_MAX = 512 << 10
ROBOTS_PATH_RE = re.compile(r"^/[A-Za-z0-9._@+/-]*/robots\.txt$")
NGINX_TEST = ["nginx", "-t"]
NGINX_RELOAD = (["systemctl", "reload", "nginx"], ["nginx", "-s", "reload"])


class RuleError(Exception):
    pass


def agent_domains(conf):
    """The domains this server's logs belong to: the rules are fetched for them."""
    ds = {d for d in conf["logs"].values() if d}
    if conf["domain"]:
        ds.add(conf["domain"])
    return sorted(ds)


def fetch_rules(server, key, domains, etag=""):
    """GET /api/rules for these domains. Returns (reply, etag); reply is None
    when nothing changed since etag (304). Raises ApiError."""
    url = server + "/api/rules"
    if domains:
        url += "?" + urllib.parse.urlencode({"domains": ",".join(domains)})
    headers = {"User-Agent": "bomvakt-linux/" + VERSION, "Accept": "application/json",
               "Authorization": "Bearer " + key}
    if etag:
        headers["If-None-Match"] = etag
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
            return json.loads(r.read().decode("utf-8") or "{}"), r.headers.get("ETag", "")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, etag
        try:
            j = json.loads(e.read().decode("utf-8"))
            raise ApiError(e.code, j.get("code", "http"), j.get("error", "HTTP %d" % e.code))
        except (ValueError, AttributeError):
            raise ApiError(e.code, "http", "HTTP %d from %s" % (e.code, server))
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        raise ApiError(0, "network", "cannot reach %s: %s" % (server, getattr(e, "reason", e)))
    except ValueError:
        raise ApiError(0, "bad_reply", "the server did not answer JSON")


def run_cmd(cmd):
    """(ok, output) of a command, stdout and stderr together."""
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, "%s: %s" % (" ".join(cmd), e)
    return r.returncode == 0, r.stdout.decode("utf-8", "replace").strip()


def write_new(path, text):
    """Write a file atomically, mode 0644 (a config the web server reads)."""
    tmp = path + ".bomvakt-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def after_first_line(text):
    """A rules file without its first line (when it was made)."""
    return text.split("\n", 1)[1] if "\n" in text else ""


def reload_nginx():
    out = ""
    for cmd in NGINX_RELOAD:
        if cmd[0] == "systemctl" and not os.path.isdir("/run/systemd/system"):
            continue
        ok, out = run_cmd(cmd)
        if ok:
            return True, ""
    return False, out


def apply_rate(reply, dry_run=False):
    """Write the nginx rate-limit file when it changed, test it, reload nginx;
    put the old file back when the test fails. Returns (changed, message);
    raises RuleError when it could not."""
    nr = reply.get("nginx_rate") or {}
    path = str(nr.get("path", ""))
    conf = str(nr.get("conf", ""))
    if not os.path.isdir(RATE_DIR) or not shutil.which("nginx"):
        return False, "nginx rate limits: no nginx here, skipped"
    # Only ever a file of our own in conf.d.
    if (os.path.dirname(path) != RATE_DIR or not os.path.basename(path).startswith(RATE_PREFIX)
            or not path.endswith(".conf") or not conf.startswith(RATE_MARK)):
        raise RuleError("the server sent no usable nginx file")
    n = conf.count("\nlimit_req_zone ")
    want = conf if n else ""
    try:
        with open(path, encoding="utf-8") as fh:
            have = fh.read()
    except FileNotFoundError:
        have = None
    except OSError as e:
        raise RuleError("cannot read %s: %s" % (path, e))
    if have is not None and not have.startswith(RATE_MARK):
        raise RuleError("%s was not written by bomvakt: left alone" % path)
    if not want and have is None:
        return False, "nginx rate limits: none"
    if want and have is not None and after_first_line(want) == after_first_line(have):
        return False, "nginx rate limits: %d, unchanged" % n
    if dry_run:
        if not want:
            return True, "would remove %s (no rate limits left)" % path
        return True, "would write %s (%d rate limits):\n\n%s" % (path, n, want)
    try:
        if want:
            write_new(path, want)
        else:
            os.remove(path)
    except OSError as e:
        raise RuleError("cannot write %s: %s" % (path, e))
    ok, out = run_cmd(NGINX_TEST)
    if not ok:
        try:
            if have is None:
                os.remove(path)
            else:
                write_new(path, have)
        except OSError:
            pass
        raise RuleError("nginx -t failed with the new %s, so the old one is back:\n%s" % (path, out))
    ok, out = reload_nginx()
    if not ok:
        raise RuleError("nginx -t passed but nginx did not reload: %s" % out)
    if not want:
        return True, "removed %s (no rate limits left), nginx reloaded" % path
    return True, "wrote %s (%d rate limits), nginx reloaded" % (path, n)


def write_robots(path, text):
    """Write a robots.txt as root into a directory a web user may own: through
    a fresh temp file (O_EXCL, never through a planted link), owned like the
    old file or else like the directory, mode 0644, then renamed over it."""
    import tempfile
    d = os.path.dirname(path)
    try:
        st = os.lstat(path)
        uid, gid = st.st_uid, st.st_gid
    except FileNotFoundError:
        st = os.stat(d)
        uid, gid = st.st_uid, st.st_gid
    fd, tmp = tempfile.mkstemp(prefix=".robots.", suffix=".bomvakt-tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.chown(tmp, uid, gid)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def apply_robots(reply, dry_run=False):
    """Write each site's robots.txt to the path set on bomvakt.com when it
    changed. The first time, the file that was there is copied to
    ROBOTS_BACKUP. Returns ([messages], [errors]); messages only for changes."""
    done, errors = [], []
    for r in reply.get("robots") or []:
        domain = str(r.get("domain", ""))
        path = str(r.get("path", ""))
        text = str(r.get("content", ""))
        if not ROBOTS_PATH_RE.match(path) or "/../" in path or "/./" in path or "//" in path:
            errors.append("robots.txt for %s: unusable path %r" % (domain, path))
            continue
        if len(text.encode("utf-8")) > ROBOTS_MAX:
            errors.append("robots.txt for %s: too large" % domain)
            continue
        d = os.path.dirname(path)
        if not os.path.isdir(d) or os.path.islink(d):
            errors.append("robots.txt for %s: %s is not a directory here" % (domain, d))
            continue
        old = None
        if os.path.lexists(path):
            if os.path.islink(path) or not os.path.isfile(path):
                errors.append("robots.txt for %s: %s is a link or not a file, left alone" % (domain, path))
                continue
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    old = fh.read(ROBOTS_MAX + 1)
            except OSError as e:
                errors.append("robots.txt for %s: cannot read %s: %s" % (domain, path, e))
                continue
        if old == text:
            continue
        if dry_run:
            done.append("would write %s (%d lines)" % (path, len(text.splitlines())))
            continue
        try:
            backup = os.path.join(ROBOTS_BACKUP, re.sub(r"[^A-Za-z0-9._-]", "_", domain) + ".robots.txt")
            saved = old is not None and not os.path.exists(backup)
            if saved:
                os.makedirs(ROBOTS_BACKUP, mode=0o700, exist_ok=True)
                with open(backup, "w", encoding="utf-8") as fh:
                    fh.write(old)
            write_robots(path, text)
            done.append("wrote %s%s" % (path, " (the file that was there is in %s)" % backup if saved else ""))
        except OSError as e:
            errors.append("robots.txt for %s: cannot write %s: %s" % (domain, path, e))
    return done, errors


ROOT_RE = re.compile(r"^/[A-Za-z0-9._@+/-]{0,230}$")


def web_roots(domains):
    """{domain: {"root": dir, "file": bool}} for the given domains, from the
    root / DocumentRoot of the enabled server blocks that name them (www.
    counts as the domain). file = a robots.txt file exists there; a site that
    serves one without it makes it on the fly (WordPress and the like)."""
    want = set(domains)
    out = {}
    try:
        blocks = vhosts()
    except Exception as e:   # never let a config we cannot read stop the rules job
        log.warning("cannot read the web server configs: %s", e)
        return out
    for b in blocks:
        root = (b.get("root") or "").rstrip("/")
        if not root or "$" in root or not ROOT_RE.match(root) or "/../" in root + "/":
            continue
        for n in b["names"]:
            d = n.lower().rstrip(".")
            d = d[4:] if d.startswith("www.") and d[4:] in want else d
            if d in want and d not in out:
                out[d] = {"root": root, "file": os.path.isfile(os.path.join(root, "robots.txt"))}
    return out


def report_roots(conf, key, st):
    """Tell Bomvakt each domain's web root (POST /api/roots), so the website
    can suggest the robots.txt path. Only when it changed since last time."""
    roots = web_roots(agent_domains(conf))
    sig = json.dumps(roots, sort_keys=True)
    if not roots or st.get("roots_sent") == sig:
        st["roots"] = roots
        return
    try:
        api(conf["server"], "/api/roots", {"roots": roots}, key=key)
        st["roots_sent"] = sig
    except ApiError as e:
        log.warning("could not report the web roots: %s", e)
    st["roots"] = roots


def load_rules_state():
    try:
        with open(RULES_STATE, encoding="utf-8") as fh:
            st = json.load(fh)
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def save_rules_state(st):
    try:
        os.makedirs(os.path.dirname(RULES_STATE), mode=0o755, exist_ok=True)
        write_new(RULES_STATE, json.dumps(st, indent=1))
    except OSError as e:
        log.warning("cannot save %s: %s", RULES_STATE, e)



# ------------------------------------------------------------------ commands

ASKING = "setup"   # the command to name when there is no terminal to ask on


def ask(prompt, default=""):
    shown = "%s [%s]: " % (prompt, default) if default else "%s: " % prompt
    try:
        v = input(shown).strip()
    except EOFError:
        print()
        sys.exit("bomvakt: no answer (no terminal); run: sudo bomvakt %s" % ASKING)
    return v or default


def guess_domain():
    name = socket.getfqdn()
    if "." in name and not name.endswith((".local", ".localdomain", ".lan", ".internal")) and name != "localhost":
        return name.lower()
    return ""


def need_root(what):
    if os.geteuid() != 0:
        sys.exit("bomvakt: %s needs root: sudo bomvakt %s" % (what, what))


def restart_service():
    if os.path.isdir("/run/systemd/system") and os.system("systemctl is-enabled --quiet bomvakt 2>/dev/null") == 0:
        os.system("systemctl restart bomvakt")
        print()
        print("Restarted the bomvakt service. Follow it with:")
        print("  journalctl -u bomvakt -f")


# ------------------------------------------------------- one log per site

# The web server's site configs: which sites share a log, and `bomvakt logs`,
# which gives each site its own (like certbot: show, back up, edit, test,
# reload, roll back on a failed test, undo with --undo).
SITE_CONFS = (
    ("nginx", "/etc/nginx/sites-enabled/*", "/var/log/nginx/access.log"),
    ("nginx", "/etc/nginx/conf.d/*.conf", "/var/log/nginx/access.log"),
    ("apache", "/etc/apache2/sites-enabled/*", "/var/log/apache2/access.log"),
    ("apache", "/etc/httpd/conf.d/*.conf", "/var/log/httpd/access_log"),
)
# Where --undo looks for marked lines (sites-enabled entries are symlinks here).
SITE_CONF_DIRS = ("/etc/nginx/sites-available", "/etc/nginx/conf.d",
                  "/etc/apache2/sites-available", "/etc/httpd/conf.d")
CONF_TEST = {"nginx": ["nginx", "-t"], "apache": ["apachectl", "configtest"]}
BACKUP_DIR = "/var/lib/bomvakt/backup"
F2B_JAIL = "/etc/fail2ban/jail.d/bomvakt.conf"
MARK_ADD = "# bomvakt-added (undo: sudo bomvakt logs --undo)"
MARK_OFF = "# bomvakt-off: "
MARK_CONF = "# added by bomvakt logs"
# Hosting panels and config tools rewrite these files: never edit them there.
PANELS = (("/usr/local/psa", "Plesk"), ("/usr/local/cpanel", "cPanel"),
          ("/etc/webmin/virtual-server", "Virtualmin"), ("/usr/local/ispconfig", "ISPConfig"),
          ("/usr/local/directadmin", "DirectAdmin"))
MANAGED_RE = re.compile(r"managed by (ansible|puppet|salt|chef)|do not edit|generated by", re.I)


def site_name(names):
    """The site a block is for: its first real name, www. dropped."""
    for n in names:
        n = n.lower().strip('"\'')
        if n in ("", "_", "localhost", "default") or n.startswith(("~", "*")) or "$" in n:
            continue
        return n[4:] if n.startswith("www.") else n
    return ""


def parse_nginx(lines):
    """The server { } blocks of one file: names, access_log lines at the
    block's own level (not in location { }) and where to add one."""
    blocks = []
    cur = None
    depth = 0
    for i, raw in enumerate(lines):
        line = raw.split("#", 1)[0]
        s = line.strip()
        if cur is not None and depth == cur["depth"] and s:
            m = re.match(r"server_name\s+([^;]+);(.*)$", s)
            if m:
                cur["names"] += m.group(1).split()
                if m.group(2).strip():
                    cur["unclear"] = True
                cur["after"] = i
                cur["indent"] = raw[:len(raw) - len(raw.lstrip())]
            m = re.match(r"access_log\s+([^\s;]+)\s*([^;]*)", s)
            if m:
                cur["logs"].append((i, m.group(1).strip('"\''), m.group(2).strip()))
            m = re.match(r"root\s+([^;]+);", s)
            if m and not cur.get("root"):
                cur["root"] = m.group(1).strip().strip('"\'')
        for pos, ch in enumerate(line):
            if ch == "{":
                depth += 1
                if cur is None and re.search(r"(?:^|[\s;}])server\s*$", line[:pos]):
                    rest = line[pos + 1:]
                    cur = {"start": i, "depth": depth, "names": [], "logs": [], "after": None,
                           "indent": "    ", "unclear": bool(rest.strip(" \t}"))}
                    # A one-line block: read its names and logs, but never edit it.
                    top = rest
                    while re.search(r"\{[^{}]*\}", top):
                        top = re.sub(r"\{[^{}]*\}", "", top)
                    m = re.search(r"server_name\s+([^;]+);", top)
                    if m:
                        cur["names"] += m.group(1).split()
                    for m in re.finditer(r"(?:^|[;\s])access_log\s+([^\s;]+)\s*([^;]*)", top):
                        cur["logs"].append((i, m.group(1).strip('"\''), m.group(2).strip()))
            elif ch == "}":
                if cur is not None and depth == cur["depth"]:
                    cur["end"] = i
                    blocks.append(cur)
                    cur = None
                depth -= 1
    return blocks


def parse_apache(lines):
    """The <VirtualHost> blocks of one file, like parse_nginx."""
    blocks = []
    cur = None
    for i, raw in enumerate(lines):
        s = raw.strip()
        if s.startswith("#"):
            continue
        if re.match(r"<VirtualHost\b", s, re.I):
            cur = {"start": i, "names": [], "logs": [], "after": None, "indent": "    ", "unclear": False}
        elif re.match(r"</VirtualHost\s*>", s, re.I) and cur is not None:
            cur["end"] = i
            blocks.append(cur)
            cur = None
        elif cur is not None:
            m = re.match(r"ServerName\s+(\S+)\s*$", s, re.I)
            if m:
                cur["names"].insert(0, m.group(1))
                cur["after"] = i
                cur["indent"] = raw[:len(raw) - len(raw.lstrip())]
            m = re.match(r"ServerAlias\s+(.+)$", s, re.I)
            if m:
                cur["names"] += m.group(1).split()
            m = re.match(r"CustomLog\s+(\"[^\"]*\"|\S+)\s*(.*)$", s, re.I)
            if m:
                cur["logs"].append((i, m.group(1).strip('"'), m.group(2).strip()))
            m = re.match(r"DocumentRoot\s+(\"[^\"]*\"|\S+)\s*$", s, re.I)
            if m and not cur.get("root"):
                cur["root"] = m.group(1).strip('"')
    return blocks


def vhosts():
    """Every site block of the enabled configs, with its file, kind and the
    log it writes to ("off", the shared log, or its own)."""
    import glob
    out = []
    seen = set()
    for srv, pattern, shared in SITE_CONFS:
        logdir = os.path.dirname(shared)
        for path in sorted(glob.glob(pattern)):
            real = os.path.realpath(path)
            if real in seen or not os.path.isfile(real):
                continue
            seen.add(real)
            try:
                with open(real, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            lines = text.split("\n")
            managed = bool(MANAGED_RE.search(text))
            for b in (parse_nginx if srv == "nginx" else parse_apache)(lines):
                b["site"] = site_name(b["names"])
                if not b["site"]:
                    continue
                own = []
                b["shared_lines"] = []
                b["shared_args"] = None
                for i, p, args in b["logs"]:
                    if p == "off":
                        own.append("off")
                        continue
                    p = p.replace("${APACHE_LOG_DIR}", logdir)
                    if not p.startswith(("/", "|", "syslog:")):
                        p = os.path.join(logdir, os.path.basename(p))
                    if p == shared:
                        b["shared_lines"].append(i)
                        if b["shared_args"] is None:
                            b["shared_args"] = args
                    else:
                        own.append(p)
                b.update(srv=srv, file=real, shared=shared, logdir=logdir, managed=managed,
                         log=own[0] if own else shared)
                out.append(b)
    return out


def site_logs(blocks):
    """{site: its log}; a site with its own log in any block counts as having one."""
    found = {}
    for b in blocks:
        if b["log"] != "off" and found.get(b["site"], b["shared"]) == b["shared"]:
            found[b["site"]] = b["log"]
    return found


def shared_groups(blocks):
    """{shared log: [sites]} for logs that two or more sites write to."""
    by_log = {}
    for site, path in site_logs(blocks).items():
        by_log.setdefault(path, []).append(site)
    shared = {b["shared"] for b in blocks}
    return {p: sorted(s) for p, s in by_log.items() if len(s) > 1 and p in shared}


def unfollowed(blocks, conf):
    """{site: own log} for sites with a log of their own the agent does not follow."""
    shared = {b["shared"] for b in blocks}
    return {s: p for s, p in sorted(site_logs(blocks).items())
            if p not in conf["logs"] and p not in shared and p.startswith("/")}


def web_sites():
    return site_logs(vhosts())


def shared_log_notes(conf):
    """Lines for `status` and `setup` when sites share a log or a site's log
    is not followed. Nothing when every site has its own, followed log."""
    blocks = vhosts()
    notes = []
    for path, sites in sorted(shared_groups(blocks).items()):
        where = ("all sent as %s" % (conf["logs"][path] or "NO DOMAIN")) if path in conf["logs"] else "not followed"
        notes.append("  %s share %s (%s)" % (", ".join(sites), path, where))
    for site, path in unfollowed(blocks, conf).items():
        notes.append("  %s has %s (not followed)" % (site, path))
    if notes:
        notes.insert(0, "web server sites:")
        notes.append("  Fix it: sudo bomvakt logs (shows each change and asks first)")
    return notes


def site_log_path(domain):
    """Where a site's own log goes: the one it already has, else the usual
    folder of this server's web server."""
    domain = domain[4:] if domain.startswith("www.") else domain
    blocks = vhosts()
    path = site_logs(blocks).get(domain, "")
    if path and path not in {b["shared"] for b in blocks}:
        return path
    for d in ("/var/log/nginx", "/var/log/apache2", "/var/log/httpd"):
        if os.path.isdir(d):
            return "%s/%s.access.log" % (d, domain)
    return "/var/log/nginx/%s.access.log" % domain


def default_log():
    """The shared access log of the web server on this server, or ''."""
    for _, _, path in SITE_CONFS:
        if os.path.exists(path):
            return path
    return ""


def fix_default_log(path, conf):
    """The config's nginx log on a server without nginx (the shipped default):
    put the web server log that is there in its place, in the file and in
    `conf`. Returns that log, or '' when nothing changed."""
    if DEFAULT_LOGS not in conf["logs"] or os.path.exists(DEFAULT_LOGS):
        return ""
    found = default_log()
    if not found or found in conf["logs"]:
        return ""
    cp = configparser.ConfigParser()
    try:
        with open(path, encoding="utf-8") as fh:
            cp.read_file(fh)
        raw = cp.get("bomvakt", "logs", fallback=DEFAULT_LOGS)
        new = ", ".join(found if p.strip() == DEFAULT_LOGS else p.strip() for p in raw.split(",") if p.strip())
        set_conf_value(path, "logs", new or found)
    except (OSError, configparser.Error):
        return ""
    conf["logs"][found] = conf["logs"].pop(DEFAULT_LOGS)
    return found


def panel():
    for path, name in PANELS:
        if os.path.exists(path):
            return name
    return ""


def plan_edits(blocks, sites):
    """New file contents that give each of `sites` its own log: a line after
    server_name/ServerName in each of its blocks, and the block's line to
    the shared log commented out (both servers log to every line they have).
    Returns ({file: new lines}, {site: log}, {site: why it is skipped})."""
    edits = {}
    new_logs = {}
    skipped = {}
    have = site_logs(blocks)
    for b in blocks:
        if b["site"] in sites and (b["managed"] or b["unclear"] or b["after"] is None):
            skipped[b["site"]] = ("%s is written by a tool or panel" % b["file"]) if b["managed"] \
                else ("%s is laid out in a way bomvakt does not edit" % b["file"])
    for b in blocks:
        site = b["site"]
        if site not in sites or site in skipped or b["log"] == "off":
            continue
        own = have.get(site, "")
        if not own.startswith("/") or own == b["shared"]:
            own = "%s/%s.access.log" % (b["logdir"], site)
        if b["log"] == b["shared"]:
            new_logs[site] = own
        elif b["log"].startswith("/"):
            new_logs[site] = b["log"]
        if b["log"] != b["shared"] and not b["shared_lines"]:
            continue
        if b["file"] not in edits:
            with open(b["file"], encoding="utf-8") as fh:
                edits[b["file"]] = fh.read().split("\n")
        lines = edits[b["file"]]
        for i in b["shared_lines"]:
            raw = lines[i]
            ind = raw[:len(raw) - len(raw.lstrip())]
            lines[i] = ind + MARK_OFF + raw.lstrip()
        if b["log"] == b["shared"]:
            # Keep the shared line's format (and nginx options such as buffer=).
            args = b["shared_args"]
            if b["srv"] == "nginx":
                line = "access_log %s%s;" % (own, " " + args if args else "")
            else:
                line = "CustomLog %s %s" % (own, args or "combined")
            # Inserted lines shift later indexes: mark the spot, insert at the end.
            lines[b["after"]] = lines[b["after"]] + "\n" + b["indent"] + MARK_ADD + "\n" + b["indent"] + line
    for f in edits:
        edits[f] = "\n".join(edits[f]).split("\n")
    return edits, new_logs, skipped


def write_keeping(path, text):
    """Replace a file atomically with the same owner and mode."""
    st = os.stat(path)
    tmp = path + ".bomvakt-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, st.st_mode & 0o7777)
    try:
        os.chown(tmp, st.st_uid, st.st_gid)
    except OSError:
        pass
    os.replace(tmp, path)


def backup(files):
    """Copy files to a new folder under BACKUP_DIR, path kept; returns it."""
    import shutil
    d = os.path.join(BACKUP_DIR, time.strftime("%Y%m%d-%H%M%S"))
    for f in files:
        dest = os.path.join(d, f.lstrip("/"))
        os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
        shutil.copy2(f, dest)
    return d


def restore(d, files):
    for f in files:
        with open(os.path.join(d, f.lstrip("/")), encoding="utf-8") as fh:
            write_keeping(f, fh.read())


def conf_test(kinds):
    """Run each web server's own config test; (ok, output)."""
    import subprocess
    for k in sorted(kinds):
        try:
            r = subprocess.run(CONF_TEST[k], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, "%s: %s" % (" ".join(CONF_TEST[k]), e)
        if r.returncode != 0:
            return False, r.stdout.decode("utf-8", "replace").strip()
    return True, ""


def reload_web(kinds):
    for k in sorted(kinds):
        unit = "nginx" if k == "nginx" else ("apache2" if os.path.isdir("/etc/apache2") else "httpd")
        os.system("systemctl reload %s" % unit)
        print("Reloaded %s." % unit)


def add_conf_logs(path, entries):
    """Add `log = site` lines under [logs] in bomvakt.conf, each after a
    marker line so --undo can take them out again; keeps the rest as is."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except FileNotFoundError:
        lines = ["[bomvakt]"]
    at = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s == "[logs]":
            at = i + 1
        elif at is not None and s.startswith("[") and s.endswith("]"):
            break
        elif at is not None and s:
            at = i + 1
    if at is None:
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", "[logs]"]
        at = len(lines)
    add = []
    for log_path, site in entries:
        add += [MARK_CONF, "%s = %s" % (log_path, site)]
    lines[at:at] = add
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip("\n") + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def f2b_logpath(text, globs):
    """The fail2ban jail with the bomvakt-bots logpath also reading `globs`;
    None when there is nothing to change."""
    lines = text.split("\n")
    section = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1]
            continue
        if section == "bomvakt-bots" and re.match(r"logpath\s*=", s):
            j = i + 1
            while j < len(lines) and lines[j][:1] in (" ", "\t") and lines[j].strip():
                j += 1
            have = [line.split("=", 1)[1].strip()] + [x.strip() for x in lines[i + 1:j]]
            have = [x for x in have if x]
            # Only folders the jail already reads: a log of nginx's in front of Apache is enough.
            dirs = {os.path.dirname(x) for x in have}
            want = have + [g for g in globs if g not in have and os.path.dirname(g) in dirs]
            if want == have:
                return None
            lines[i:j] = ["logpath  = " + want[0]] + ["           " + x for x in want[1:]]
            return "\n".join(lines)
    return None


def show_diff(path, old, new):
    import difflib
    sys.stdout.writelines(difflib.unified_diff([x + "\n" for x in old], [x + "\n" for x in new],
                                               path, path + " (new)", n=2))


def cmd_logs(args, conf):
    global ASKING
    if not getattr(args, "from_setup", False):
        ASKING = "logs"
    if args.undo:
        return logs_undo(args, conf)
    if not args.dry_run:
        need_root("logs")
        found = fix_default_log(args.conf, conf)
        if found:
            print("%s: logs = %s (this server has no %s)." % (args.conf, found, DEFAULT_LOGS))
    name = panel()
    if name:
        sys.exit("bomvakt: %s manages this server's web configs and would overwrite a change.\n"
                 "Give each site its own log in %s, then map the logs under [logs] in %s." % (name, name, args.conf))
    blocks = vhosts()
    if not blocks:
        sys.exit("bomvakt: found no nginx or Apache sites in %s." % ", ".join(p for _, p, _ in SITE_CONFS))
    groups = shared_groups(blocks)
    follow = unfollowed(blocks, conf)
    if not groups and not follow:
        print("Every site has its own log, and the agent follows each one. Nothing to do.")
        return
    split = []
    for path, sites in sorted(groups.items()):
        print("These sites write to one log, %s:" % path)
        for s in sites:
            print("  %d) %s" % (len(split) + 1, s))
            split.append(s)
    if split:
        print("Each one gets its own log next to it, <site>.access.log.")
        if not args.yes:
            while True:
                a = ask("Which? all, none, or numbers like 1,3", "all").lower().replace(" ", "")
                if a in ("all", "a", "yes", "y"):
                    break
                if a in ("none", "no", "n"):
                    split = []
                    break
                try:
                    picked = sorted({int(x) for x in a.split(",") if x})
                except ValueError:
                    picked = []
                if picked and all(1 <= x <= len(split) for x in picked):
                    split = [split[x - 1] for x in picked]
                    break
                print("  Type all, none, or numbers from 1 to %d." % len(split))
    sites = set(split)

    _, _, skipped = plan_edits(blocks, sites)
    for site, why in sorted(skipped.items()):
        print("Skipped %s: %s. How to do it by hand: %s/konto/hjalp/sajtlogg" % (site, why, conf["server"]))
        sites.discard(site)

    # Every site needs to be on the account, or the server drops its lines.
    key = read_key(args.key_file)
    if key and not args.dry_run:
        try:
            names = [s["domain"] for s in api(conf["server"], "/api/me", key=key)["sites"]]
        except ApiError as e:
            sys.exit("bomvakt: %s" % e)
        for site in sorted(sites | set(follow)):
            if site in names or any(site.endswith("." + n) for n in names):
                continue
            if args.yes or ask("%s is not on your account. Add it? (yes/no)" % site, "yes").lower() in ("y", "yes", "j", "ja"):
                try:
                    api(conf["server"], "/api/domain", {"domain": site}, key=key)
                    print("Added %s to the account." % site)
                    continue
                except ApiError as e:
                    print("  %s" % e)
            print("Left %s as it is." % site)
            sites.discard(site)
            follow.pop(site, None)
    elif not key and not args.dry_run:
        sys.exit("bomvakt: no key yet; run: sudo bomvakt setup")

    edits, new_logs, _ = plan_edits(blocks, sites)
    new_logs.update({s: p for s, p in follow.items() if s not in new_logs})
    if not new_logs:
        print("Nothing changed.")
        return

    f2b_new = None
    if os.path.exists(F2B_JAIL):
        try:
            with open(F2B_JAIL, encoding="utf-8") as fh:
                f2b_old = fh.read()
            f2b_new = f2b_logpath(f2b_old, sorted({os.path.dirname(p) + "/*.access.log" for p in new_logs.values()}))
        except OSError:
            f2b_new = None

    print()
    for f in sorted(edits):
        with open(f, encoding="utf-8") as fh:
            show_diff(f, fh.read().split("\n"), edits[f])
    if f2b_new is not None:
        show_diff(F2B_JAIL, f2b_old.split("\n"), f2b_new.split("\n"))
    print("%s %s, under [logs]:" % ("and in" if edits or f2b_new is not None else "In", args.conf))
    for site, path in sorted(new_logs.items()):
        print("  %s = %s" % (path, site))
    print()
    if args.dry_run:
        print("Dry run: nothing changed. Run without --dry-run to make these changes.")
        return
    if not args.yes and ask("Make these changes? (yes/no)", "yes").lower() not in ("y", "yes", "j", "ja"):
        print("Nothing changed.")
        return

    files = sorted(edits) + ([F2B_JAIL] if f2b_new is not None else [])
    saved = backup(files)
    for f in sorted(edits):
        write_keeping(f, "\n".join(edits[f]))
    kinds = {b["srv"] for b in blocks if b["file"] in edits}
    ok, out = conf_test(kinds)
    if not ok:
        restore(saved, sorted(edits))
        print(out)
        sys.exit("bomvakt: the web server's config test failed, so every file is back as it was\n"
                 "and nothing was reloaded. Backup: %s" % saved)
    reload_web(kinds)
    add_conf_logs(args.conf, sorted((p, s) for s, p in new_logs.items()))
    if f2b_new is not None:
        write_keeping(F2B_JAIL, f2b_new)
        os.system("fail2ban-client reload >/dev/null 2>&1")
        print("fail2ban reads the new logs too.")
    print("Backup of the old files: %s" % saved)
    for site, path in sorted(new_logs.items()):
        print("  %s -> %s" % (site, path))
    print("Undo it all with: sudo bomvakt logs --undo")
    if not getattr(args, "from_setup", False):
        restart_service()


def undo_lines(lines):
    """Take out MARK_ADD lines (and the line after each), and uncomment
    MARK_OFF lines. Returns the new lines, or None when there were none."""
    out = []
    skip = False
    hit = False
    for raw in lines:
        if skip:
            skip = False
            continue
        s = raw.strip()
        if s == MARK_ADD:
            skip = hit = True
            continue
        if s.startswith(MARK_OFF):
            ind = raw[:len(raw) - len(raw.lstrip())]
            out.append(ind + s[len(MARK_OFF):])
            hit = True
            continue
        out.append(raw)
    return out if hit else None


def undo_conf_lines(lines):
    out = []
    skip = False
    for raw in lines:
        if skip:
            skip = False
            continue
        if raw.strip() == MARK_CONF:
            skip = True
            continue
        out.append(raw)
    return out


def logs_undo(args, conf):
    import glob
    need_root("logs --undo")
    edits = {}
    kinds = set()
    for d in SITE_CONF_DIRS:
        for f in sorted(glob.glob(d + "/*")):
            if not os.path.isfile(f) or os.path.islink(f):
                continue
            try:
                with open(f, encoding="utf-8") as fh:
                    old = fh.read().split("\n")
            except (OSError, UnicodeDecodeError):
                continue
            new = undo_lines(old)
            if new is not None:
                edits[f] = new
                kinds.add("nginx" if "/nginx/" in f else "apache")
    if not edits:
        print("No changes by bomvakt logs found. Nothing to undo.")
        return
    for f in sorted(edits):
        with open(f, encoding="utf-8") as fh:
            show_diff(f, fh.read().split("\n"), edits[f])
    if ask("Undo these changes? (yes/no)", "yes").lower() not in ("y", "yes", "j", "ja"):
        print("Nothing changed.")
        return
    saved = backup(sorted(edits))
    for f in sorted(edits):
        write_keeping(f, "\n".join(edits[f]))
    ok, out = conf_test(kinds)
    if not ok:
        restore(saved, sorted(edits))
        print(out)
        sys.exit("bomvakt: the config test failed, so the files are back as they were. Backup: %s" % saved)
    reload_web(kinds)
    try:
        with open(args.conf, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        write_keeping(args.conf, "\n".join(undo_conf_lines(lines)))
    except OSError:
        pass
    print("Took the own logs out of the web server and %s. The log files are kept." % args.conf)
    print("fail2ban's logpath is left as it is; it does no harm.")
    restart_service()


def pick_site(server, key, names):
    """Which of the account's sites this server's access log belongs to.
    A log line has no domain in it (in the usual combined format), so the
    agent tags every line with this one. Adds a new domain when typed."""
    print()
    print("Which website runs on this server? Its access log is sent as that site.")
    guess = guess_domain()
    default = str(names.index(guess) + 1) if guess in names else ("1" if names else "")
    for i, n in enumerate(names, 1):
        print("  %d) %s" % (i, n))
    if names:
        print("  or type another domain or IP address to add it to the account")
    while True:
        a = ask("Website" if not names else "Choose 1-%d or type a domain" % len(names), default or guess).lower()
        if a.isdigit():
            if 1 <= int(a) <= len(names):
                return names[int(a) - 1]
            print("  Pick a number from 1 to %d, or type a domain." % len(names))
            continue
        if not a:
            print("  A domain or IP address is required.")
            continue
        if a in names:
            return a
        try:
            r = api(server, "/api/domain", {"domain": a}, key=key)
        except ApiError as e:
            print("  %s" % e)
            if e.code == "bad_domain":
                continue
            sys.exit(1)
        # The server may tidy the name (https://, a path): use its spelling.
        new = [x["domain"] for x in r["sites"] if x["domain"] not in names]
        a = new[0] if new else a
        print("Added %s to the account." % a)
        return a


def cmd_setup(args, conf):
    need_root("setup")
    server = conf["server"]
    print("Bomvakt setup (%s)" % server)
    print()
    if read_key(args.key_file):
        if ask("This server already has a key. Replace it? (yes/no)", "no").lower() not in ("y", "yes", "j", "ja"):
            print("Kept the key. Check it with: sudo bomvakt status")
            return
        print()
    print("  1) Existing user: I have a key")
    print("  2) Create a new user")
    print()
    while True:
        choice = ask("Choose 1 or 2")
        if choice in ("1", "2"):
            break
        print("  Type 1 or 2.")
    print()
    if choice == "1":
        while True:
            key = ask("Key (bv_…)")
            if not KEY_RE.match(key):
                print("  That is not a Bomvakt key: bv_ followed by 40 characters.")
                continue
            try:
                me = api(server, "/api/me", key=key)
                break
            except ApiError as e:
                print("  %s" % e)
                if e.status not in (401, 403):
                    sys.exit(1)
        sites = me.get("sites", [])
        names = [s["domain"] for s in sites]
        print("Key accepted: account %s." % me["account"]["username"])
        domain = conf["domain"]
        if not domain or domain not in names:
            domain = pick_site(server, key, names)
    else:
        print("A new account. The username is how Bomvakt knows you; the email is")
        print("only used to reach you about the account, and can be left empty.")
        while True:
            user = ask("Username").lower()
            if USER_RE.match(user):
                break
            print("  3-32 characters: a-z, 0-9, dot, dash, underscore.")
        email = ask("Email (optional)")
        domain = ask("Website on this server (domain or IP address)", guess_domain()).lower()
        name = ask("Display name in your account (optional, Enter = the domain)")
        while True:
            if not domain:
                domain = ask("Website on this server (required)").lower()
                continue
            try:
                r = api(server, "/api/register", {"username": user, "email": email, "domain": domain, "name": name})
                break
            except ApiError as e:
                print("  %s" % e)
                if e.code == "username_taken" or e.code == "bad_username":
                    user = ask("Username").lower()
                elif e.code == "bad_email":
                    email = ask("Email (optional)")
                elif e.code == "bad_domain":
                    domain = ask("Website on this server (domain or IP address)").lower()
                else:
                    sys.exit(1)
        key = r["key"]
        domain = r["sites"][0]["domain"]
        print()
        print("Account created: %s" % r["account"]["username"])
        print()
        print("  Your key:  %s" % key)
        print()
        print("This key is the login to your account, and it is shown only this once.")
        print("Keep a copy somewhere safe: use it with `bomvakt setup` on your other")
        print("servers, so they join the same account.")
    write_key(args.key_file, key)
    set_conf_value(args.conf, "domain", domain)
    print()
    print("Saved the key in %s" % args.key_file)
    print("and domain = %s in %s." % (domain, args.conf))
    found = fix_default_log(args.conf, conf)
    if found:
        print("and logs = %s: this server has no %s." % (found, DEFAULT_LOGS))
    site = own_site(server, key, domain)
    if site:
        print()
        show_tag(server, site)
    print()
    print("More websites on this server, each with its own access log?")
    print("Run this for each; it shows the line to add:")
    print("  sudo bomvakt add-domain <domain>")
    print()
    print("To see it all at bomvakt.com/konto, set a password:")
    print("  sudo bomvakt password")
    conf = load_config(args.conf)
    if shared_groups(vhosts()) and not panel():
        print()
        print("\n".join(shared_log_notes(conf)[:-1]))
        if ask("Give each site its own log now? (yes/no)", "yes").lower() in ("y", "yes", "j", "ja"):
            print()
            cmd_logs(argparse.Namespace(undo=False, dry_run=False, yes=True, from_setup=True,
                                        conf=args.conf, key_file=args.key_file), conf)
    restart_service()


def cmd_add_domain(args, conf):
    key = read_key(args.key_file)
    if not key:
        need_root("add-domain")
        sys.exit("bomvakt: no key yet; run: sudo bomvakt setup")
    try:
        r = api(conf["server"], "/api/domain", {"domain": args.domain, "name": args.name or ""}, key=key)
    except ApiError as e:
        sys.exit("bomvakt: %s" % e)
    domain = args.domain.lower()
    print("Domains on the account: %s" % sites_line(r["sites"]))
    site = find_site(r["sites"], domain)
    if site:
        print()
        show_tag(conf["server"], site)
    print()
    if domain in conf["logs"].values():
        print("The agent already reads a log of its own for %s." % domain)
        return
    print("The server log: the agent sends nothing for %s yet. It reads only" % domain)
    print("the logs in %s, and their lines count for %s." % (args.conf, conf["domain"] or "the main domain"))
    print()
    if os.geteuid() == 0 and vhosts() and not panel():
        print("`sudo bomvakt logs` finds the site's log, shows each change and asks first.")
        if ask("Run it now? (yes/no)", "yes").lower() in ("y", "yes", "j", "ja"):
            print()
            cmd_logs(argparse.Namespace(undo=False, dry_run=False, yes=False,
                                        conf=args.conf, key_file=args.key_file), conf)
            return
        print("Later: sudo bomvakt logs")
        return
    print("Give it a log of its own, then add to %s:" % args.conf)
    print()
    print("  [logs]")
    print("  %s = %s" % (site_log_path(domain), domain))
    print()
    print("then: sudo systemctl restart bomvakt")
    print("How to give a site its own log: %s/konto/hjalp/sajtlogg" % conf["server"])


def find_site(sites, domain):
    """The site for a domain in an /api reply (www. or not), or None."""
    bare = domain[4:] if domain.startswith("www.") else domain
    for s in sites:
        if s["domain"] in (domain, bare, "www." + bare):
            return s
    return None


def own_site(server, key, domain):
    try:
        return find_site(api(server, "/api/me", key=key).get("sites", []), domain)
    except ApiError:
        return None


def show_tag(server, site):
    """The tracking code for a site's pages, and where the web panel has it."""
    print("For statistics on the people who visit %s, paste this into <head>" % site["domain"])
    print("on every page (no cookies, no consent banner):")
    print()
    print('  <script async src="%s/js/bv.min.js" data-site="%s"></script>' % (server, site["site_key"]))
    print()
    print("It is also under Integration: %s/konto/sajt/%d/integration" % (server, site["id"]))


def sites_line(sites):
    """example.com (Shop), other.se: the domains, with display names."""
    return ", ".join(s["domain"] + (" (%s)" % s["name"] if s.get("name") else "") for s in sites) or "none"


def cmd_name(args, conf):
    key = read_key(args.key_file)
    if not key:
        need_root("name")
        sys.exit("bomvakt: no key yet; run: sudo bomvakt setup")
    text = " ".join(args.text).strip()
    try:
        r = api(conf["server"], "/api/name", {"domain": args.domain, "name": text}, key=key)
    except ApiError as e:
        sys.exit("bomvakt: %s" % e)
    print("%s is now shown as %s." % (args.domain.lower(), text or "its domain"))
    print("Domains on the account: %s" % sites_line(r["sites"]))


def cmd_password(args, conf):
    """Set the password for the web login. The key proves the account is yours."""
    need_root("password")
    key = read_key(args.key_file)
    if not key:
        sys.exit("bomvakt: no key yet; run: sudo bomvakt setup")
    import getpass
    while True:
        try:
            pw = getpass.getpass("New password for bomvakt.com/konto (at least 8 characters): ")
            again = getpass.getpass("Same password again: ")
        except EOFError:
            print()
            sys.exit("bomvakt: no answer (no terminal); run: sudo bomvakt password")
        if pw != again:
            print("  The two passwords are not the same.")
            continue
        if len(pw) < 8:
            print("  At least 8 characters.")
            continue
        break
    reset = ask("Also turn off two-step login (lost phone)? (yes/no)", "no").lower() in ("y", "yes", "j", "ja")
    try:
        r = api(conf["server"], "/api/password", {"password": pw, "reset_2fa": reset}, key=key)
    except ApiError as e:
        sys.exit("bomvakt: %s" % e)
    print("Password set. Every web login of the account has been logged out.")
    if r.get("two_step_off"):
        print("Two-step login is off; turn it on again in your account settings: https://bomvakt.com/konto/profil")
    print("Log in at %s/konto as %s." % (conf["server"], r["username"]))


def cmd_rules(args, conf):
    """Fetch the rules and apply what this agent supports. --quiet (the
    timer) prints only changes and errors."""
    need_root("rules")
    say = (lambda m: None) if args.quiet else print
    if conf["rules"] == "off" and not args.dry_run:
        say("rules are turned off in %s (rules = off)" % args.conf)
        return
    key = read_key(args.key_file)
    if not key:
        sys.exit("bomvakt: no key yet; run: sudo bomvakt setup")
    st = load_rules_state()
    st["checked"] = int(time.time())
    etag = "" if args.force or args.dry_run else st.get("etag", "")
    try:
        reply, etag = fetch_rules(conf["server"], key, agent_domains(conf), etag)
    except ApiError as e:
        st["error"] = str(e)
        if not args.dry_run:
            save_rules_state(st)
        sys.exit("bomvakt: rules: %s" % e)
    if not args.dry_run:
        report_roots(conf, key, st)
    if reply is None:
        st.pop("error", None)
        save_rules_state(st)
        say("rules unchanged since the last check (%s)" % st.get("message", "nothing applied"))
        return
    st["robots"] = sorted(str(r.get("domain", "")) for r in reply.get("robots") or [])
    errors, changes, msgs = [], [], []
    try:
        changed, msg = apply_rate(reply, args.dry_run)
        msgs.append(msg.split("\n")[0])
        if changed:
            changes.append(msg)
    except RuleError as e:
        errors.append(str(e))
    done, errs = apply_robots(reply, args.dry_run)
    changes += done
    errors += errs
    n = len(reply.get("robots") or [])
    msgs.append("robots.txt: %d file%s" % (n, "" if n == 1 else "s") if n else "robots.txt: none")
    if args.dry_run:
        print("\n".join(changes + ["error: " + e for e in errors]) or "nothing would change")
        return
    st["message"] = "; ".join(msgs)
    if changes:
        st["changed"] = st["checked"]
    if errors:
        st["error"] = "; ".join(errors)
        st.pop("etag", None)   # try again next time
    else:
        st["etag"] = etag
        st.pop("error", None)
    save_rules_state(st)
    if changes:
        print("\n".join(changes))
    elif not errors:
        say(st["message"])
    if errors:
        sys.exit("bomvakt: rules: " + "\nbomvakt: rules: ".join(errors))

def cmd_status(args, conf):
    me = None
    print("bomvakt %s" % VERSION)
    print("server:  %s" % conf["server"])
    key = read_key(args.key_file)
    if not key and os.geteuid() != 0 and os.path.exists(args.key_file):
        print("key:     (run with sudo to read %s)" % args.key_file)
    elif not key:
        print("key:     none (run: sudo bomvakt setup)")
    else:
        print("key:     %s…" % key[:7])
        try:
            me = api(conf["server"], "/api/me", key=key)
            print("account: %s" % me["account"]["username"])
            print("domains: %s" % sites_line(me["sites"]))
            latest = me.get("agent_latest")
            if latest and newer(latest, VERSION):
                print("update:  %s is available (sudo bomvakt update)" % latest)
        except ApiError as e:
            print("account: %s" % e)
    print("logs:")
    for p, d in conf["logs"].items():
        ok = "readable" if os.access(p, os.R_OK) else ("missing" if not os.path.exists(p) else "not readable by you")
        if ok == "missing" and default_log() and default_log() not in conf["logs"]:
            ok = "missing; this server's web server log is %s" % default_log()
        print("  %s -> %s (%s)" % (p, d or "NO DOMAIN", ok))
    notes = shared_log_notes(conf)
    if notes:
        print("\n".join(notes))
    try:
        with open("/var/lib/bomvakt/state.json", encoding="utf-8") as fh:
            st = json.load(fh)
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st["last_ok"])) if st.get("last_ok") else "never"
        print("service: last send %s, %d lines sent since start, %d waiting" % (
            when, st.get("sent_since_start", 0), st.get("waiting", 0)))
        if st.get("last_error"):
            print("         last error: %s" % st["last_error"])
    except (OSError, ValueError, KeyError):
        pass
    if conf["rules"] == "off":
        print("rules:   off (rules = off in %s)" % args.conf)
    else:
        rs = load_rules_state()
        if rs.get("checked"):
            print("rules:   %s, checked %s" % (rs.get("message", "nothing applied yet"),
                                            time.strftime("%Y-%m-%d %H:%M", time.localtime(rs["checked"]))))
            if rs.get("error"):
                print("         last error: %s" % rs["error"].split("\n")[0])
            # robots.txt the agent could write but nobody turned on: say where.
            managed = set(rs.get("robots", []))
            ids = {s["domain"]: s["id"] for s in (me or {}).get("sites", [])}
            for d, r in sorted((rs.get("roots") or {}).items()):
                if d in managed or d not in ids:
                    continue
                print("robots:  %s not managed; suggested path %s/robots.txt," % (d, r["root"]))
                print("         turn it on at %s/konto/sajt/%s/robots" % (conf["server"], ids[d]))
        elif os.geteuid() != 0:
            print("rules:   (run with sudo to see)")
        else:
            print("rules:   not fetched yet (sudo bomvakt rules)")
    sys.stdout.flush()
    if os.path.isdir("/run/systemd/system"):
        os.system("systemctl is-active bomvakt >/dev/null 2>&1 && echo 'running: yes' || echo 'running: no'")


def newest_installer(tmp):
    """Download the newest install.sh into tmp; return its path, or None."""
    import subprocess
    path = os.path.join(tmp, "install.sh")
    req = urllib.request.Request(SRC_URL + "/install.sh", headers={"User-Agent": "bomvakt-linux/" + VERSION})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read(1 << 20)
    except (OSError, ValueError) as e:
        print("bomvakt: could not download the newest installer (%s)" % e)
        return None
    if not body.startswith(b"#!"):
        print("bomvakt: the downloaded installer does not look like a script")
        return None
    with open(path, "wb") as fh:
        fh.write(body)
    try:
        ok = subprocess.run(["sh", "-n", path]).returncode == 0
    except OSError:
        ok = False
    if not ok:
        print("bomvakt: the downloaded installer failed sh -n")
        return None
    return path


def cmd_update(args, conf):
    """Run the newest install.sh (so installer changes apply at once); the local copy if that fails."""
    import shutil
    import subprocess
    import tempfile
    need_root("update")
    tmp = tempfile.mkdtemp(prefix="bomvakt-update-")
    try:
        script = newest_installer(tmp)
        if script is None:
            if not os.path.exists(INSTALLER):
                sys.exit("bomvakt: %s is missing; run the installer again:\n"
                         "  curl -fsSL https://bomvakt.com/install.sh | sudo sh" % INSTALLER)
            print("bomvakt: using the installed copy, %s" % INSTALLER)
            script = INSTALLER
        sys.stdout.flush()
        rc = subprocess.run(["sh", script, "update"]).returncode
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(rc)


def cmd_parse(args, conf):
    """Show how lines are read: the first 20 parsed lines and a count of misses."""
    domain = conf["domain"] or "example.com"
    shown = bad = total = 0
    with open(args.file, "rb") as fh:
        for raw in fh:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if not line:
                continue
            total += 1
            row = parse_line(line, domain)
            if row is None:
                bad += 1
                if bad <= 5:
                    print("NOT READ: %s" % line[:200])
            elif shown < 20:
                shown += 1
                print(json.dumps(row, ensure_ascii=False))
    print("%d lines, %d not in the combined format" % (total, bad))


def main():
    ap = argparse.ArgumentParser(prog="bomvakt", description="Bomvakt agent for Linux")
    ap.add_argument("--conf", default=DEFAULT_CONF, help="config file (default %(default)s)")
    ap.add_argument("--key-file", default=DEFAULT_KEY, help="API key file (default %(default)s)")
    ap.add_argument("--version", action="version", version="bomvakt " + VERSION)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run", help="follow the logs and send them (what the service runs)")
    sub.add_parser("setup", help="create an account or use an existing key")
    p = sub.add_parser("add-domain", help="add a domain or IP address to the account")
    p.add_argument("domain")
    p.add_argument("--name", default="", help="display name in the account (default: the domain)")
    p = sub.add_parser("name", help="set a site's display name (no text = back to the domain)")
    p.add_argument("domain")
    p.add_argument("text", nargs="*", help="the name, e.g. My shop")
    sub.add_parser("password", help="set the web login password (and turn off two-step login)")
    sub.add_parser("status", help="account, domains, logs, last send")
    p = sub.add_parser("logs", help="give each website its own access log (shows the change, asks first)")
    p.add_argument("--dry-run", action="store_true", help="only show what would change")
    p.add_argument("--yes", action="store_true", help="do not ask (all sites)")
    p.add_argument("--undo", action="store_true", help="take the changes out again")
    p = sub.add_parser("rules", help="fetch the rules from bomvakt.com and apply them (nginx rate limits)")
    p.add_argument("--dry-run", action="store_true", help="only show what would change")
    p.add_argument("--force", action="store_true", help="fetch and compare even if nothing changed")
    p.add_argument("--quiet", action="store_true", help="print only changes and errors (the timer)")
    sub.add_parser("update", help="install the latest version")
    p = sub.add_parser("parse", help="show how a log file is read (sends nothing)")
    p.add_argument("file")
    args = ap.parse_args()

    # The journal adds its own timestamp.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    conf = load_config(args.conf)
    cmd = args.cmd or "run"
    if cmd == "run":
        run(conf, args.key_file, args.conf)
        return
    {"setup": cmd_setup, "add-domain": cmd_add_domain, "name": cmd_name, "password": cmd_password, "status": cmd_status, "logs": cmd_logs, "rules": cmd_rules,
     "update": cmd_update, "parse": cmd_parse}[cmd](args, conf)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        sys.exit(130)
