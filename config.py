import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def env(name: str, default_value: str) -> str:
    return os.environ.get(name, default_value)


def env_bool(name: str, default_value: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default_value
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default_value: int) -> int:
    return int(env(name, str(default_value)))


def env_list(name: str, default_value: str = "") -> tuple[str, ...]:
    value = env(name, default_value)
    return tuple(item.strip() for item in value.split(",") if item.strip())


def env_path(name: str, default_value: Path) -> Path:
    value = os.environ.get(name)
    if not value:
        return default_value
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


SITE_NAME = env("SITE_NAME", "KD4WLE BPQ Portal")
SITE_TITLE = env("SITE_TITLE", SITE_NAME)
SITE_SUBTITLE = env("SITE_SUBTITLE", "Packet Radio Messaging Interface")
SITE_FOOTER_TEXT = env("SITE_FOOTER_TEXT", "Packet Radio Messaging")
BRAND_SUBTITLE = env("BRAND_SUBTITLE", "BPQ Portal")
BRAND_LOGO_PATH = env("BRAND_LOGO_PATH", "/static/img/logo.png")
BRAND_LOGO_ALT = env("BRAND_LOGO_ALT", SITE_NAME)
CONTACT_FORM_URL = env("CONTACT_FORM_URL", "https://kd4wle.net/contact-form")
CONTACT_FORM_LABEL = env("CONTACT_FORM_LABEL", "Contact Sysop / Request Account")
NODE_CALLSIGN = env("NODE_CALLSIGN", "TITUS1")

DB_PATH = env_path("DATABASE_PATH", BASE_DIR / "bpq_webmail.db")
SESSION_SECRET = env("SESSION_SECRET", "replace-with-a-long-random-secret")

BPQ_HOST = env("BPQ_HOST", env("BPQ_POP3_HOST", "127.0.0.1"))
BPQ_TELNET_HOST = env("BPQ_TELNET_HOST", BPQ_HOST)
BPQ_TELNET_PORT = env_int("BPQ_TELNET_PORT", 8010)
BPQ_POP3_HOST = env("BPQ_POP3_HOST", BPQ_HOST)
BPQ_POP3_PORT = env_int("BPQ_POP3_PORT", 110)
BPQ_SMTP_HOST = env("BPQ_SMTP_HOST", BPQ_HOST)
BPQ_SMTP_PORT = env_int("BPQ_SMTP_PORT", 25)

WEB_BIND_HOST = env("WEB_BIND_HOST", "127.0.0.1")
WEB_BIND_PORT = env_int("WEB_BIND_PORT", 8088)

PORTAL_TAGLINE = env("PORTAL_TAGLINE", f"Sent via the {SITE_NAME}")
APP_VERSION = env("APP_VERSION", "v0.55.2 Alpha")

FIRST_RUN_SETUP_ENABLED = env_bool("FIRST_RUN_SETUP_ENABLED", True)
FIRST_RUN_SETUP_TOKEN = env("FIRST_RUN_SETUP_TOKEN", "")
AUTO_CREATE_ADMIN = env_bool("AUTO_CREATE_ADMIN", False)
APP_ADMIN_USERNAME = env("APP_ADMIN_USERNAME", "")
APP_ADMIN_PASSWORD = env("APP_ADMIN_PASSWORD", "")
APP_ADMIN_CALLSIGN = env("APP_ADMIN_CALLSIGN", "ADMIN")
APP_ADMIN_BPQ_USER = env("APP_ADMIN_BPQ_USER", "ADMIN")
APP_ADMIN_BPQ_PASSWORD = env("APP_ADMIN_BPQ_PASSWORD", "")

COMPOSE_PRIVATE_COMMAND = env("BPQ_COMPOSE_PRIVATE_COMMAND", "sp")
COMPOSE_BULLETIN_COMMAND = env("BPQ_COMPOSE_BULLETIN_COMMAND", "sb")
COMPOSE_NTS_COMMAND = env("BPQ_COMPOSE_NTS_COMMAND", "st")
COMPOSE_WINLINK_COMMAND = env("BPQ_COMPOSE_WINLINK_COMMAND", "sp")

# Privacy-conscious portal usage analytics.
ANALYTICS_ENABLED = env_bool("ANALYTICS_ENABLED", True)
ANALYTICS_IGNORE_PATHS = env_list(
    "ANALYTICS_IGNORE_PATHS",
    "/static*,/favicon.ico,/health",
)
ANALYTICS_IGNORE_BOTS = env_bool("ANALYTICS_IGNORE_BOTS", True)
ANALYTICS_RETENTION_DAYS = env_int("ANALYTICS_RETENTION_DAYS", 365)
