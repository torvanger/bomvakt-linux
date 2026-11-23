# Setup examples

What `sudo bomvakt setup` (and the first install) shows in every case, with
made-up answers. The keys and names here are examples. The server's own name
is `web1.example.com`, which setup suggests as the domain; Enter takes the
suggestion in brackets. The display name is optional: it is what the site is
called in the account at bomvakt.com, and Enter leaves it as the domain.
Change it later with `sudo bomvakt name <domain> <name>`.

The account `kim` (key `bv_3f9a…7d2e`) already exists with `example.com` and
`shop.example.com`; another account holds `shared.example.net`.

Why setup asks which website runs on the server: a line in the usual access
log format has no domain in it, so the agent tags every line of the log with
that site. A server with several sites, each with its own log, maps them in
`[logs]` in `bomvakt.conf` (`sudo bomvakt add-domain` shows the line); with
Apache's `vhost_combined` format the site is read from each line instead.

## Contents

1. [New user, everything right the first time](#new-user-everything-right-the-first-time)
2. [New user, typing mistakes](#new-user-typing-mistakes)
3. [New user, a domain another account already has](#new-user-a-domain-another-account-already-has)
4. [New user with an IP address and no email](#new-user-with-an-ip-address-and-no-email)
5. [New user, too many sign-ups from this address](#new-user-too-many-sign-ups-from-this-address)
6. [Existing user, the domain is already on the account](#existing-user-the-domain-is-already-on-the-account)
7. [Existing user, a new server with a new domain](#existing-user-a-new-server-with-a-new-domain)
8. [Existing user, wrong key and a bad domain](#existing-user-wrong-key-and-a-bad-domain)
9. [Existing user, config already names a domain on the account](#existing-user-config-already-names-a-domain-on-the-account)
10. [This server already has a key: keep it](#this-server-already-has-a-key-keep-it)
11. [This server already has a key: replace it](#this-server-already-has-a-key-replace-it)
12. [Bomvakt cannot be reached](#bomvakt-cannot-be-reached)
13. [Not run as root](#not-run-as-root)
14. [No terminal to answer in](#no-terminal-to-answer-in)
15. [Several sites share one log](#several-sites-share-one-log)
16. [Check first with logs --dry-run](#check-first-with-logs---dry-run)

## New user, everything right the first time

Choose 2, a username, an email, Enter to take the suggested domain (the server's own name), and a display name for the account's site list.

```console
Bomvakt setup (https://bomvakt.com)

  1) Existing user: I have a key
  2) Create a new user

Choose 1 or 2: 2

A new account. The username is how Bomvakt knows you; the email is
only used to reach you about the account, and can be left empty.
Username: anna
Email (optional): anna@example.org
Website on this server (domain or IP address) [web1.example.com]: 
Display name in your account (optional, Enter = the domain): Annas webb

Account created: anna

  Your key:  bv_8c41e07b5d2a96f3e1b07c4d9a28f5e6b3c1d704

This key is the login to your account, and it is shown only this once.
Keep a copy somewhere safe: use it with `bomvakt setup` on your other
servers, so they join the same account.

Saved the key in /etc/bomvakt/key and domain = web1.example.com in /etc/bomvakt/bomvakt.conf.
More websites on this server, each with its own access log? Run
sudo bomvakt add-domain <domain> for each; it shows the line to add.
To see it all at bomvakt.com/konto, set a password: sudo bomvakt password
Restarted the bomvakt service. Follow it with: journalctl -u bomvakt -f
```

Afterwards: `/etc/bomvakt/key` = `bv_8c41e07b5d2a96f3e1b07c4d9a28f5e6b3c1d704`, `domain = web1.example.com`

## New user, typing mistakes

Every check in a row. The agent checks the menu choice and the username's form at once; Bomvakt checks the rest after the domain question and asks again for just the field that was wrong: a reserved name, a bad email, a name that is taken, a domain that is not a domain. A URL is accepted and tidied to its host name.

```console
Bomvakt setup (https://bomvakt.com)

  1) Existing user: I have a key
  2) Create a new user

Choose 1 or 2: 3
  Type 1 or 2.
Choose 1 or 2: 2

A new account. The username is how Bomvakt knows you; the email is
only used to reach you about the account, and can be left empty.
Username: al
  3-32 characters: a-z, 0-9, dot, dash, underscore.
Username: admin
Email (optional): anna@
Website on this server (domain or IP address) [web1.example.com]: localhost
Display name in your account (optional, Enter = the domain): 
  The username admin is reserved.
Username: kim
  That does not look like an email address.
Email (optional): anna@example.org
  Give a domain (example.com) or an IP address (203.0.113.7).
Website on this server (domain or IP address): https://anna.example.org/
  The username kim is taken.
Username: anna

Account created: anna

  Your key:  bv_8c41e07b5d2a96f3e1b07c4d9a28f5e6b3c1d704

This key is the login to your account, and it is shown only this once.
Keep a copy somewhere safe: use it with `bomvakt setup` on your other
servers, so they join the same account.

Saved the key in /etc/bomvakt/key and domain = anna.example.org in /etc/bomvakt/bomvakt.conf.
More websites on this server, each with its own access log? Run
sudo bomvakt add-domain <domain> for each; it shows the line to add.
To see it all at bomvakt.com/konto, set a password: sudo bomvakt password
Restarted the bomvakt service. Follow it with: journalctl -u bomvakt -f
```

Afterwards: `/etc/bomvakt/key` = `bv_8c41e07b5d2a96f3e1b07c4d9a28f5e6b3c1d704`, `domain = anna.example.org`

## New user, a domain another account already has

Allowed: each account gets its own copy of the domain, and only sees the lines its own agents send. Whether the lines really come from that domain is checked on the traffic, not at sign-up.

```console
Bomvakt setup (https://bomvakt.com)

  1) Existing user: I have a key
  2) Create a new user

Choose 1 or 2: 2

A new account. The username is how Bomvakt knows you; the email is
only used to reach you about the account, and can be left empty.
Username: anna
Email (optional): 
Website on this server (domain or IP address) [web1.example.com]: shared.example.net
Display name in your account (optional, Enter = the domain): 

Account created: anna

  Your key:  bv_8c41e07b5d2a96f3e1b07c4d9a28f5e6b3c1d704

This key is the login to your account, and it is shown only this once.
Keep a copy somewhere safe: use it with `bomvakt setup` on your other
servers, so they join the same account.

Saved the key in /etc/bomvakt/key and domain = shared.example.net in /etc/bomvakt/bomvakt.conf.
More websites on this server, each with its own access log? Run
sudo bomvakt add-domain <domain> for each; it shows the line to add.
To see it all at bomvakt.com/konto, set a password: sudo bomvakt password
Restarted the bomvakt service. Follow it with: journalctl -u bomvakt -f
```

Afterwards: `/etc/bomvakt/key` = `bv_8c41e07b5d2a96f3e1b07c4d9a28f5e6b3c1d704`, `domain = shared.example.net`

## New user with an IP address and no email

A server without a domain name: the IP is what Bomvakt protects. Email left empty.

```console
Bomvakt setup (https://bomvakt.com)

  1) Existing user: I have a key
  2) Create a new user

Choose 1 or 2: 2

A new account. The username is how Bomvakt knows you; the email is
only used to reach you about the account, and can be left empty.
Username: anna
Email (optional): 
Website on this server (domain or IP address) [web1.example.com]: 203.0.113.7
Display name in your account (optional, Enter = the domain): 

Account created: anna

  Your key:  bv_8c41e07b5d2a96f3e1b07c4d9a28f5e6b3c1d704

This key is the login to your account, and it is shown only this once.
Keep a copy somewhere safe: use it with `bomvakt setup` on your other
servers, so they join the same account.

Saved the key in /etc/bomvakt/key and domain = 203.0.113.7 in /etc/bomvakt/bomvakt.conf.
More websites on this server, each with its own access log? Run
sudo bomvakt add-domain <domain> for each; it shows the line to add.
To see it all at bomvakt.com/konto, set a password: sudo bomvakt password
Restarted the bomvakt service. Follow it with: journalctl -u bomvakt -f
```

Afterwards: `/etc/bomvakt/key` = `bv_8c41e07b5d2a96f3e1b07c4d9a28f5e6b3c1d704`, `domain = 203.0.113.7`

## New user, too many sign-ups from this address

Bomvakt allows 5 sign-ups per IP address and hour. Setup stops; nothing is saved.

```console
Bomvakt setup (https://bomvakt.com)

  1) Existing user: I have a key
  2) Create a new user

Choose 1 or 2: 2

A new account. The username is how Bomvakt knows you; the email is
only used to reach you about the account, and can be left empty.
Username: anna
Email (optional): 
Website on this server (domain or IP address) [web1.example.com]: 
Display name in your account (optional, Enter = the domain): 
  Too many sign-ups from this address. Try again in an hour.
```

Afterwards: nothing saved, the service keeps waiting for a key.

## Existing user, the domain is already on the account

Choose 1 and paste the key. The account has example.com and shop.example.com; setup lists them and this server runs the second, so: 2.

```console
Bomvakt setup (https://bomvakt.com)

  1) Existing user: I have a key
  2) Create a new user

Choose 1 or 2: 1

Key (bv_…): bv_3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c7d2e
Key accepted: account kim.

Which website runs on this server? Its access log is sent as that site.
  1) example.com
  2) shop.example.com
  or type another domain or IP address to add it to the account
Choose 1-2 or type a domain [1]: 2

Saved the key in /etc/bomvakt/key and domain = shop.example.com in /etc/bomvakt/bomvakt.conf.
More websites on this server, each with its own access log? Run
sudo bomvakt add-domain <domain> for each; it shows the line to add.
To see it all at bomvakt.com/konto, set a password: sudo bomvakt password
Restarted the bomvakt service. Follow it with: journalctl -u bomvakt -f
```

Afterwards: `/etc/bomvakt/key` = `bv_3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c7d2e`, `domain = shop.example.com`

## Existing user, a new server with a new domain

The key is pasted on a server that runs a site the account does not have yet: type its domain instead of a number, and it is added.

```console
Bomvakt setup (https://bomvakt.com)

  1) Existing user: I have a key
  2) Create a new user

Choose 1 or 2: 1

Key (bv_…): bv_3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c7d2e
Key accepted: account kim.

Which website runs on this server? Its access log is sent as that site.
  1) example.com
  2) shop.example.com
  or type another domain or IP address to add it to the account
Choose 1-2 or type a domain [1]: blog.example.com
Added blog.example.com to the account.

Saved the key in /etc/bomvakt/key and domain = blog.example.com in /etc/bomvakt/bomvakt.conf.
More websites on this server, each with its own access log? Run
sudo bomvakt add-domain <domain> for each; it shows the line to add.
To see it all at bomvakt.com/konto, set a password: sudo bomvakt password
Restarted the bomvakt service. Follow it with: journalctl -u bomvakt -f
```

Afterwards: `/etc/bomvakt/key` = `bv_3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c7d2e`, `domain = blog.example.com`

## Existing user, wrong key and a bad domain

A cut-off paste, then a key Bomvakt does not know, then the right one. Then a number that is not on the list, something that is not a domain, and Enter for the suggestion.

```console
Bomvakt setup (https://bomvakt.com)

  1) Existing user: I have a key
  2) Create a new user

Choose 1 or 2: 1

Key (bv_…): bv_3f9a1c3f9a
  That is not a Bomvakt key: bv_ followed by 40 characters.
Key (bv_…): bv_0000000000000000000000000000000000000000
  missing or unknown API key
Key (bv_…): bv_3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c7d2e
Key accepted: account kim.

Which website runs on this server? Its access log is sent as that site.
  1) example.com
  2) shop.example.com
  or type another domain or IP address to add it to the account
Choose 1-2 or type a domain [1]: 7
  Pick a number from 1 to 2, or type a domain.
Choose 1-2 or type a domain [1]: localhost
  Give a domain (example.com) or an IP address (203.0.113.7).
Choose 1-2 or type a domain [1]: 

Saved the key in /etc/bomvakt/key and domain = example.com in /etc/bomvakt/bomvakt.conf.
More websites on this server, each with its own access log? Run
sudo bomvakt add-domain <domain> for each; it shows the line to add.
To see it all at bomvakt.com/konto, set a password: sudo bomvakt password
Restarted the bomvakt service. Follow it with: journalctl -u bomvakt -f
```

Afterwards: `/etc/bomvakt/key` = `bv_3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c7d2e`, `domain = example.com`

## Existing user, config already names a domain on the account

bomvakt.conf already has domain = shop.example.com, which is on the account, so there is no domain question.

```console
Bomvakt setup (https://bomvakt.com)

  1) Existing user: I have a key
  2) Create a new user

Choose 1 or 2: 1

Key (bv_…): bv_3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c7d2e
Key accepted: account kim.

Saved the key in /etc/bomvakt/key and domain = shop.example.com in /etc/bomvakt/bomvakt.conf.
More websites on this server, each with its own access log? Run
sudo bomvakt add-domain <domain> for each; it shows the line to add.
To see it all at bomvakt.com/konto, set a password: sudo bomvakt password
Restarted the bomvakt service. Follow it with: journalctl -u bomvakt -f
```

Afterwards: `/etc/bomvakt/key` = `bv_3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c7d2e`, `domain = shop.example.com`

## This server already has a key: keep it

Running setup again. The default answer (no) leaves everything as it was.

```console
Bomvakt setup (https://bomvakt.com)

This server already has a key. Replace it? (yes/no) [no]: 
Kept the key. Check it with: sudo bomvakt status
```

Afterwards: `/etc/bomvakt/key` = `bv_3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c7d2e`, `domain = example.com`

## This server already has a key: replace it

Moving the server to another account: yes, then a new sign-up.

```console
Bomvakt setup (https://bomvakt.com)

This server already has a key. Replace it? (yes/no) [no]: yes

  1) Existing user: I have a key
  2) Create a new user

Choose 1 or 2: 2

A new account. The username is how Bomvakt knows you; the email is
only used to reach you about the account, and can be left empty.
Username: anna
Email (optional): 
Website on this server (domain or IP address) [web1.example.com]: anna.example.org
Display name in your account (optional, Enter = the domain): 

Account created: anna

  Your key:  bv_8c41e07b5d2a96f3e1b07c4d9a28f5e6b3c1d704

This key is the login to your account, and it is shown only this once.
Keep a copy somewhere safe: use it with `bomvakt setup` on your other
servers, so they join the same account.

Saved the key in /etc/bomvakt/key and domain = anna.example.org in /etc/bomvakt/bomvakt.conf.
More websites on this server, each with its own access log? Run
sudo bomvakt add-domain <domain> for each; it shows the line to add.
To see it all at bomvakt.com/konto, set a password: sudo bomvakt password
Restarted the bomvakt service. Follow it with: journalctl -u bomvakt -f
```

Afterwards: `/etc/bomvakt/key` = `bv_8c41e07b5d2a96f3e1b07c4d9a28f5e6b3c1d704`, `domain = anna.example.org`

## Bomvakt cannot be reached

No network, DNS or a firewall blocking outgoing HTTPS. Setup stops; nothing is saved. The new-user path stops the same way.

```console
Bomvakt setup (https://bomvakt.com)

  1) Existing user: I have a key
  2) Create a new user

Choose 1 or 2: 1

Key (bv_…): bv_3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c7d2e
  cannot reach https://bomvakt.com: Connection refused
```

Afterwards: nothing saved, the service keeps waiting for a key.

## Not run as root

Setup writes /etc/bomvakt, so it needs sudo.

```console
bomvakt: setup needs root: sudo bomvakt setup
```

Afterwards: nothing saved, the service keeps waiting for a key.

## No terminal to answer in

For example `curl … | sudo sh` in a script, or ssh without -t: input ends at the first question.

```console
Bomvakt setup (https://bomvakt.com)

  1) Existing user: I have a key
  2) Create a new user

Choose 1 or 2: 
bomvakt: no answer (no terminal); run: sudo bomvakt setup
```

Afterwards: nothing saved, the service keeps waiting for a key.

## Several sites share one log

The server runs two sites that both write to nginx's shared `access.log`. Setup finds them in `/etc/nginx/sites-enabled`, and on yes gives each its own log the way certbot edits a config: it shows the change, backs up the files, runs `nginx -t`, and reloads only when the test passes (on a failed test every file is put back). The same runs later with `sudo bomvakt logs`; `--dry-run` only shows it, `--undo` takes it out again.

```console
Bomvakt setup (https://bomvakt.com)

  1) Existing user: I have a key
  2) Create a new user

Choose 1 or 2: 1

Key (bv_…): bv_3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c3f9a1c7d2e
Key accepted: account kim.

Which website runs on this server? Its access log is sent as that site.
  1) example.com
  2) shop.example.com
  or type another domain or IP address to add it to the account
Choose 1-2 or type a domain [1]: 1

Saved the key in /etc/bomvakt/key and domain = example.com in /etc/bomvakt/bomvakt.conf.
More websites on this server, each with its own access log? Run
sudo bomvakt add-domain <domain> for each; it shows the line to add.
To see it all at bomvakt.com/konto, set a password: sudo bomvakt password

web server sites:
  example.com, shop.example.com share /var/log/nginx/access.log (all sent as example.com)
Give each site its own log now? (yes/no) [yes]: 

These sites write to one log, /var/log/nginx/access.log:
  1) example.com
  2) shop.example.com
Each one gets its own log next to it, <site>.access.log.

--- /etc/nginx/sites-available/example.com
+++ /etc/nginx/sites-available/example.com (new)
@@ -2,4 +2,6 @@
     listen 443 ssl; # managed by Certbot
     server_name example.com www.example.com;
+    # bomvakt-added (undo: sudo bomvakt logs --undo)
+    access_log /var/log/nginx/example.com.access.log;
     root /var/www/example.com;
--- /etc/nginx/sites-available/shop.example.com
+++ /etc/nginx/sites-available/shop.example.com (new)
@@ -1,4 +1,6 @@
 server {
     server_name shop.example.com;
+    # bomvakt-added (undo: sudo bomvakt logs --undo)
+    access_log /var/log/nginx/shop.example.com.access.log;
     location / { proxy_pass http://127.0.0.1:3000; }
and in /etc/bomvakt/bomvakt.conf, under [logs]:
  /var/log/nginx/example.com.access.log = example.com
  /var/log/nginx/shop.example.com.access.log = shop.example.com

Reloaded nginx.
Backup of the old files: /var/lib/bomvakt/backup/20261121-093012
  example.com -> /var/log/nginx/example.com.access.log
  shop.example.com -> /var/log/nginx/shop.example.com.access.log
Undo it all with: sudo bomvakt logs --undo
Restarted the bomvakt service. Follow it with: journalctl -u bomvakt -f
```

Afterwards: each site has its own log in nginx and under `[logs]`; the shared `access.log` keeps only requests no site answered (often scanners on the IP address), still sent as `example.com`.

## Check first with logs --dry-run

Three sites share nginx's `access.log`. `sudo bomvakt logs --dry-run` shows what `sudo bomvakt logs` would do and changes nothing: no files, no backup, no reload, no account changes (sites not yet on the account are checked and added only by the real run). The same diff, the same skips.

`shop.example.com` already writes to the shared log with its own format, `main buffer=32k`: its new line keeps that format, and the old line stays as a `# bomvakt-off:` comment so `--undo` can bring it back. `example.com` gets a line in both of its blocks, the HTTPS one and Certbot's redirect. `old.example.com` is a whole `server { … }` on one line, which bomvakt reads but never edits, so it is skipped with a link to doing it by hand.

```console
$ sudo bomvakt logs --dry-run
These sites write to one log, /var/log/nginx/access.log:
  1) example.com
  2) old.example.com
  3) shop.example.com
Each one gets its own log next to it, <site>.access.log.
Which? all, none, or numbers like 1,3 [all]: 
Skipped old.example.com: /etc/nginx/sites-available/old.example.com is laid out in a way bomvakt does not edit. How to do it by hand: https://bomvakt.com/konto/hjalp/sajtlogg

--- /etc/nginx/sites-available/example.com
+++ /etc/nginx/sites-available/example.com (new)
@@ -2,4 +2,6 @@
     listen 443 ssl; # managed by Certbot
     server_name example.com www.example.com;
+    # bomvakt-added (undo: sudo bomvakt logs --undo)
+    access_log /var/log/nginx/example.com.access.log;
     root /var/www/example.com;
 }
@@ -10,4 +12,6 @@
     listen 80;
     server_name example.com www.example.com;
+    # bomvakt-added (undo: sudo bomvakt logs --undo)
+    access_log /var/log/nginx/example.com.access.log;
     return 404; # managed by Certbot
 }
--- /etc/nginx/sites-available/shop.example.com
+++ /etc/nginx/sites-available/shop.example.com (new)
@@ -1,5 +1,7 @@
 server {
     server_name shop.example.com;
-    access_log /var/log/nginx/access.log main buffer=32k;
+    # bomvakt-added (undo: sudo bomvakt logs --undo)
+    access_log /var/log/nginx/shop.example.com.access.log main buffer=32k;
+    # bomvakt-off: access_log /var/log/nginx/access.log main buffer=32k;
     location / { proxy_pass http://127.0.0.1:3000; }
 }
and in /etc/bomvakt/bomvakt.conf, under [logs]:
  /var/log/nginx/example.com.access.log = example.com
  /var/log/nginx/shop.example.com.access.log = shop.example.com

Dry run: nothing changed. Run without --dry-run to make these changes.
```

Afterwards: nothing changed. Run `sudo bomvakt logs` to make the changes (it asks once more before writing), or put `old.example.com` on its own log by hand first.
