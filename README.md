# Bomvakt for Linux

Bomvakt is the guard at the boom barrier. This is its agent for Linux
servers: a small service that reads your web server's access logs and sends
them to [Bomvakt](https://bomvakt.com), so you can see who really hits your
server, including the bots that never run JavaScript, and decide what to
ban.

### What it does

* Installs as one systemd service, `bomvakt`, with one command, and removes
  itself just as easily.
* On first install, creates your Bomvakt account (username, optional email,
  the domain or IP address to protect) and stores the account's key.
* Follows your nginx or Apache access logs the way `tail -F` does, through
  log rotation, and sends every line to Bomvakt over HTTPS, in compressed
  batches every few seconds.
* Keeps lines while Bomvakt cannot be reached, and after a restart carries
  on where it stopped, so updates lose nothing.
* Runs as a throwaway user that can read the logs and nothing else.
* Needs only Python 3.8+ and its standard library: no pip, no packages, no
  compiler.

### What it sends

For each line of the access log: the time, the client IP address, the
request (method, path with query string, protocol), the status code, the
response size, the referrer and the user agent, plus the domain it belongs
to. Nothing else from your server: no other files, no system details beyond
the host name. Bomvakt keeps the lines for 30 days.

The IP address and the full request are exactly what you need to decide
what to ban, and they are personal data under the GDPR. Bomvakt processes
them on your behalf, to protect your server.

### What it does not do

* It does not change your web server, firewall or other config. (Banning
  comes later, and only when you turn it on.)
* It does not need root once it is installed.
* It does not update itself. You decide when: `sudo bomvakt update`.

----

## Table of contents

* [How it works](#how-it-works)
  * [Your account and key](#your-account-and-key)
  * [Following the logs](#following-the-logs)
  * [Sending](#sending)
  * [The service](#the-service)
  * [What the installer does](#what-the-installer-does)
* [Installation](#installation)
  * [Requirements](#requirements)
  * [One-line install](#one-line-install)
  * [Install from a checkout](#install-from-a-checkout)
* [Configuration](#configuration)
* [Usage](#usage)
* [Updating](#updating)
* [Uninstalling](#uninstalling)
* [Troubleshooting](#troubleshooting)
* [Development](#development)
* [License](#license)

----

## How it works

### Your account and key

The first install asks whether you already have an account:

```
  1) Existing user: I have a key
  2) Create a new user

Choose 1 or 2: 2
Username: kim
Email (optional): kim@example.com
Website on this server (domain or IP address) [web1.example.com]: example.com
```

Bomvakt creates the account and answers with its key, `bv_` followed by 40
characters. The key is the login to your account and is shown only once:
keep a copy. It is saved in `/etc/bomvakt/key` (readable by root only) and
handed to the service by systemd, so the service's own user never reads the
file.

One account holds many domains and servers. On your next server, run the
installer, choose 1 and paste the key, instead of creating a new account.
Add more domains with `sudo bomvakt add-domain shop.example.com`.
[SETUP-EXAMPLES.md](SETUP-EXAMPLES.md) shows what setup says in every case.

A domain covers its subdomains: lines for `www.example.com` and
`shop.example.com` count for `example.com`. A server without a domain name
can be registered by its IP address.

Setup asks which website runs on the server because a line in the usual
access log format has no domain in it: the agent tags every line of the log
with that site. A server with several sites, each with its own log, maps
them under `[logs]` in `bomvakt.conf` (`sudo bomvakt add-domain` prints the
line). With Apache's `vhost_combined` format the site is read from each
line instead, so one shared log works too.

Most servers write every site to one shared `access.log`. `bomvakt status`
and `setup` read the enabled nginx and Apache site configs and say when
several sites share a log, or when a site's own log is not followed.
`sudo bomvakt logs` fixes it the way certbot edits a config:

1. lists the sites that share a log and asks which to split (all by default);
2. shows every change as a diff and asks once more;
3. backs up the files to `/var/lib/bomvakt/backup/`;
4. adds `access_log …/<site>.access.log;` (nginx) or `CustomLog … combined`
   (Apache) after `server_name`/`ServerName` in each of the site's blocks,
   and comments out the block's line to the shared log (both servers write
   to every log line they have), each change marked `# bomvakt-…`;
5. runs `nginx -t` or `apachectl configtest`, and puts every file back and
   reloads nothing if it fails;
6. reloads the web server, maps the new logs under `[logs]`, adds them to
   the `bomvakt-bots` fail2ban jail if there is one, and restarts the agent.

`--dry-run` only shows the changes; `--undo` takes them out again (the log
files are kept). It leaves alone files written by a tool ("managed by
Ansible", "do not edit"), one-line blocks and servers run by a hosting panel
(Plesk, cPanel, Virtualmin, ISPConfig, DirectAdmin): add the line by hand
there. The distro's logrotate rule already covers new files in the log
folder.

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

The agent reads the `combined` log format, the default of both nginx and
Apache, and Apache's `vhost_combined` (`other_vhosts_access.log`), where the
host name at the start of each line decides the domain. Check what it makes
of your log with `bomvakt parse /var/log/nginx/access.log`: it shows the
first lines as they would be sent, and counts lines it cannot read.

### Sending

Lines go out every 5 seconds, or sooner when 2,000 are waiting, as one
gzip-compressed HTTPS request to `https://bomvakt.com/api/ingest` with your
key.

* **Bomvakt cannot be reached:** lines wait in memory (up to 200,000) and
  the agent retries after 5, 10, 20 … up to 300 seconds.
* **Restart or update:** after each successful send the agent writes how
  far it has read each log to `/var/lib/bomvakt/state.json`. When it starts
  again it carries on from there, so lines written while it was stopped are
  sent too (up to 100 MB of backlog; more than that, it starts at the end).
  Before the first send it starts at the end of each log: no history.
* **A domain that is not on your account:** its lines are dropped, with one
  warning in the journal naming the domain.
* **The key is refused:** one error in the journal; it tries again every 5
  minutes.

Every `interval` seconds (default 300) it writes a summary to the journal:

```
bomvakt[812]: INFO last 300 s: lines /var/log/nginx/access.log 6710; sent 6710, waiting 0
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
* **`LoadCredential=key:/etc/bomvakt/key`**: systemd reads the root-only
  key file and gives the service a private copy. (systemd older than 247,
  e.g. RHEL 8, lacks this: there the installer makes the key file readable
  by group `adm` instead.)
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
3. downloads `bomvakt.py`, `bomvakt.service`, `bomvakt.conf`, the manual
   `bomvakt.1` and itself into
   a temporary directory, and checks that `bomvakt.py` compiles;
4. copies them into place (see below), keeping an existing config and key;
5. enables and starts the service, and shows the log if it fails to start;
6. if this server has no key yet, runs `bomvakt setup` (account and key).
   With no terminal to ask on (e.g. from a provisioning tool), or with
   `BOMVAKT_NO_SETUP=1`, it skips this and tells you to run it later.

`update` does steps 1 to 5 and never asks anything.

These are the only paths it touches:

| Path | What | `uninstall` | `purge` |
|---|---|---|---|
| `/usr/lib/bomvakt/bomvakt.py` | the agent | removed | removed |
| `/usr/lib/bomvakt/install.sh` | the installer, for update and offline uninstall | removed | removed |
| `/usr/local/bin/bomvakt` | link to the agent, the `bomvakt` command | removed | removed |
| `/etc/systemd/system/bomvakt.service` | the service | removed | removed |
| `/usr/local/share/man/man1/bomvakt.1` | the manual, `man bomvakt` | removed | removed |
| `/var/lib/bomvakt` | state: how far each log was read | removed | removed |
| `/etc/bomvakt/bomvakt.conf` | your config | **kept** | removed |
| `/etc/bomvakt/key` | your account's key | **kept** | removed |

----

## Installation

### Requirements

* Linux with systemd: Debian, Ubuntu, RHEL, AlmaLinux, Rocky Linux,
  Raspberry Pi OS and most others.
* Python 3.8 or newer (installed for you on apt and dnf systems).
* `curl` or `wget`.
* Root, for the install only.
* Outgoing HTTPS (port 443) to `bomvakt.com`.

### One-line install

Piping a script into a root shell asks for trust, so read it first:

```sh
curl -fsSL https://bomvakt.com/install.sh | less
```

Then install:

```sh
curl -fsSL https://bomvakt.com/install.sh | sudo sh
```

It installs, starts the service, and then asks whether you are an existing
user (paste your key) or a new one (username, optional email, the domain to
protect).

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
# The domain (or IP address) these logs belong to. `bomvakt setup` fills it in.
domain = example.com

# Access logs to follow, comma-separated. Their lines are sent as `domain`.
logs = /var/log/nginx/access.log

# Seconds between summaries in the journal (minimum 10).
interval = 300

# Where to send. Leave as is.
server = https://bomvakt.com
```

One log per site: list each under `[logs]` with its own domain (add the
domains to your account first with `sudo bomvakt add-domain`):

```ini
[logs]
/var/log/nginx/shop.access.log = shop.example.com
/var/log/nginx/blog.access.log = blog.example.com
```

Common log paths:

| Web server | Debian / Ubuntu | RHEL / Alma / Rocky |
|---|---|---|
| nginx | `/var/log/nginx/access.log` | `/var/log/nginx/access.log` |
| Apache | `/var/log/apache2/access.log` | `/var/log/httpd/access_log` |

After a change:

```sh
sudo systemctl restart bomvakt
```

----

## Usage

```sh
sudo bomvakt status               # account, domains, logs, shared logs, last send
sudo bomvakt logs                 # give each site its own log (--dry-run, --undo)
sudo bomvakt setup                # create an account, or switch to another key
sudo bomvakt add-domain shop.example.com --name "Webshop"
sudo bomvakt name shop.example.com Webshop   # display name; no name = the domain
sudo bomvakt password             # web login password; can also turn off two-step login (lost phone)
bomvakt parse /var/log/nginx/access.log    # how lines are read; sends nothing
journalctl -u bomvakt -f          # follow its log
sudo systemctl restart bomvakt    # after editing the config
man bomvakt                       # the manual
bomvakt --version
```

----

## Updating

```sh
sudo bomvakt update
```

It downloads the newest installer and runs it, so changes to the installer
apply on the first update (if the download fails, the installed copy in
`/usr/lib/bomvakt` is used). The installer downloads the latest version,
replaces the agent and the service, keeps your config and key, and restarts
the service. It never asks anything, so
it is safe in scripts. The agent picks up from where it stopped reading: no
lines are lost. When a newer version exists, the agent says so once in the
journal and in `bomvakt status`.

The same, without the `bomvakt` command (e.g. from version 0.1):

```sh
curl -fsSL https://bomvakt.com/install.sh | sudo sh -s -- update
```

----

## Uninstalling

```sh
curl -fsSL https://bomvakt.com/install.sh | sudo sh -s -- uninstall
```

This stops and removes the service and everything in the table above except
your config and key. To remove those too, use `purge` instead of
`uninstall`. Your account and the lines already sent stay at Bomvakt.

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

**`lines for shop.example.com are dropped: it is not on your account`**: the
log has lines for a domain your account does not have. Add it with
`sudo bomvakt add-domain shop.example.com`, or fix `domain` in the config.

**`the server does not accept the key`**: the key in `/etc/bomvakt/key` is
wrong or was replaced. Run `sudo bomvakt setup` and paste the right one.

**`some lines are not in the combined log format`**: your web server uses
another `log_format`. Run `bomvakt parse` on the log to see which lines.
Switch the `access_log` back to `combined` (nginx) or `CustomLog … combined`
(Apache).

**`send failed … retry in`**: Bomvakt could not be reached. The lines wait
and are sent when it answers again. Check that the server can open
`https://bomvakt.com` (`curl -I https://bomvakt.com`).

**The service does not start**: the installer prints the last 20 log lines.
Later: `journalctl -u bomvakt -n 50`.

**`needs systemd`**: containers and some minimal systems run without
systemd. Run the agent directly instead:
`python3 bomvakt.py --conf bomvakt.conf --key-file key run`.

----

## Development

Everything is in five files: `install.sh`, `bomvakt.py`, `bomvakt.service`,
`bomvakt.conf` and the manual `bomvakt.1` (read it in place with
`man ./bomvakt.1`; keep it in step with the commands). Run the agent in the foreground against any file, no
root needed:

```sh
printf '[bomvakt]\ndomain = example.com\nlogs = /tmp/test.log\ninterval = 10\n' > /tmp/test.conf
touch /tmp/test.log
python3 bomvakt.py --conf /tmp/test.conf --key-file /tmp/test.key run
```

Without a key it reads and counts but sends nothing. In another terminal,
append a line in the combined format to `/tmp/test.log`. Stop it with
Ctrl-C. To send to a local Bomvakt, set `server = http://localhost:8080` in
the config (or `BOMVAKT_SERVER=…`), put a key in `/tmp/test.key`, and set
`STATE_DIRECTORY=/tmp` to test resuming.

Checks before a commit:

```sh
sh -n install.sh
python3 -m py_compile bomvakt.py
systemd-analyze verify bomvakt.service
```

----

## License

MIT, see [`LICENSE`](LICENSE).
