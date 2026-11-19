# Bomvakt for Linux

Bomvakt is the guard at the boom barrier. This is its agent for Linux
servers: a small service that reads your web server's access logs, so
[Bomvakt](https://bomvakt.com) can tell real visitors from bots, including
the ones that never run JavaScript.

### What it does

* Installs as one systemd service, `bomvakt`, with one command, and removes
  itself just as easily.
* Follows your nginx or Apache access logs the way `tail -F` does, through
  log rotation.
* Runs as a throwaway user that can read the logs and nothing else.
* Needs only Python 3.8+ and its standard library: no pip, no packages, no
  compiler.

### What it does not do

* **It sends nothing anywhere yet.** Version 0.1 only counts log lines and
  writes the count to the journal. This lets you check the install, the
  permissions and the log paths before any data leaves your server.
* It does not change your web server, firewall or other config.
* It does not need root once it is installed.

----

## Table of contents

* [How it works](#how-it-works)
  * [Following the logs](#following-the-logs)
  * [The service](#the-service)
  * [What the installer does](#what-the-installer-does)
* [Installation](#installation)
  * [Requirements](#requirements)
  * [One-line install](#one-line-install)
  * [Install from a checkout](#install-from-a-checkout)
* [Configuration](#configuration)
* [Usage](#usage)
* [Upgrading](#upgrading)
* [Uninstalling](#uninstalling)
* [Troubleshooting](#troubleshooting)
* [Development](#development)
* [License](#license)

----

## How it works

### Following the logs

Your web server writes one line per request to its access log. The agent
opens each log listed in the config and jumps to the end, so old lines are
skipped. It does not replay history. Once a second it reads whatever is new.

Logrotate moves or truncates the log every day or week. The agent notices
when a file has a new inode (moved and recreated) or became shorter than the
point it had read to (truncated). It reads the last lines of the old file,
then reopens the path and reads the new file from the start. No lines are
lost and none are counted twice.

If a log does not exist or cannot be read, the agent writes one warning and
keeps trying. It picks up the file as soon as it appears, so you can install
the agent before the web server.

Every `interval` seconds (default 60) it writes one line per log to the
journal:

```
bomvakt[812]: INFO lines in the last 60 s: /var/log/nginx/access.log 1342
```

### The service

`/etc/systemd/system/bomvakt.service` runs
`/usr/bin/python3 /usr/lib/bomvakt/bomvakt.py`. systemd does the
rest:

* **`DynamicUser=yes`**: systemd creates a user for the service when it
  starts and removes it when it stops. There is no account to log into.
* **`SupplementaryGroups=adm`**: on Debian and Ubuntu, web server logs belong
  to group `adm`. Membership gives read access to the logs, nothing else.
* **`ProtectSystem=strict`, `ProtectHome=yes`, `NoNewPrivileges=yes`,
  `CapabilityBoundingSet=`** and friends: the whole file system is read-only,
  home directories are hidden, and the process has no special rights. The
  only writable place is `/var/lib/bomvakt` (`StateDirectory`).
* **`Restart=on-failure`**: if it crashes, systemd starts it again after
  10 seconds.

You can check the protection yourself with
`systemd-analyze security bomvakt`.

### What the installer does

`install.sh` is a plain POSIX shell script, written one command per line so
you can read it, or run any line by hand. On `install` it:

1. checks that it runs as root on a system with systemd;
2. checks for Python 3.8+, and installs `python3` with `apt-get` or `dnf` if
   it is missing;
3. downloads `bomvakt.py`, `bomvakt.service`, `bomvakt.conf` and itself into
   a temporary directory, and checks that `bomvakt.py` compiles;
4. copies them into place (see below), keeping an existing config;
5. enables and starts the service, and shows the log if it fails to start.

These are the only paths it touches:

| Path | What | `uninstall` | `purge` |
|---|---|---|---|
| `/usr/lib/bomvakt/bomvakt.py` | the agent | removed | removed |
| `/usr/lib/bomvakt/install.sh` | the installer, for offline uninstall | removed | removed |
| `/etc/systemd/system/bomvakt.service` | the service | removed | removed |
| `/var/lib/bomvakt` | state | removed | removed |
| `/etc/bomvakt/bomvakt.conf` | your config | **kept** | removed |

----

## Installation

### Requirements

* Linux with systemd: Debian, Ubuntu, RHEL, AlmaLinux, Rocky Linux,
  Raspberry Pi OS and most others.
* Python 3.8 or newer (installed for you on apt and dnf systems).
* `curl` or `wget`.
* Root, for the install only.

### One-line install

Piping a script into a root shell asks for trust, so read it first:

```sh
curl -fsSL https://bomvakt.com/install.sh | less
```

Then install:

```sh
curl -fsSL https://bomvakt.com/install.sh | sudo sh
```

`https://bomvakt.com/install.sh` is a redirect to `install.sh` in this
repository, so what you read here is what you run.

### Install from a checkout

```sh
git clone https://github.com/torvanger/bomvakt-linux.git
cd bomvakt-linux
sudo sh install.sh
```

When `install.sh` finds the other files beside it, it uses them instead of
downloading. To download from a fork or branch instead, set `BOMVAKT_SRC` to
the raw URL of its folder:

```sh
curl -fsSL https://bomvakt.com/install.sh | sudo BOMVAKT_SRC=https://raw.githubusercontent.com/you/bomvakt-linux/main sh
```

----

## Configuration

`/etc/bomvakt/bomvakt.conf`:

```ini
[bomvakt]
# Access logs to follow, comma-separated.
logs = /var/log/nginx/access.log

# Seconds between summaries in the journal (minimum 10).
interval = 60
```

Common log paths:

| Web server | Debian / Ubuntu | RHEL / Alma / Rocky |
|---|---|---|
| nginx | `/var/log/nginx/access.log` | `/var/log/nginx/access.log` |
| Apache | `/var/log/apache2/access.log` | `/var/log/httpd/access_log` |

Several sites with their own logs: list them all, separated by commas.
After a change:

```sh
sudo systemctl restart bomvakt
```

----

## Usage

```sh
systemctl status bomvakt          # running?
journalctl -u bomvakt -f          # follow its log
sudo systemctl restart bomvakt    # after editing the config
python3 /usr/lib/bomvakt/bomvakt.py --version
```

----

## Upgrading

Run the install again. It replaces the agent and the service, keeps your
config, and restarts the service:

```sh
curl -fsSL https://bomvakt.com/install.sh | sudo sh
```

----

## Uninstalling

```sh
curl -fsSL https://bomvakt.com/install.sh | sudo sh -s -- uninstall
```

This stops and removes the service and everything in the table above except
your config. To remove the config too, use `purge` instead of `uninstall`.

Without network access, use the copy the installer left behind:

```sh
sudo /usr/lib/bomvakt/install.sh uninstall
```

----

## Troubleshooting

**`cannot read /var/log/...: Permission denied`**: the log is not readable
by group `adm`. See who owns it with `ls -l /var/log/nginx/`. On Debian and
Ubuntu, logrotate creates new logs as `www-data adm 0640`. If yours differ,
fix the `create` line in `/etc/logrotate.d/nginx` (or `apache2`/`httpd`).

**`cannot read /var/log/...: No such file or directory`**: wrong path in the
config, or the web server has not written a log yet. See
[Configuration](#configuration).

**The service does not start**: the installer prints the last 20 log lines.
Later: `journalctl -u bomvakt -n 50`.

**`needs systemd`**: containers and some minimal systems run without
systemd. Run the agent directly instead:
`python3 bomvakt.py --conf bomvakt.conf`.

----

## Development

Everything is in four files: `install.sh`, `bomvakt.py`, `bomvakt.service`
and `bomvakt.conf`. Run the agent in the foreground against any file, no
root needed:

```sh
printf '[bomvakt]\nlogs = /tmp/test.log\ninterval = 10\n' > /tmp/test.conf
touch /tmp/test.log
python3 bomvakt.py --conf /tmp/test.conf
```

In another terminal, `echo hello >> /tmp/test.log`. Stop it with Ctrl-C.

Checks before a commit:

```sh
sh -n install.sh
python3 -m py_compile bomvakt.py
systemd-analyze verify bomvakt.service
```

----

## License

MIT, see [`LICENSE`](LICENSE).
