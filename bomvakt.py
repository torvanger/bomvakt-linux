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
    bomvakt status             account, domains, logs, last send
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
import signal
import socket
import sys
import time
import urllib.error
import urllib.request

VERSION = "0.4.1"
DEFAULT_CONF = "/etc/bomvakt/bomvakt.conf"
DEFAULT_KEY = "/etc/bomvakt/key"
DEFAULT_SERVER = "https://bomvakt.com"
DEFAULT_LOGS = "/var/log/nginx/access.log"
INSTALLER = "/usr/lib/bomvakt/install.sh"

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
    return {"logs": logs, "domain": domain, "interval": interval, "server": server.rstrip("/")}


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


def run(conf, key_file):
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


# ------------------------------------------------------------------ commands

def ask(prompt, default=""):
    shown = "%s [%s]: " % (prompt, default) if default else "%s: " % prompt
    try:
        v = input(shown).strip()
    except EOFError:
        print()
        sys.exit("bomvakt: no answer (no terminal); run: sudo bomvakt setup")
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
        print("Restarted the bomvakt service. Follow it with: journalctl -u bomvakt -f")


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
    print("Saved the key in %s and domain = %s in %s." % (args.key_file, domain, args.conf))
    print("More websites on this server, each with its own access log? Run")
    print("sudo bomvakt add-domain <domain> for each; it shows the line to add.")
    print("To see it all at bomvakt.com/konto, set a password: sudo bomvakt password")
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
    print("Domains on the account: %s" % sites_line(r["sites"]))
    print("To send a log of its own for it, add to %s:" % args.conf)
    print()
    print("  [logs]")
    print("  /var/log/nginx/%s.access.log = %s" % (args.domain.lower(), args.domain.lower()))
    print()
    print("then: sudo systemctl restart bomvakt")


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
        print("Two-step login is off; turn it on again under Inställningar at bomvakt.com/konto.")
    print("Log in at %s/konto as %s." % (conf["server"], r["username"]))


def cmd_status(args, conf):
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
        print("  %s -> %s (%s)" % (p, d or "NO DOMAIN", ok))
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
    sys.stdout.flush()
    if os.path.isdir("/run/systemd/system"):
        os.system("systemctl is-active bomvakt >/dev/null 2>&1 && echo 'running: yes' || echo 'running: no'")


def cmd_update(args, conf):
    need_root("update")
    if not os.path.exists(INSTALLER):
        sys.exit("bomvakt: %s is missing; run the installer again:\n"
                 "  curl -fsSL https://bomvakt.com/install.sh | sudo sh" % INSTALLER)
    os.execv("/bin/sh", ["sh", INSTALLER, "update"])


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
    sub.add_parser("update", help="install the latest version")
    p = sub.add_parser("parse", help="show how a log file is read (sends nothing)")
    p.add_argument("file")
    args = ap.parse_args()

    # The journal adds its own timestamp.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    conf = load_config(args.conf)
    cmd = args.cmd or "run"
    if cmd == "run":
        run(conf, args.key_file)
        return
    {"setup": cmd_setup, "add-domain": cmd_add_domain, "name": cmd_name, "password": cmd_password, "status": cmd_status,
     "update": cmd_update, "parse": cmd_parse}[cmd](args, conf)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        sys.exit(130)
