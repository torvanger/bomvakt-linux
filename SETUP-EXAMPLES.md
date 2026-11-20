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
