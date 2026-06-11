# BPQ Webmail Starter for LinBPQ

A small FastAPI web interface for LinBPQ/BPQMail POP3 + SMTP.

## What it does

- Local website login
- Admin-created users
- Approval flag per user
- Maps each web user to a BPQ POP3/SMTP mailbox
- Read BPQ inbox using POP3
- Read individual messages
- Send messages using SMTP

## What it does not do yet

- Bulletin area browsing
- Message delete/mark read
- Full threading
- Attachments
- RF/node status pages

## Setup

```bash
cd bpq_webmail
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`.

Then run:

```bash
uvicorn app:app --host 127.0.0.1 --port 8088
```

Open:

```text
http://127.0.0.1:8088
```

Default admin is controlled by `.env`.

## Suggested deployment

Put this behind nginx/apache/caddy with HTTPS. Keep LinBPQ POP3/SMTP bound to localhost or firewall-restricted.
