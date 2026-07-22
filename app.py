from fastapi.staticfiles import StaticFiles
import sqlite3
import telnetlib
import time
import threading
import re as _re
import poplib
import smtplib
from email.parser import BytesParser
from email.policy import default
from email.message import EmailMessage
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from fastapi import FastAPI, Request, Form, Query, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext
from itsdangerous import URLSafeSerializer, BadSignature

import config

BASE_DIR = config.BASE_DIR
DB_PATH = config.DB_PATH
BPQ_POP3_HOST = config.BPQ_POP3_HOST
BPQ_POP3_PORT = config.BPQ_POP3_PORT
BPQ_TELNET_HOST = config.BPQ_TELNET_HOST
BPQ_TELNET_PORT = config.BPQ_TELNET_PORT
BPQ_SMTP_HOST = config.BPQ_SMTP_HOST
BPQ_SMTP_PORT = config.BPQ_SMTP_PORT
PORTAL_TAGLINE = config.PORTAL_TAGLINE
SESSION_SECRET = config.SESSION_SECRET
APP_VERSION = config.APP_VERSION
NODE_PROMPT_PREFIX = f"{config.NODE_CALLSIGN.upper()}:" if config.NODE_CALLSIGN else ""

COMPOSE_MODES = {
    "private": {
        "label": "Private Message",
        "command": config.COMPOSE_PRIVATE_COMMAND,
        "recipient_label": "To",
        "recipient_placeholder": "CALLSIGN or address",
    },
    "bulletin": {
        "label": "Bulletin",
        "command": config.COMPOSE_BULLETIN_COMMAND,
        "recipient_label": "Bulletin Category / Area",
        "recipient_placeholder": "CATEGORY or @AREA",
    },
    "nts": {
        "label": "NTS",
        "command": config.COMPOSE_NTS_COMMAND,
        "recipient_label": "NTS Destination",
        "recipient_placeholder": "NTS destination",
    },
    "winlink": {
        "label": "Winlink",
        "command": config.COMPOSE_WINLINK_COMMAND,
        "recipient_label": "Winlink Address",
        "recipient_placeholder": "CALLSIGN or user@example.org",
    },
}


# Simple in-process TTL cache for slow BPQ/telnet views
_CACHE = {}



def log_elapsed(label, start):
    try:
        print(f"PERF {label}: {time.time() - start:.2f}s")
    except Exception:
        pass


def is_node_prompt_line(line: str) -> bool:
    return bool(NODE_PROMPT_PREFIX and line.strip().upper().startswith(NODE_PROMPT_PREFIX))


BPQ_CONTINUE_PROMPT_RE = _re.compile(
    r"(<\s*cr\s*>\s*to\s*cont(?:inue)?|press\s+(?:return|enter)|more\?|--\s*more\s*--)",
    _re.IGNORECASE,
)


def read_bpq_paged_output(tn, initial_wait=1.0, idle_wait=0.35, max_wait=30.0) -> str:
    output = ""
    deadline = time.time() + max_wait
    last_data_at = time.time()
    handled_prompt_end = 0
    time.sleep(initial_wait)

    while time.time() < deadline:
        chunk = tn.read_very_eager().decode(errors="ignore")
        if chunk:
            output += chunk
            last_data_at = time.time()
            tail_start = max(0, len(output) - 300)
            prompt = BPQ_CONTINUE_PROMPT_RE.search(output[tail_start:])
            prompt_end = tail_start + prompt.end() if prompt else 0
            if prompt and prompt_end > handled_prompt_end:
                handled_prompt_end = prompt_end
                tn.write(b"\r")
                time.sleep(idle_wait)
                continue
        elif time.time() - last_data_at >= idle_wait:
            break

        time.sleep(idle_wait)

    return BPQ_CONTINUE_PROMPT_RE.sub("", output)


def bpq_command(user, commands, timeout=10, settle=0.5):
    """Run one or more BPQ telnet commands and return combined output."""
    start = time.time()
    output = ""
    if isinstance(commands, str):
        commands = [commands]

    tn = telnetlib.Telnet(BPQ_TELNET_HOST, BPQ_TELNET_PORT, timeout=timeout)
    try:
        time.sleep(settle)
        output += tn.read_very_eager().decode(errors="ignore")

        tn.write((user["bpq_user"] + "\r").encode())
        time.sleep(settle)
        output += tn.read_very_eager().decode(errors="ignore")

        tn.write((user["bpq_password"] + "\r").encode())
        time.sleep(settle)
        output += tn.read_very_eager().decode(errors="ignore")

        for cmd in commands:
            tn.write((cmd + "\r").encode())
            time.sleep(settle)
            output += tn.read_very_eager().decode(errors="ignore")

        tn.write(b"bye\r")
        log_elapsed(f"bpq commands {commands}", start)
        return output
    finally:
        try:
            tn.close()
        except Exception:
            pass


def ttl_cache_get(key, ttl_seconds):
    item = _CACHE.get(key)
    if not item:
        return None
    ts, value = item
    if time.time() - ts > ttl_seconds:
        _CACHE.pop(key, None)
        return None
    return value

def ttl_cache_set(key, value):
    _CACHE[key] = (time.time(), value)
    return value

app = FastAPI(title=config.SITE_TITLE)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["app_version"] = APP_VERSION
templates.env.globals["site_name"] = config.SITE_NAME
templates.env.globals["site_title"] = config.SITE_TITLE
templates.env.globals["site_subtitle"] = config.SITE_SUBTITLE
templates.env.globals["site_footer_text"] = config.SITE_FOOTER_TEXT
templates.env.globals["brand_subtitle"] = config.BRAND_SUBTITLE
templates.env.globals["brand_logo_path"] = config.BRAND_LOGO_PATH
templates.env.globals["brand_logo_alt"] = config.BRAND_LOGO_ALT
templates.env.globals["contact_form_url"] = config.CONTACT_FORM_URL
templates.env.globals["contact_form_label"] = config.CONTACT_FORM_LABEL
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
signer = URLSafeSerializer(SESSION_SECRET, salt="bpq-webmail-session")


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def admin_exists() -> bool:
    if not DB_PATH.exists():
        return False
    with db() as conn:
        row = conn.execute("SELECT count(*) FROM users WHERE is_admin = 1").fetchone()
        return bool(row and row[0])


def is_safe_setup_password(username: str, password: str) -> bool:
    password = password or ""
    weak_values = {
        "",
        "admin",
        "password",
        "changeme",
        "change-me",
        "change-me-now",
        "dev-change-me",
        username.lower(),
    }
    return len(password) >= 10 and password.lower() not in weak_values


def should_auto_create_admin() -> bool:
    if not config.AUTO_CREATE_ADMIN:
        return False
    return all(
        (
            config.APP_ADMIN_USERNAME,
            is_safe_setup_password(config.APP_ADMIN_USERNAME, config.APP_ADMIN_PASSWORD),
            config.APP_ADMIN_BPQ_USER,
            config.APP_ADMIN_BPQ_PASSWORD,
        )
    )


def setup_token_valid(token: str) -> bool:
    return not config.FIRST_RUN_SETUP_TOKEN or token == config.FIRST_RUN_SETUP_TOKEN


def init_db() -> None:
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                callsign TEXT NOT NULL,
                bpq_user TEXT NOT NULL,
                bpq_password TEXT NOT NULL,
                approved INTEGER NOT NULL DEFAULT 0,
                is_admin INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS password_reset_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                callsign TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                handled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                handled_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bulletin_preferences (
                user_id INTEGER PRIMARY KEY,
                hidden_categories TEXT NOT NULL DEFAULT '',
                hidden_areas TEXT NOT NULL DEFAULT '',
                hidden_senders TEXT NOT NULL DEFAULT '',
                page_size INTEGER NOT NULL DEFAULT 25
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_message_reads (
                user_id INTEGER NOT NULL,
                message_id TEXT NOT NULL,
                read_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, message_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watch_lists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                watch_type TEXT NOT NULL CHECK (watch_type IN ('sender', 'area', 'category')),
                watch_value TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, watch_type, watch_value)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message_id TEXT NOT NULL,
                watch_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                read_at TEXT,
                UNIQUE(user_id, message_id, watch_id)
            )
            """
        )
        admin_count = conn.execute("SELECT count(*) FROM users WHERE is_admin = 1").fetchone()[0]
        if admin_count == 0 and should_auto_create_admin():
            conn.execute(
                """
                INSERT INTO users
                (username, password_hash, callsign, bpq_user, bpq_password, approved, is_admin)
                VALUES (?, ?, ?, ?, ?, 1, 1)
                """,
                (
                    config.APP_ADMIN_USERNAME,
                    pwd_context.hash(config.APP_ADMIN_PASSWORD),
                    config.APP_ADMIN_CALLSIGN,
                    config.APP_ADMIN_BPQ_USER,
                    config.APP_ADMIN_BPQ_PASSWORD,
                ),
            )


@app.on_event("startup")
def startup() -> None:
    init_db()


def get_session_user(request: Request) -> Optional[sqlite3.Row]:
    cookie = request.cookies.get("bpq_session")
    if not cookie:
        return None
    try:
        data = signer.loads(cookie)
    except BadSignature:
        return None
    user_id = data.get("user_id")
    with db() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def require_user(request: Request) -> sqlite3.Row:
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    if not user["approved"]:
        raise HTTPException(status_code=403, detail="Account is not approved yet.")
    return user


def require_admin(request: Request) -> sqlite3.Row:
    user = require_user(request)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


def get_bpq_service_user() -> Optional[sqlite3.Row]:
    with db() as conn:
        user = conn.execute(
            """
            SELECT * FROM users
            WHERE approved = 1 AND is_admin = 1
            ORDER BY id
            LIMIT 1
            """
        ).fetchone()
        if user:
            return user
        return conn.execute(
            """
            SELECT * FROM users
            WHERE approved = 1
            ORDER BY id
            LIMIT 1
            """
        ).fetchone()


def parse_pop_message(raw_lines: list[bytes]) -> dict:
    raw = b"\r\n".join(raw_lines)
    msg = BytesParser(policy=default).parsebytes(raw)
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                body = part.get_content()
                break
    else:
        try:
            body = msg.get_content()
        except Exception:
            body = raw.decode(errors="replace")
    return {
        "subject": msg.get("subject", "(no subject)"),
        "from": msg.get("from", ""),
        "to": msg.get("to", ""),
        "date": msg.get("date", ""),
        "body": body,
    }


def pop3_client(user: sqlite3.Row) -> poplib.POP3:
    client = poplib.POP3(BPQ_POP3_HOST, BPQ_POP3_PORT, timeout=10)
    client.user(user["bpq_user"])
    client.pass_(user["bpq_password"])
    return client


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = get_session_user(request)
    return templates.TemplateResponse(
        "home.html",
        {"request": request, "user": user, "setup_needed": not admin_exists()},
    )


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None, "setup_needed": not admin_exists()},
    )


@app.get("/setup", response_class=HTMLResponse)
def first_run_setup_form(request: Request, token: str = Query("")):
    if not config.FIRST_RUN_SETUP_ENABLED or admin_exists():
        raise HTTPException(status_code=404, detail="Setup is not available.")
    if not setup_token_valid(token):
        raise HTTPException(status_code=403, detail="Invalid setup token.")

    return templates.TemplateResponse(
        "setup.html",
        {
            "request": request,
            "user": None,
            "error": None,
            "token": token,
        },
    )


@app.post("/setup", response_class=HTMLResponse)
def first_run_setup_create(
    request: Request,
    token: str = Form(""),
    username: str = Form(...),
    password: str = Form(...),
    callsign: str = Form(...),
    bpq_user: str = Form(...),
    bpq_password: str = Form(...),
):
    if not config.FIRST_RUN_SETUP_ENABLED or admin_exists():
        raise HTTPException(status_code=404, detail="Setup is not available.")
    if not setup_token_valid(token):
        raise HTTPException(status_code=403, detail="Invalid setup token.")

    username = username.strip()
    callsign = callsign.strip().upper()
    bpq_user = bpq_user.strip()

    if not username or not callsign or not bpq_user or not bpq_password:
        return templates.TemplateResponse(
            "setup.html",
            {
                "request": request,
                "user": None,
                "error": "All fields are required.",
                "token": token,
            },
        )

    if not is_safe_setup_password(username, password):
        return templates.TemplateResponse(
            "setup.html",
            {
                "request": request,
                "user": None,
                "error": "Use a unique admin password with at least 10 characters.",
                "token": token,
            },
        )

    with db() as conn:
        if conn.execute("SELECT count(*) FROM users WHERE is_admin = 1").fetchone()[0]:
            raise HTTPException(status_code=404, detail="Setup is not available.")
        conn.execute(
            """
            INSERT INTO users
            (username, password_hash, callsign, bpq_user, bpq_password, approved, is_admin)
            VALUES (?, ?, ?, ?, ?, 1, 1)
            """,
            (
                username,
                pwd_context.hash(password),
                callsign,
                bpq_user,
                bpq_password,
            ),
        )

    return RedirectResponse("/login", status_code=303)


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    password_ok = False
    if user:
        try:
            password_ok = pwd_context.verify(password, user["password_hash"])
        except Exception as exc:
            print(f"Password verification failed for {username}: {exc}")
    if not user or not password_ok:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid username or password."})
    if not user["approved"]:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Account is waiting for approval."})
    resp = RedirectResponse("/dashboard", status_code=303)
    resp.set_cookie("bpq_session", signer.dumps({"user_id": user["id"]}), httponly=True, samesite="lax")
    return resp


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_form(request: Request):
    return templates.TemplateResponse(
        "forgot_password.html",
        {"request": request, "error": None, "message": None},
    )


@app.post("/forgot-password", response_class=HTMLResponse)
def forgot_password_request(
    request: Request,
    username: str = Form(...),
    callsign: str = Form(...),
    message: str = Form(""),
):
    username = username.strip()
    callsign = callsign.strip().upper()
    message = message.strip()

    if not username or not callsign:
        return templates.TemplateResponse(
            "forgot_password.html",
            {
                "request": request,
                "error": "Username and callsign are required.",
                "message": None,
            },
        )

    with db() as conn:
        conn.execute(
            """
            insert into password_reset_requests (username, callsign, message)
            values (?, ?, ?)
            """,
            (username, callsign, message),
        )
        conn.commit()

    return templates.TemplateResponse(
        "forgot_password.html",
        {
            "request": request,
            "error": None,
            "message": "Password reset request received. A sysop will review it.",
        },
    )


@app.get("/logout")
def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie("bpq_session")
    return resp


@app.get("/inbox", response_class=HTMLResponse)
def inbox(request: Request, status: str = Query("")):
    user = require_user(request)
    messages = []
    error = None
    try:
        client = pop3_client(user)
        count, _ = client.stat()
        for idx in range(1, count + 1):
            # TOP gets headers plus 0 body lines, if supported by the server.
            try:
                _, lines, _ = client.top(idx, 0)
            except Exception:
                _, lines, _ = client.retr(idx)
            parsed = parse_pop_message(lines)
            messages.append({"id": idx, **parsed})
        client.quit()
    except Exception as exc:
        error = f"Could not connect to LinBPQ POP3: {exc}"
    messages.reverse()
    return templates.TemplateResponse(
        "inbox.html",
        {
            "request": request,
            "user": user,
            "messages": messages,
            "error": error,
            "status": status,
        },
    )


@app.get("/message/{message_id}", response_class=HTMLResponse)
def message(request: Request, message_id: int):
    user = require_user(request)
    try:
        client = pop3_client(user)
        _, lines, _ = client.retr(message_id)
        parsed = parse_pop_message(lines)
        client.quit()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not read message: {exc}")
    return templates.TemplateResponse("message.html", {"request": request, "user": user, "message": parsed, "message_id": message_id})


@app.post("/message/{message_id}/kill")
def kill_message(request: Request, message_id: int):
    user = require_user(request)

    try:
        client = pop3_client(user)
        client.dele(message_id)
        client.quit()
    except Exception as exc:
        query = urlencode({"status": f"Could not kill message {message_id}: {exc}"})
        return RedirectResponse(
            f"/inbox?{query}",
            status_code=303,
        )

    query = urlencode({"status": f"Message {message_id} killed."})
    return RedirectResponse(
        f"/inbox?{query}",
        status_code=303,
    )


@app.get("/compose", response_class=HTMLResponse)
def compose_form(request: Request, to: str = "", subject: str = "", mode: str = "private"):
    user = require_user(request)
    if mode not in COMPOSE_MODES:
        mode = "private"
    return templates.TemplateResponse(
        "compose.html",
        {
            "request": request,
            "user": user,
            "to": to,
            "subject": subject,
            "body": "",
            "error": None,
            "mode": mode,
            "modes": COMPOSE_MODES,
        },
    )


@app.post("/compose", response_class=HTMLResponse)
def compose_send(
    request: Request,
    mode: str = Form("private"),
    to: str = Form(...),
    subject: str = Form(...),
    body: str = Form(...),
):
    user = require_user(request)
    output = ""
    mode_config = COMPOSE_MODES.get(mode)

    if not mode_config:
        return templates.TemplateResponse(
            "compose.html",
            {
                "request": request,
                "user": user,
                "to": to,
                "subject": subject,
                "body": body,
                "error": "Invalid compose mode.",
                "mode": "private",
                "modes": COMPOSE_MODES,
            },
        )

    try:
        tn = telnetlib.Telnet(BPQ_TELNET_HOST, BPQ_TELNET_PORT, timeout=15)

        tn.read_until(b"Username:", timeout=10)
        tn.write((user["bpq_user"] + "\r").encode())

        tn.read_until(b"Password:", timeout=10)
        tn.write((user["bpq_password"] + "\r").encode())

        time.sleep(1)
        output += tn.read_very_eager().decode(errors="ignore")

        tn.write(b"bbs\r")
        time.sleep(1)
        output += tn.read_very_eager().decode(errors="ignore")

        tn.write((f"{mode_config['command']} {to.strip()}\r").encode())
        time.sleep(1)
        output += tn.read_very_eager().decode(errors="ignore")

        tn.write((subject.strip() + "\r").encode())
        time.sleep(1)
        output += tn.read_very_eager().decode(errors="ignore")

        clean_body = body.replace("\r\n", "\n").replace("\r", "\n").strip()

        if PORTAL_TAGLINE:
            clean_body += f"\n\n---\n{PORTAL_TAGLINE}"

        for line in clean_body.split("\n"):
            tn.write((line + "\r").encode())
            time.sleep(0.25)

        time.sleep(1)
        output += tn.read_very_eager().decode(errors="ignore")

        tn.write(b"/ex\r")
        time.sleep(5)
        output += tn.read_very_eager().decode(errors="ignore")

        tn.write(b"bye\r")
        tn.close()

        return templates.TemplateResponse(
            "compose.html",
            {
                "request": request,
                "user": user,
                "to": to,
                "subject": subject,
                "body": body,
                "error": f"{mode_config['label']} send response:\n\n" + output,
                "mode": mode,
                "modes": COMPOSE_MODES,
            },
        )

    except Exception as exc:
        return templates.TemplateResponse(
            "compose.html",
            {
                "request": request,
                "user": user,
                "to": to,
                "subject": subject,
                "body": body,
                "error": f"Could not send via LinBPQ BBS: {exc}\n\n{output}",
                "mode": mode if mode in COMPOSE_MODES else "private",
                "modes": COMPOSE_MODES,
            },
        )

def admin_toggle_user(request: Request, user_id: int):
    admin = require_admin(request)
    if admin["id"] == user_id:
        return RedirectResponse("/admin/users", status_code=303)
    with db() as conn:
        user = conn.execute("SELECT approved FROM users WHERE id = ?", (user_id,)).fetchone()
        if user:
            conn.execute("UPDATE users SET approved = ? WHERE id = ?", (0 if user["approved"] else 1, user_id))
    return RedirectResponse("/admin/users", status_code=303)


@app.get("/dashboard")
def dashboard(request: Request):
    user = require_user(request)

    new_mail_count = None
    mail_error = None
    client = None
    try:
        client = pop3_client(user)
        new_mail_count, _ = client.stat()
    except Exception as exc:
        mail_error = f"Could not load mailbox count: {exc}"
    finally:
        if client:
            try:
                client.quit()
            except Exception:
                pass

    bulletins = LB_CACHE["messages"] if "LB_CACHE" in globals() else []
    unread_count = unread_count_for_messages(user["id"], bulletins)
    watches = get_watch_lists(user["id"])
    watched_bulletins = filter_watched_bulletins(bulletins, watches)
    watched_unread_count = unread_count_for_messages(user["id"], watched_bulletins)
    notification_count = unread_notification_count(user["id"])
    latest_bulletin = bulletins[0] if bulletins else None
    read_ids = get_read_message_ids(user["id"], [str(m["id"]) for m in bulletins])
    recent_bulletins = [dict(m) for m in bulletins[:5]]
    for message in recent_bulletins:
        message["is_read"] = str(message["id"]) in read_ids
    recent_unread_watched_bulletins = [
        dict(m) for m in watched_bulletins
        if str(m["id"]) not in read_ids
    ][:5]

    node_count = len(NODE_CACHE["nodes"]) if "NODE_CACHE" in globals() and NODE_CACHE["nodes"] else None
    mheard_count = len(MHEARD_CACHE["heard"]) if "MHEARD_CACHE" in globals() and MHEARD_CACHE["heard"] else None
    connected_users_count = NODE_STATUS_CACHE["connection_count"] if "NODE_STATUS_CACHE" in globals() and NODE_STATUS_CACHE["timestamp"] else None
    port_count = len(NODE_STATUS_CACHE["ports"]) if "NODE_STATUS_CACHE" in globals() and NODE_STATUS_CACHE["ports"] else None
    cache_ready = bool(
        ("LB_CACHE" in globals() and LB_CACHE["timestamp"])
        or ("NODE_CACHE" in globals() and NODE_CACHE["timestamp"])
        or ("MHEARD_CACHE" in globals() and MHEARD_CACHE["timestamp"])
        or ("NODE_STATUS_CACHE" in globals() and NODE_STATUS_CACHE["timestamp"])
    )
    system_health = "Ready" if cache_ready else "Starting"

    with db() as conn:
        total_users = conn.execute("select count(*) from users").fetchone()[0]
        approved_users = conn.execute("select count(*) from users where approved=1").fetchone()[0]

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "total_users": total_users,
            "approved_users": approved_users,
            "new_mail_count": new_mail_count,
            "mail_error": mail_error,
            "bulletin_count": len(bulletins),
            "unread_count": unread_count,
            "watched_unread_count": watched_unread_count,
            "notification_count": notification_count,
            "latest_bulletin": latest_bulletin,
            "recent_bulletins": recent_bulletins,
            "has_watches": bool(watches),
            "recent_unread_watched_bulletins": recent_unread_watched_bulletins,
            "node_count": node_count,
            "connected_users_count": connected_users_count,
            "port_count": port_count,
            "system_health": system_health,
            "mheard_count": mheard_count,
            "bulletin_cache_age": int(time.time() - LB_CACHE["timestamp"]) if "LB_CACHE" in globals() and LB_CACHE["timestamp"] else None,
            "node_cache_age": int(time.time() - NODE_CACHE["timestamp"]) if "NODE_CACHE" in globals() and NODE_CACHE["timestamp"] else None,
            "mheard_cache_age": int(time.time() - MHEARD_CACHE["timestamp"]) if "MHEARD_CACHE" in globals() and MHEARD_CACHE["timestamp"] else None,
        },
    )


LB_CACHE = {
    "timestamp": 0,
    "messages": [],
    "raw_output": "",
    "body_cache": {},
}

MHEARD_CACHE = {
    "timestamp": 0,
    "heard": [],
}

CONNECTIONS_CACHE = {
    "timestamp": 0,
    "uplinks": [],
    "circuits": [],
    "lines": [],
    "raw_output": "",
}

NODE_STATUS_CACHE = {
    "timestamp": 0,
    "ports": [],
    "version_line": "",
    "connection_count": 0,
    "raw_ports": "",
    "raw_users": "",
}

LB_CACHE_SECONDS = 60
MHEARD_CACHE_SECONDS = 300
CONNECTIONS_CACHE_SECONDS = 60
NODE_STATUS_CACHE_SECONDS = 60

BULLETIN_DATE_RE = _re.compile(r"^\d{1,2}-[A-Za-z]{3}$")
BULLETIN_CALL_RE = _re.compile(r"^[A-Za-z]{1,3}\d[A-Za-z0-9/.-]*$")


def parse_bulletin_line(line: str) -> Optional[dict]:
    parts = line.strip().split(None, 7)
    if len(parts) < 6:
        return None

    msg_id, date, msg_type, size, category = parts[:5]
    if not msg_id.isdigit() or not BULLETIN_DATE_RE.match(date) or not size.isdigit():
        return None

    has_area = len(parts) >= 8 and (parts[5].startswith("@") or BULLETIN_CALL_RE.match(parts[6]))

    if has_area:
        area = parts[5]
        sender = parts[6]
        subject = parts[7]
    else:
        area = ""
        sender = parts[5]
        subject = " ".join(parts[6:]) if len(parts) > 6 else ""

    return {
        "id": msg_id,
        "date": date,
        "type": msg_type,
        "size": size,
        "category": category,
        "area": area,
        "from": sender,
        "subject": subject,
    }


def fetch_bulletin_list(user) -> tuple[list[dict], str, list[str]]:
    tn = telnetlib.Telnet(BPQ_TELNET_HOST, BPQ_TELNET_PORT, timeout=10)
    try:
        tn.read_until(b"Username:", timeout=10)
        tn.write((user["bpq_user"] + "\r").encode())

        tn.read_until(b"Password:", timeout=10)
        tn.write((user["bpq_password"] + "\r").encode())

        time.sleep(1)
        tn.read_very_eager()

        tn.write(b"bbs\r")
        time.sleep(1)
        tn.read_very_eager()

        tn.write(b"lb\r")
        time.sleep(4)
        raw_output = tn.read_very_eager().decode(errors="ignore")

        tn.write(b"bye\r")
    finally:
        try:
            tn.close()
        except Exception:
            pass

    messages = []
    unmatched = []
    for line in raw_output.splitlines():
        parsed = parse_bulletin_line(line)
        if parsed:
            messages.append(parsed)
        elif line.strip() and line.lstrip()[:1].isdigit():
            unmatched.append(line)

    return messages, raw_output, unmatched


def read_bulletin_body(user, msg_id: int) -> str:
    responses = []

    for command in (f"r {msg_id}", f"read {msg_id}"):
        tn = telnetlib.Telnet(BPQ_TELNET_HOST, BPQ_TELNET_PORT, timeout=10)
        try:
            tn.read_until(b"Username:", timeout=10)
            tn.write((user["bpq_user"] + "\r").encode())

            tn.read_until(b"Password:", timeout=10)
            tn.write((user["bpq_password"] + "\r").encode())

            time.sleep(1)
            tn.read_very_eager()

            tn.write(b"bbs\r")
            time.sleep(1)
            tn.read_very_eager()

            tn.write((command + "\r").encode())
            response = read_bpq_paged_output(tn, initial_wait=1.0, max_wait=45.0)
            responses.append(f"$ {command}\n{response}".strip())

            tn.write(b"bye\r")
        finally:
            try:
                tn.close()
            except Exception:
                pass

        response_text = response.strip()
        if response_text and "not found" not in response_text.lower() and "unknown command" not in response_text.lower():
            return response

    return "\n\n".join(responses)


def refresh_mheard_cache(user, ports: dict[str, str]) -> None:
    heard = []
    tn = telnetlib.Telnet(BPQ_TELNET_HOST, BPQ_TELNET_PORT, timeout=10)
    try:
        tn.read_until(b"Username:", timeout=10)
        tn.write((user["bpq_user"] + "\r").encode())

        tn.read_until(b"Password:", timeout=10)
        tn.write((user["bpq_password"] + "\r").encode())

        time.sleep(1)
        tn.read_very_eager()

        for pnum, pname in ports.items():
            tn.write((f"mh {pnum}\r").encode())
            time.sleep(1)
            output = tn.read_very_eager().decode(errors="ignore")

            for line in output.splitlines():
                parts = line.split()
                if len(parts) >= 2 and ":" in parts[1]:
                    heard.append({
                        "port": pnum,
                        "port_name": pname,
                        "callsign": parts[0],
                        "last_heard": parts[1],
                        "extra": " ".join(parts[2:]) if len(parts) > 2 else "",
                    })

        tn.write(b"bye\r")
    finally:
        try:
            tn.close()
        except Exception:
            pass

    MHEARD_CACHE["timestamp"] = time.time()
    MHEARD_CACHE["heard"] = heard


def refresh_connections_cache(user) -> None:
    output = bpq_command(user, "users", timeout=10, settle=1.0)
    lines = []
    circuits = []
    uplinks = []

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue

        if is_node_prompt_line(line) or "G8BPQ Network System" in line:
            continue

        if line.startswith("TNC Uplink"):
            uplinks.append(line)
            continue

        if "Circuit(" in line:
            circuits.append(line)
            continue

        lines.append(line)

    CONNECTIONS_CACHE["timestamp"] = time.time()
    CONNECTIONS_CACHE["uplinks"] = uplinks
    CONNECTIONS_CACHE["circuits"] = circuits
    CONNECTIONS_CACHE["lines"] = lines
    CONNECTIONS_CACHE["raw_output"] = output


def refresh_node_status_cache(user) -> None:
    ports = []
    version_line = ""
    connection_count = 0
    raw_ports = ""
    raw_users = ""

    tn = telnetlib.Telnet(BPQ_TELNET_HOST, BPQ_TELNET_PORT, timeout=10)
    try:
        tn.read_until(b"Username:", timeout=10)
        tn.write((user["bpq_user"] + "\r").encode())

        tn.read_until(b"Password:", timeout=10)
        tn.write((user["bpq_password"] + "\r").encode())

        time.sleep(1)
        tn.read_very_eager()

        tn.write(b"po\r")
        time.sleep(1)
        raw_ports = tn.read_very_eager().decode(errors="ignore")

        tn.write(b"users\r")
        time.sleep(1)
        raw_users = tn.read_very_eager().decode(errors="ignore")

        tn.write(b"bye\r")
    finally:
        try:
            tn.close()
        except Exception:
            pass

    for line in raw_ports.splitlines():
        clean = line.strip()

        if "G8BPQ Network System" in clean:
            version_line = clean

        if is_node_prompt_line(clean):
            continue

        if " Port " in clean and clean[0:2].strip().isdigit():
            parts = clean.split(None, 1)
            if len(parts) == 2:
                ports.append({
                    "number": parts[0],
                    "description": parts[1],
                })

    for line in raw_users.splitlines():
        if "Circuit(" in line or line.strip().startswith("TNC Uplink"):
            connection_count += 1

    NODE_STATUS_CACHE["timestamp"] = time.time()
    NODE_STATUS_CACHE["ports"] = ports
    NODE_STATUS_CACHE["version_line"] = version_line
    NODE_STATUS_CACHE["connection_count"] = connection_count
    NODE_STATUS_CACHE["raw_ports"] = raw_ports
    NODE_STATUS_CACHE["raw_users"] = raw_users


def refresh_nodes_cache(user) -> None:
    raw_output = bpq_command(user, "nodes", timeout=15, settle=1.0)
    nodes = []
    in_nodes_section = False

    for line in raw_output.splitlines():
        clean = line.strip()

        if not clean:
            continue

        if clean == "Nodes" or clean.endswith("} Nodes") or clean.endswith(":Nodes") or clean.lower().endswith(" nodes"):
            in_nodes_section = True
            continue

        if not in_nodes_section or is_node_prompt_line(clean):
            continue

        for token in clean.split():
            if ":" in token:
                alias, callsign = token.split(":", 1)
                nodes.append({
                    "alias": alias.strip(),
                    "callsign": callsign.strip(),
                })
            elif "-" in token or token.isalnum():
                if token not in ["Nodes"]:
                    nodes.append({
                        "alias": "",
                        "callsign": token.strip(),
                    })

    NODE_CACHE["timestamp"] = time.time()
    NODE_CACHE["nodes"] = nodes
    NODE_CACHE["raw_output"] = raw_output


def bulletin_matches_query(message: dict, query: str, body_cache: dict) -> bool:
    q = query.lower()
    fields = (
        message.get("subject", ""),
        message.get("from", ""),
        message.get("area", ""),
        message.get("category", ""),
    )
    if any(q in str(value).lower() for value in fields):
        return True

    cached_body = body_cache.get(str(message.get("id")))
    return bool(cached_body and q in cached_body.lower())


def get_read_message_ids(user_id: int, message_ids: list[str]) -> set[str]:
    if not message_ids:
        return set()

    placeholders = ",".join("?" for _ in message_ids)
    with db() as conn:
        rows = conn.execute(
            f"""
            select message_id
            from user_message_reads
            where user_id=? and message_id in ({placeholders})
            """,
            [user_id, *message_ids],
        ).fetchall()

    return {row["message_id"] for row in rows}


def mark_messages_read(user_id: int, message_ids: list[str]) -> None:
    clean_ids = sorted({str(message_id).strip() for message_id in message_ids if str(message_id).strip()})
    if not clean_ids:
        return

    with db() as conn:
        conn.executemany(
            """
            insert into user_message_reads (user_id, message_id, read_at)
            values (?, ?, CURRENT_TIMESTAMP)
            on conflict(user_id, message_id) do update set read_at=CURRENT_TIMESTAMP
            """,
            [(user_id, message_id) for message_id in clean_ids],
        )
        conn.commit()


WATCH_FIELDS = {
    "sender": "from",
    "area": "area",
    "category": "category",
}


def normalize_watch_value(value: str) -> str:
    return value.strip().upper()


def get_watch_lists(user_id: int) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            """
            select id, watch_type, watch_value, created_at
            from watch_lists
            where user_id=?
            order by watch_type, watch_value
            """,
            (user_id,),
        ).fetchall()


def bulletin_matches_watch(message: dict, watch) -> bool:
    field = WATCH_FIELDS.get(watch["watch_type"])
    if not field:
        return False
    return str(message.get(field, "")).strip().upper() == watch["watch_value"].strip().upper()


def filter_watched_bulletins(messages: list[dict], watches) -> list[dict]:
    if not watches:
        return []
    return [m for m in messages if any(bulletin_matches_watch(m, watch) for watch in watches)]


def unread_count_for_messages(user_id: int, messages: list[dict]) -> int:
    message_ids = [str(m["id"]) for m in messages]
    read_ids = get_read_message_ids(user_id, message_ids)
    return sum(1 for msg_id in message_ids if msg_id not in read_ids)


def notification_title(message: dict, watch) -> str:
    return f"{watch['watch_type'].title()} watch matched bulletin #{message['id']}"


def notification_body(message: dict, watch) -> str:
    subject = message.get("subject", "(no subject)")
    return f"{watch['watch_value']} matched {message.get('from', '')} / {message.get('area', '')} / {message.get('category', '')}: {subject}"


def sync_notifications_for_user(user_id: int) -> None:
    messages = LB_CACHE["messages"] if "LB_CACHE" in globals() else []
    watches = get_watch_lists(user_id)
    if not messages or not watches:
        return

    message_ids = [str(m["id"]) for m in messages]
    read_ids = get_read_message_ids(user_id, message_ids)
    rows = []

    for message in messages:
        msg_id = str(message["id"])
        if msg_id in read_ids:
            continue
        for watch in watches:
            if bulletin_matches_watch(message, watch):
                rows.append((
                    user_id,
                    msg_id,
                    watch["id"],
                    notification_title(message, watch),
                    notification_body(message, watch),
                ))

    if not rows:
        return

    with db() as conn:
        conn.executemany(
            """
            insert or ignore into notifications (user_id, message_id, watch_id, title, body)
            values (?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


def unread_notification_count(user_id: int) -> int:
    sync_notifications_for_user(user_id)
    with db() as conn:
        return conn.execute(
            "select count(*) from notifications where user_id=? and is_read=0",
            (user_id,),
        ).fetchone()[0]


def unread_watchlist_count(user_id: int) -> int:
    messages = LB_CACHE["messages"] if "LB_CACHE" in globals() else []
    watches = get_watch_lists(user_id)
    if not messages or not watches:
        return 0
    watched_messages = filter_watched_bulletins(messages, watches)
    return unread_count_for_messages(user_id, watched_messages)


def mark_notifications_read_for_messages(user_id: int, message_ids: list[str]) -> None:
    clean_ids = sorted({str(message_id).strip() for message_id in message_ids if str(message_id).strip()})
    if not clean_ids:
        return

    placeholders = ",".join("?" for _ in clean_ids)
    with db() as conn:
        conn.execute(
            f"""
            update notifications
            set is_read=1, read_at=CURRENT_TIMESTAMP
            where user_id=? and message_id in ({placeholders})
            """,
            [user_id, *clean_ids],
        )
        conn.commit()


templates.env.globals["unread_notification_count"] = unread_notification_count
templates.env.globals["unread_watchlist_count"] = unread_watchlist_count


def apply_bulletin_preferences(messages: list[dict], prefs) -> tuple[list[dict], int]:
    hidden_categories = set()
    hidden_areas = set()
    hidden_senders = set()
    per_page = 25

    if prefs:
        hidden_categories = {x.strip().upper() for x in (prefs["hidden_categories"] or "").split(",") if x.strip()}
        hidden_areas = {x.strip().upper() for x in (prefs["hidden_areas"] or "").split(",") if x.strip()}
        hidden_senders = {x.strip().upper() for x in (prefs["hidden_senders"] or "").split(",") if x.strip()}
        per_page = prefs["page_size"] or 25

    if hidden_categories:
        messages = [m for m in messages if m["category"].strip().upper() not in hidden_categories]

    if hidden_areas:
        messages = [m for m in messages if m["area"].strip().upper() not in hidden_areas]

    if hidden_senders:
        messages = [m for m in messages if m["from"].strip().upper() not in hidden_senders]

    return messages, per_page


def get_bulletin_preferences(user_id: int):
    with db() as conn:
        return conn.execute(
            "select hidden_categories, hidden_areas, hidden_senders, page_size from bulletin_preferences where user_id=?",
            (user_id,),
        ).fetchone()


def parse_bulletin_preferences(prefs) -> tuple[set[str], set[str], set[str], int]:
    hidden_categories = set()
    hidden_areas = set()
    hidden_senders = set()
    page_size = 25

    if prefs:
        hidden_categories = {x.strip().upper() for x in (prefs["hidden_categories"] or "").split(",") if x.strip()}
        hidden_areas = {x.strip().upper() for x in (prefs["hidden_areas"] or "").split(",") if x.strip()}
        hidden_senders = {x.strip().upper() for x in (prefs["hidden_senders"] or "").split(",") if x.strip()}
        page_size = prefs["page_size"] or 25

    return hidden_categories, hidden_areas, hidden_senders, page_size


def save_bulletin_preferences_for_user(
    user_id: int,
    hidden_categories: list[str],
    hidden_areas: list[str],
    hidden_senders_text: str,
    page_size: int,
) -> None:
    cats = ",".join(sorted({c.strip().upper() for c in hidden_categories if c.strip()}))
    areas = ",".join(sorted({a.strip().upper() for a in hidden_areas if a.strip()}))
    senders = ",".join(sorted({s.strip().upper() for s in hidden_senders_text.replace(" ", ",").split(",") if s.strip()}))
    page_size = max(5, min(page_size, 100))

    with db() as conn:
        conn.execute(
            """
            insert into bulletin_preferences (user_id, hidden_categories, hidden_areas, hidden_senders, page_size)
            values (?, ?, ?, ?, ?)
            on conflict(user_id) do update set
                hidden_categories=excluded.hidden_categories,
                hidden_areas=excluded.hidden_areas,
                hidden_senders=excluded.hidden_senders,
                page_size=excluded.page_size
            """,
            (user_id, cats, areas, senders, page_size),
        )
        conn.commit()


def bulletin_preference_options() -> tuple[list[str], list[str], list[str]]:
    messages = LB_CACHE["messages"] if LB_CACHE["messages"] else []
    categories = sorted(set(m["category"] for m in messages if m["category"]))
    areas = sorted(set(m["area"] for m in messages if m["area"]))
    senders = sorted(set(m["from"] for m in messages if m["from"]))
    return categories, areas, senders

@app.get("/bulletins")
def bulletins(request: Request, page: int = Query(1, ge=1), refresh: int = Query(0), category: str = Query(""), q: str = Query("")):
    user = require_user(request)
    messages = []
    error = None
    raw_output = ""
    unmatched_lines = []

    if refresh != 1 and LB_CACHE["messages"] and time.time() - LB_CACHE["timestamp"] < LB_CACHE_SECONDS:
        messages = LB_CACHE["messages"]
        raw_output = LB_CACHE["raw_output"]
        unmatched_lines = LB_CACHE.get("unmatched_lines", [])
    else:
        try:
            messages, raw_output, unmatched_lines = fetch_bulletin_list(user)
            LB_CACHE["timestamp"] = time.time()
            LB_CACHE["messages"] = messages
            LB_CACHE["raw_output"] = raw_output
            LB_CACHE["unmatched_lines"] = unmatched_lines

        except Exception as e:
            error = f"Could not load bulletins from BBS: {e}"

    with db() as conn:
        prefs = conn.execute(
            "select hidden_categories, hidden_areas, hidden_senders, page_size from bulletin_preferences where user_id=?",
            (user["id"],),
        ).fetchone()

    messages, per_page = apply_bulletin_preferences(messages, prefs)

    category = category.strip().upper()
    q = q.strip()
    categories = sorted(set(m["category"] for m in messages if m["category"]))
    areas = sorted(set(m["area"] for m in messages if m["area"]))
    senders = sorted(set(m["from"] for m in messages if m["from"]))

    if category:
        messages = [m for m in messages if m["category"].strip().upper() == category]

    if q:
        body_cache = LB_CACHE.get("body_cache", {})
        messages = [
            m for m in messages
            if bulletin_matches_query(m, q, body_cache)
        ]

    message_ids = [str(m["id"]) for m in messages]
    read_ids = get_read_message_ids(user["id"], message_ids)
    unread_count = sum(1 for message_id in message_ids if message_id not in read_ids)

    total_messages = len(messages)
    total_pages = max(1, (total_messages + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_messages = [dict(m) for m in messages[start_idx:end_idx]]
    for m in page_messages:
        m["is_read"] = str(m["id"]) in read_ids

    return ttl_cache_set("bulletins", templates.TemplateResponse(
        "bulletins.html",
        {
            "request": request,
            "user": user,
            "messages": page_messages,
            "error": error,
            "raw_output": raw_output,
            "page": page,
            "total_pages": total_pages,
            "total_messages": total_messages,
            "unread_count": unread_count,
            "has_prev": page > 1,
            "has_next": page < total_pages,
            "prev_page": page - 1,
            "next_page": page + 1,
            "cache_age": int(time.time() - LB_CACHE["timestamp"]) if LB_CACHE["timestamp"] else None,
            "category": category,
            "q": q,
            "categories": categories,
            "areas": areas,
            "senders": senders,
            "unmatched_lines": unmatched_lines,
        },
    ))


@app.post("/bulletins/mark-visible-read")
def mark_visible_bulletins_read(
    request: Request,
    message_ids: list[str] = Form(default=[]),
    page: int = Form(default=1),
    category: str = Form(default=""),
    q: str = Form(default=""),
):
    user = require_user(request)
    mark_messages_read(user["id"], message_ids)

    params = {"page": page}
    if category.strip():
        params["category"] = category.strip()
    if q.strip():
        params["q"] = q.strip()

    return RedirectResponse("/bulletins?" + urlencode(params), status_code=303)


@app.get("/watchlists")
def watchlists(request: Request):
    user = require_user(request)
    watches = get_watch_lists(user["id"])
    messages = LB_CACHE["messages"] if "LB_CACHE" in globals() else []
    prefs = get_bulletin_preferences(user["id"])
    messages, _ = apply_bulletin_preferences(messages, prefs)
    watched_messages = [dict(m) for m in filter_watched_bulletins(messages, watches)]
    message_ids = [str(m["id"]) for m in watched_messages]
    read_ids = get_read_message_ids(user["id"], message_ids)
    unread_count = sum(1 for msg_id in message_ids if msg_id not in read_ids)

    for m in watched_messages:
        m["is_read"] = str(m["id"]) in read_ids

    return templates.TemplateResponse(
        "watchlists.html",
        {
            "request": request,
            "user": user,
            "watches": watches,
            "messages": watched_messages,
            "unread_count": unread_count,
            "cache_age": int(time.time() - LB_CACHE["timestamp"]) if "LB_CACHE" in globals() and LB_CACHE["timestamp"] else None,
            "error": None,
        },
    )


@app.post("/watchlists/add")
def add_watchlist(
    request: Request,
    watch_type: str = Form(...),
    watch_value: str = Form(...),
):
    user = require_user(request)
    watch_type = watch_type.strip().lower()
    watch_value = normalize_watch_value(watch_value)

    if watch_type in WATCH_FIELDS and watch_value:
        with db() as conn:
            conn.execute(
                """
                insert or ignore into watch_lists (user_id, watch_type, watch_value)
                values (?, ?, ?)
                """,
                (user["id"], watch_type, watch_value),
            )
            conn.commit()

    return RedirectResponse("/watchlists", status_code=303)


@app.post("/watchlists/delete/{watch_id}")
def delete_watchlist(request: Request, watch_id: int):
    user = require_user(request)

    with db() as conn:
        conn.execute(
            "delete from watch_lists where id=? and user_id=?",
            (watch_id, user["id"]),
        )
        conn.commit()

    return RedirectResponse("/watchlists", status_code=303)


@app.get("/notifications")
def notifications(request: Request):
    user = require_user(request)
    sync_notifications_for_user(user["id"])

    with db() as conn:
        rows = conn.execute(
            """
            select n.id, n.message_id, n.watch_id, n.title, n.body, n.is_read, n.created_at, n.read_at,
                   w.watch_type, w.watch_value
            from notifications n
            left join watch_lists w on w.id=n.watch_id
            where n.user_id=?
            order by n.is_read asc, n.created_at desc
            limit 100
            """,
            (user["id"],),
        ).fetchall()

    unread_count = sum(1 for row in rows if not row["is_read"])
    return templates.TemplateResponse(
        "notifications.html",
        {
            "request": request,
            "user": user,
            "notifications": rows,
            "unread_count": unread_count,
        },
    )


@app.post("/notifications/read/{notification_id}")
def mark_notification_read(request: Request, notification_id: int):
    user = require_user(request)

    with db() as conn:
        conn.execute(
            """
            update notifications
            set is_read=1, read_at=CURRENT_TIMESTAMP
            where id=? and user_id=?
            """,
            (notification_id, user["id"]),
        )
        conn.commit()

    return RedirectResponse("/notifications", status_code=303)


@app.post("/notifications/read-all")
def mark_all_notifications_read(request: Request):
    user = require_user(request)

    with db() as conn:
        conn.execute(
            """
            update notifications
            set is_read=1, read_at=CURRENT_TIMESTAMP
            where user_id=? and is_read=0
            """,
            (user["id"],),
        )
        conn.commit()

    return RedirectResponse("/notifications", status_code=303)


@app.get("/bulletins/preferences")
def bulletin_preferences(request: Request):
    user = require_user(request)
    categories, areas, senders = bulletin_preference_options()
    prefs = get_bulletin_preferences(user["id"])
    hidden_categories, hidden_areas, hidden_senders, page_size = parse_bulletin_preferences(prefs)

    return templates.TemplateResponse(
        "bulletin_preferences.html",
        {
            "request": request,
            "user": user,
            "categories": categories,
            "areas": areas,
            "senders": senders,
            "hidden_categories": hidden_categories,
            "hidden_areas": hidden_areas,
            "hidden_senders": hidden_senders,
            "page_size": page_size,
        },
    )


@app.post("/bulletins/preferences")
def save_bulletin_preferences(
    request: Request,
    hidden_categories: list[str] = Form(default=[]),
    hidden_areas: list[str] = Form(default=[]),
    hidden_senders_text: str = Form(default=""),
    page_size: int = Form(default=25),
):
    user = require_user(request)
    save_bulletin_preferences_for_user(user["id"], hidden_categories, hidden_areas, hidden_senders_text, page_size)

    return RedirectResponse("/bulletins", status_code=303)


@app.get("/profile")
def profile(request: Request, prefs_saved: int = Query(0)):
    user = require_user(request)
    categories, areas, senders = bulletin_preference_options()
    prefs = get_bulletin_preferences(user["id"])
    hidden_categories, hidden_areas, hidden_senders, page_size = parse_bulletin_preferences(prefs)

    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "user": user,
            "categories": categories,
            "areas": areas,
            "senders": senders,
            "hidden_categories": hidden_categories,
            "hidden_areas": hidden_areas,
            "hidden_senders": hidden_senders,
            "page_size": page_size,
            "prefs_message": "Preferences saved." if prefs_saved else None,
            "password_error": None,
            "password_message": None,
        },
    )


@app.post("/profile/preferences")
def profile_save_preferences(
    request: Request,
    hidden_categories: list[str] = Form(default=[]),
    hidden_areas: list[str] = Form(default=[]),
    hidden_senders_text: str = Form(default=""),
    page_size: int = Form(default=25),
):
    user = require_user(request)
    save_bulletin_preferences_for_user(user["id"], hidden_categories, hidden_areas, hidden_senders_text, page_size)
    return RedirectResponse("/profile?prefs_saved=1", status_code=303)


@app.post("/profile/password")
def profile_change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    user = require_user(request)
    categories, areas, senders = bulletin_preference_options()
    prefs = get_bulletin_preferences(user["id"])
    hidden_categories, hidden_areas, hidden_senders, page_size = parse_bulletin_preferences(prefs)

    password_error = None
    password_message = None

    if not pwd_context.verify(current_password, user["password_hash"]):
        password_error = "Current password is incorrect."
    elif len(new_password) < 8:
        password_error = "New password must be at least 8 characters."
    elif new_password != confirm_password:
        password_error = "New password and confirmation do not match."
    else:
        with db() as conn:
            conn.execute(
                "update users set password_hash=? where id=?",
                (pwd_context.hash(new_password), user["id"]),
            )
            conn.commit()
        password_message = "Password updated."

    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "user": user,
            "categories": categories,
            "areas": areas,
            "senders": senders,
            "hidden_categories": hidden_categories,
            "hidden_areas": hidden_areas,
            "hidden_senders": hidden_senders,
            "page_size": page_size,
            "prefs_message": None,
            "password_error": password_error,
            "password_message": password_message,
        },
    )


@app.get("/bulletin/{msg_id}")
def read_bulletin(request: Request, msg_id: int):
    user = require_user(request)
    error = None
    body = ""

    try:
        body = read_bulletin_body(user, msg_id)
        if body:
            LB_CACHE.setdefault("body_cache", {})[str(msg_id)] = body
        mark_messages_read(user["id"], [str(msg_id)])

    except Exception as e:
        error = f"Could not read bulletin {msg_id}: {e}"

    return templates.TemplateResponse(
        "bulletin_read.html",
        {
            "request": request,
            "user": user,
            "msg_id": msg_id,
            "body": body,
            "error": error,
        },
    )


@app.get("/mheard")
def mheard(request: Request, port: str = Query("all")):
    user = get_session_user(request)
    ports = {
        "1": "AX/IP/UDP",
        "2": "Internet Gateway",
        "4": "Users-145.09",
        "8": "HF 20/40/80m VARA",
        "9": "Net44",
        "10": "AREDN",
    }
    error = None

    if port != "all" and port not in ports:
        port = "all"

    if not MHEARD_CACHE["heard"] or time.time() - MHEARD_CACHE["timestamp"] > MHEARD_CACHE_SECONDS:
        service_user = get_bpq_service_user()
        if service_user:
            try:
                refresh_mheard_cache(service_user, ports)
            except Exception as e:
                error = f"Could not refresh MHeard cache: {e}"
        else:
            error = "No approved BPQ account is available to refresh the MHeard cache."

    heard = MHEARD_CACHE["heard"]
    if port != "all":
        heard = [h for h in heard if h["port"] == port]

    return templates.TemplateResponse(
        "mheard.html",
        {
            "request": request,
            "user": user,
            "ports": ports,
            "selected_port": port,
            "heard": heard,
            "error": error,
            "cache_age": int(time.time() - MHEARD_CACHE["timestamp"]) if MHEARD_CACHE["timestamp"] else None,
        },
    )


def refresh_bulletin_cache_background():
    while True:
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            user = conn.execute(
                "select * from users where is_admin=1 and approved=1 order by id limit 1"
            ).fetchone()
            conn.close()

            if user:
                messages, raw_output, unmatched_lines = fetch_bulletin_list(user)
                LB_CACHE["timestamp"] = time.time()
                LB_CACHE["messages"] = messages
                LB_CACHE["raw_output"] = raw_output
                LB_CACHE["unmatched_lines"] = unmatched_lines

                print(f"Bulletin cache refreshed: {len(messages)} messages, {len(unmatched_lines)} unmatched lines")

        except Exception as e:
            print(f"Bulletin cache refresh failed: {e}")

        time.sleep(300)


@app.on_event("startup")
def start_bulletin_cache_worker():
    t = threading.Thread(target=refresh_bulletin_cache_background, daemon=True)
    t.start()
    print("Bulletin cache background worker started")


@app.get("/connections")
def connections(request: Request):
    user = get_session_user(request)
    error = None

    if not CONNECTIONS_CACHE["timestamp"] or time.time() - CONNECTIONS_CACHE["timestamp"] > CONNECTIONS_CACHE_SECONDS:
        service_user = get_bpq_service_user()
        if service_user:
            try:
                refresh_connections_cache(service_user)
            except Exception as e:
                error = f"Could not refresh connections cache: {e}"
        else:
            error = "No approved BPQ account is available to refresh the connections cache."

    return templates.TemplateResponse(
        "connections.html",
        {
            "request": request,
            "user": user,
            "uplinks": CONNECTIONS_CACHE["uplinks"],
            "circuits": CONNECTIONS_CACHE["circuits"],
            "lines": CONNECTIONS_CACHE["lines"],
            "error": error,
            "cache_age": int(time.time() - CONNECTIONS_CACHE["timestamp"]) if CONNECTIONS_CACHE["timestamp"] else None,
        },
    )


@app.get("/node")
def node_status(request: Request):
    user = get_session_user(request)
    error = None

    if not NODE_STATUS_CACHE["timestamp"] or time.time() - NODE_STATUS_CACHE["timestamp"] > NODE_STATUS_CACHE_SECONDS:
        service_user = get_bpq_service_user()
        if service_user:
            try:
                refresh_node_status_cache(service_user)
            except Exception as e:
                error = f"Could not refresh node status cache: {e}"
        else:
            error = "No approved BPQ account is available to refresh the node status cache."

    bulletin_count = len(LB_CACHE["messages"]) if "LB_CACHE" in globals() else 0
    bulletin_cache_age = int(time.time() - LB_CACHE["timestamp"]) if "LB_CACHE" in globals() and LB_CACHE["timestamp"] else None
    latest_bulletin = LB_CACHE["messages"][0]["id"] if "LB_CACHE" in globals() and LB_CACHE["messages"] else "Unknown"

    return templates.TemplateResponse(
        "node.html",
        {
            "request": request,
            "user": user,
            "error": error,
            "ports": NODE_STATUS_CACHE["ports"],
            "version_line": NODE_STATUS_CACHE["version_line"],
            "connection_count": NODE_STATUS_CACHE["connection_count"],
            "bulletin_count": bulletin_count,
            "bulletin_cache_age": bulletin_cache_age,
            "latest_bulletin": latest_bulletin,
            "raw_ports": NODE_STATUS_CACHE["raw_ports"],
            "raw_users": NODE_STATUS_CACHE["raw_users"],
            "node_cache_age": int(time.time() - NODE_STATUS_CACHE["timestamp"]) if NODE_STATUS_CACHE["timestamp"] else None,
        },
    )


@app.get("/ports")
def ports(request: Request):
    user = get_session_user(request)
    error = None

    if not NODE_STATUS_CACHE["timestamp"] or time.time() - NODE_STATUS_CACHE["timestamp"] > NODE_STATUS_CACHE_SECONDS:
        service_user = get_bpq_service_user()
        if service_user:
            try:
                refresh_node_status_cache(service_user)
            except Exception as e:
                error = f"Could not refresh ports cache: {e}"
        else:
            error = "No approved BPQ account is available to refresh the ports cache."

    return templates.TemplateResponse(
        "ports.html",
        {
            "request": request,
            "user": user,
            "error": error,
            "ports": NODE_STATUS_CACHE["ports"],
            "raw_ports": NODE_STATUS_CACHE["raw_ports"],
            "node_cache_age": int(time.time() - NODE_STATUS_CACHE["timestamp"]) if NODE_STATUS_CACHE["timestamp"] else None,
        },
    )



NODE_CACHE = {
    "timestamp": 0,
    "nodes": [],
    "raw_output": "",
}

NODE_CACHE_SECONDS = 300

@app.get("/nodes")
def nodes(request: Request, q: str = Query(""), page: int = Query(1, ge=1), refresh: int = Query(0)):
    user = get_session_user(request)
    error = None
    nodes = NODE_CACHE["nodes"]
    raw_output = NODE_CACHE["raw_output"]

    now = time.time()

    try:
        cache_valid = (
            (refresh != 1 or not user)
            and NODE_CACHE["nodes"]
            and now - NODE_CACHE["timestamp"] < NODE_CACHE_SECONDS
        )

        if not cache_valid:
            service_user = user if user and user["approved"] else get_bpq_service_user()
            if service_user:
                refresh_nodes_cache(service_user)
            else:
                error = "No approved BPQ account is available to refresh the node cache."

        nodes = NODE_CACHE["nodes"]
        raw_output = NODE_CACHE["raw_output"]
        q_clean = q.strip().lower()
        if q_clean:
            nodes = [
                n for n in nodes
                if q_clean in n["alias"].lower()
                or q_clean in n["callsign"].lower()
            ]

    except Exception as e:
        error = f"Could not load nodes: {e}"

    per_page = 25
    total_nodes = len(nodes)
    total_pages = max(1, (total_nodes + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_nodes = nodes[start_idx:end_idx]

    cache_age = int(time.time() - NODE_CACHE["timestamp"]) if NODE_CACHE["timestamp"] else None

    return templates.TemplateResponse(
        "nodes.html",
        {
            "request": request,
            "user": user,
            "nodes": page_nodes,
            "q": q,
            "error": error,
            "raw_output": raw_output,
            "total_nodes": total_nodes,
            "page": page,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
            "prev_page": page - 1,
            "next_page": page + 1,
            "cache_age": cache_age,
            "cache_seconds": NODE_CACHE_SECONDS,
        },
    )




@app.get("/admin/password-resets")
def admin_password_resets(request: Request):
    user = require_admin(request)

    with db() as conn:
        requests = conn.execute(
            """
            select id, username, callsign, message, handled, created_at, handled_at
            from password_reset_requests
            order by handled asc, created_at desc
            """
        ).fetchall()

    return templates.TemplateResponse(
        "admin_password_resets.html",
        {"request": request, "user": user, "requests": requests},
    )


@app.post("/admin/password-resets/handled/{request_id}")
def admin_password_reset_mark_handled(request: Request, request_id: int):
    require_admin(request)

    with db() as conn:
        conn.execute(
            """
            update password_reset_requests
            set handled=1, handled_at=CURRENT_TIMESTAMP
            where id=?
            """,
            (request_id,),
        )
        conn.commit()

    return RedirectResponse("/admin/password-resets", status_code=303)


@app.post("/admin/password-resets/delete/{request_id}")
def admin_password_reset_delete(request: Request, request_id: int):
    require_admin(request)

    with db() as conn:
        conn.execute("delete from password_reset_requests where id=?", (request_id,))
        conn.commit()

    return RedirectResponse("/admin/password-resets", status_code=303)


@app.post("/admin/users/new")
def admin_users_new(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    callsign: str = Form(...),
    bpq_user: str = Form(...),
    bpq_password: str = Form(...),
):
    require_admin(request)

    username = username.strip()
    callsign = callsign.strip().upper()
    bpq_user = bpq_user.strip()

    with db() as conn:
        conn.execute(
            """
            INSERT INTO users
            (username, password_hash, callsign, bpq_user, bpq_password, approved, is_admin)
            VALUES (?, ?, ?, ?, ?, 1, 0)
            """,
            (
                username,
                pwd_context.hash(password),
                callsign,
                bpq_user,
                bpq_password,
            ),
        )

    return RedirectResponse("/admin/users", status_code=303)


@app.get("/admin/users")
def admin_users(request: Request):
    user = require_user(request)
    if not user["is_admin"]:
        return RedirectResponse("/", status_code=303)

    with db() as conn:
        users = conn.execute(
            "select id, username, callsign, bpq_user, approved, is_admin from users order by username"
        ).fetchall()

    return templates.TemplateResponse(
        "admin_users.html",
        {"request": request, "user": user, "users": users},
    )

@app.get("/admin/users/edit/{user_id}")
def admin_edit_user_form(request: Request, user_id: int):
    admin = require_user(request)
    if not admin["is_admin"]:
        return RedirectResponse("/dashboard", status_code=303)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    user = conn.execute("select * from users where id=?", (user_id,)).fetchone()
    conn.close()

    if not user:
        return RedirectResponse("/admin/users", status_code=303)

    return templates.TemplateResponse(
        "admin_user_edit.html",
        {"request": request, "user": admin, "edit_user": user},
    )


@app.post("/admin/users/edit/{user_id}")
def admin_edit_user_save(
    request: Request,
    user_id: int,
    username: str = Form(...),
    callsign: str = Form(...),
    bpq_user: str = Form(...),
    bpq_password: str = Form(""),
    approved: str = Form(None),
    is_admin: str = Form(None),
):
    admin = require_user(request)
    if not admin["is_admin"]:
        return RedirectResponse("/dashboard", status_code=303)

    conn = sqlite3.connect(DB_PATH)

    if bpq_password.strip():
        conn.execute(
            """update users
               set username=?, callsign=?, bpq_user=?, bpq_password=?, approved=?, is_admin=?
               where id=?""",
            (
                username.strip(),
                callsign.strip().upper(),
                bpq_user.strip().upper(),
                bpq_password.strip(),
                1 if approved else 0,
                1 if is_admin else 0,
                user_id,
            ),
        )
    else:
        conn.execute(
            """update users
               set username=?, callsign=?, bpq_user=?, approved=?, is_admin=?
               where id=?""",
            (
                username.strip(),
                callsign.strip().upper(),
                bpq_user.strip().upper(),
                1 if approved else 0,
                1 if is_admin else 0,
                user_id,
            ),
        )

    conn.commit()
    conn.close()
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/delete/{user_id}")
def admin_delete_user(request: Request, user_id: int):
    admin = require_user(request)
    if not admin["is_admin"]:
        return RedirectResponse("/dashboard", status_code=303)

    # Do not allow deleting yourself.
    if admin["id"] == user_id:
        return RedirectResponse("/admin/users", status_code=303)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("delete from users where id=?", (user_id,))
    conn.commit()
    conn.close()

    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/toggle-admin/{user_id}")
def admin_toggle_admin(request: Request, user_id: int):
    admin = require_user(request)
    if not admin["is_admin"]:
        return RedirectResponse("/dashboard", status_code=303)

    # Do not allow removing your own admin status.
    if admin["id"] == user_id:
        return RedirectResponse("/admin/users", status_code=303)

    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        user = conn.execute("select is_admin from users where id=?", (user_id,)).fetchone()
        if user:
            conn.execute(
                "update users set is_admin=? where id=?",
                (0 if user["is_admin"] else 1, user_id),
            )
            conn.commit()
    finally:
        conn.close()

    return RedirectResponse("/admin/users", status_code=303)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    user = conn.execute("select is_admin from users where id=?", (user_id,)).fetchone()

    if user:
        conn.execute(
            "update users set is_admin=? where id=?",
            (0 if user["is_admin"] else 1, user_id),
        )

        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_to_user ON messages(to_user)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_from_user ON messages(from_user)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at)")
        conn.commit()

    conn.close()
    return RedirectResponse("/admin/users", status_code=303)
