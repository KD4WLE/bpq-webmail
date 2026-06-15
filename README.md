# BPQ Portal

BPQ Portal is a web front end for LinBPQ/BPQMail systems. It gives packet radio
users a browser-based mailbox, bulletin reader, compose screen, watch lists,
notifications, and public node-status views while keeping BPQ-specific settings
in local deployment configuration.

The project is built with FastAPI, Jinja templates, SQLite, and BPQ POP3/telnet
access.

## Current Features

- Local web login with approved users
- First-run admin setup page
- Admin user management
- Password reset request queue for sysops
- Inbox with message reading and kill/delete action
- Compose support for:
  - Private messages
  - Bulletins
  - NTS
  - Winlink-style addressing
- Cached bulletin list with search, filters, pagination, and unread indicators
- Bulletin preferences for page size, hidden categories, hidden areas, and hidden senders
- Watch lists by sender, area, or category
- Notification center for unread watched bulletins
- Dashboard with new mail, unread bulletins, watched bulletins, notifications, latest bulletin, nodes, and MHeard status
- Public, logged-out access for:
  - MHeard
  - Node Status
  - Nodes
  - Connections
- Portable `.env` configuration for branding, BPQ hosts/ports, database path, web bind settings, and setup behavior
- Optional systemd service install/update script

## Architecture

BPQ Portal talks to BPQ through:

- **BPQ telnet/admin access**, default `127.0.0.1:8010`
- **BPQ POP3/BBS mailbox access**, default `127.0.0.1:110`

Telnet/admin access is used for sending messages, reading bulletin data, and
building node-status caches. POP3 is used for inbox counts, message listing,
message reading, and killing mailbox messages.

SQLite stores portal users, preferences, read tracking, watch lists,
notifications, and password reset requests.

## BPQ Permissions Needed

Each portal user maps to a BPQ mailbox login. That BPQ account should be able to:

- Log in to the BBS/mailbox
- Read its POP3 mailbox
- Delete/kill POP3 messages if mailbox delete is allowed
- Send with the configured compose commands

An approved admin/service account is used for cached public views. That account
must be able to run the BPQ commands used for:

- `users`
- `nodes`
- `mh <port>`
- `po`
- BBS bulletin list/read commands

Default compose command prefixes are:

- Private message: `sp`
- Bulletin: `sb`
- NTS: `st`
- Winlink: `sp`

If your BPQ system expects different commands, change the
`BPQ_COMPOSE_*_COMMAND` values in `.env`.

## Quick Start

```bash
git clone https://github.com/KD4WLE/bpq-webmail.git
cd bpq-webmail
./install.sh
```

The install script will:

- Check for required Python/venv packages
- Offer to install Ubuntu/Debian prerequisites with `apt-get`
- Create `.venv`
- Install Python requirements
- Copy `.env.example` to `.env` if `.env` is missing
- Initialize the SQLite schema

On a fresh Ubuntu/Debian VM, you can explicitly allow package installation:

```bash
./install.sh --install-deps
```

For unattended installs:

```bash
./install.sh --install-deps --yes
```

Edit `.env`:

```bash
nano .env
```

At minimum, set:

```env
SITE_NAME=Example BPQ Portal
NODE_CALLSIGN=EXAMPL
SESSION_SECRET=replace-this-with-a-long-random-secret
BPQ_HOST=127.0.0.1
BPQ_TELNET_PORT=8010
BPQ_POP3_PORT=110
WEB_BIND_HOST=127.0.0.1
WEB_BIND_PORT=8088
```

Run locally:

```bash
source .venv/bin/activate
uvicorn app:app --host 127.0.0.1 --port 8088
```

Open:

```text
http://127.0.0.1:8088
```

## First Admin Setup

BPQ Portal does not create an unsafe default admin password.

On a new install with no admin users, open:

```text
http://127.0.0.1:8088/setup
```

For safer production setup, set a setup token first:

```env
FIRST_RUN_SETUP_TOKEN=long-random-token
```

Then use:

```text
http://127.0.0.1:8088/setup?token=long-random-token
```

After the first admin exists, the setup page is disabled automatically. Existing
admins are never overwritten.

For unattended installs only, you can opt in to admin auto-create:

```env
AUTO_CREATE_ADMIN=true
APP_ADMIN_USERNAME=admin
APP_ADMIN_PASSWORD=use-a-long-unique-password
APP_ADMIN_CALLSIGN=N0CALL
APP_ADMIN_BPQ_USER=N0CALL
APP_ADMIN_BPQ_PASSWORD=bpq-mailbox-password
```

Auto-create only runs when no admin exists and the password is not an unsafe
default.

## Production Deployment

Recommended production layout:

- Run BPQ Portal bound to localhost
- Put nginx, Apache, Caddy, or another reverse proxy in front of it
- Use HTTPS on the public side
- Keep BPQ telnet/admin and POP3 ports bound to localhost or firewall-restricted
- Keep `.env` out of git

Install or update a systemd service:

```bash
sudo ./install.sh --systemd
```

On a fresh Ubuntu/Debian server, combine dependency installation and systemd
setup:

```bash
sudo ./install.sh --install-deps --systemd
```

The default systemd service name is `bpq-webmail`. To use another name:

```bash
sudo BPQ_PORTAL_SERVICE_NAME=my-bpq-portal ./install.sh --systemd
```

The generated service uses:

- `EnvironmentFile=<repo>/.env`
- `WorkingDirectory=<repo>`
- `ExecStart=<repo>/.venv/bin/python -m uvicorn app:app --host WEB_BIND_HOST --port WEB_BIND_PORT`

## Deploying A Second BPQ System

For a second node or another club system:

1. Clone the repository on that server.
2. Run `./install.sh`.
3. Edit `.env`.
4. Set a unique `SESSION_SECRET`.
5. Set the new node callsign and branding.
6. Point `BPQ_HOST`, `BPQ_TELNET_*`, and `BPQ_POP3_*` at that BPQ system.
7. Start the app or install the systemd service.
8. Create the first admin at `/setup`.

Example `.env` values:

```env
SITE_NAME=Example ARC BPQ Portal
SITE_TITLE=Example ARC BPQ Portal
SITE_SUBTITLE=Packet Radio Messaging Interface
SITE_FOOTER_TEXT=Packet Radio Messaging
BRAND_SUBTITLE=BPQ Portal
NODE_CALLSIGN=EXAMPL

DATABASE_PATH=bpq_webmail.db
SESSION_SECRET=replace-with-a-long-random-secret

BPQ_HOST=127.0.0.1
BPQ_TELNET_HOST=127.0.0.1
BPQ_TELNET_PORT=8010
BPQ_POP3_HOST=127.0.0.1
BPQ_POP3_PORT=110

WEB_BIND_HOST=127.0.0.1
WEB_BIND_PORT=8088
```

If BPQ runs on a different host, set `BPQ_HOST` or the service-specific host
variables to that address and restrict access at the firewall.

## Upgrading An Existing Install

Before pulling new code:

```bash
cp .env .env.backup.$(date +%Y%m%d-%H%M%S)
cp bpq_webmail.db bpq_webmail.db.backup.$(date +%Y%m%d-%H%M%S)
```

If upgrading from an older version where `.env` was tracked by git, preserve it
outside the work tree before the pull, then restore it after the pull:

```bash
cp .env /tmp/bpq-webmail.env.preserve
git pull
cp /tmp/bpq-webmail.env.preserve .env
chmod 600 .env
```

Then finish the update:

```bash
./install.sh
sudo systemctl restart bpq-webmail
```

If Python packaging inside `.venv` becomes corrupted, remove the virtual
environment and rerun the installer. This does not remove `.env` or the SQLite
database:

```bash
rm -rf .venv
./install.sh --install-deps
sudo systemctl restart bpq-webmail
```

`.env` is intentionally ignored by git. Existing production values should stay
in place during pulls and deploys.

After upgrading to the portable config version, review `.env.example` and add
any missing values you want to override. Existing installs keep compatible
defaults for branding and node callsign if those values are not present.

For older `.env` files that already include `BPQ_POP3_HOST`, telnet/admin access
will default to that same host unless `BPQ_HOST` or `BPQ_TELNET_HOST` is set.
You may add these values explicitly:

```env
BPQ_HOST=10.0.0.2
BPQ_TELNET_HOST=10.0.0.2
BPQ_TELNET_PORT=8010
WEB_BIND_HOST=127.0.0.1
WEB_BIND_PORT=8088
DATABASE_PATH=bpq_webmail.db
FIRST_RUN_SETUP_ENABLED=true
AUTO_CREATE_ADMIN=false
```

## Configuration Reference

All supported settings are shown in `.env.example`.

Important groups:

- Branding: `SITE_NAME`, `SITE_TITLE`, `SITE_SUBTITLE`, `SITE_FOOTER_TEXT`, `BRAND_*`
- Contact link: `CONTACT_FORM_URL`, `CONTACT_FORM_LABEL`
- Node identity: `NODE_CALLSIGN`
- Storage/security: `DATABASE_PATH`, `SESSION_SECRET`
- BPQ services: `BPQ_HOST`, `BPQ_TELNET_*`, `BPQ_POP3_*`, `BPQ_SMTP_*`
- Web bind: `WEB_BIND_HOST`, `WEB_BIND_PORT`
- Compose commands: `BPQ_COMPOSE_*_COMMAND`
- First-run setup: `FIRST_RUN_SETUP_ENABLED`, `FIRST_RUN_SETUP_TOKEN`
- Optional admin auto-create: `AUTO_CREATE_ADMIN`, `APP_ADMIN_*`

## Security Notes

- Do not commit `.env`.
- Use a long random `SESSION_SECRET`.
- Use HTTPS for public access.
- Restrict BPQ telnet/admin access to trusted hosts.
- Restrict POP3/BBS access to trusted hosts.
- Prefer first-run setup over admin auto-create.
- Give portal users only the BPQ command permissions they need.

## Development

Run syntax checks:

```bash
python3 -m py_compile app.py config.py
```

Run locally:

```bash
source .venv/bin/activate
uvicorn app:app --host 127.0.0.1 --port 8088
```
