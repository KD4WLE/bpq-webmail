"""FastAPI integration for BPQ Portal usage analytics.

This module keeps analytics middleware and admin routes separate from the
large application module. ``install()`` is idempotent and is called once from
``app.py`` by the installer migration.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import analytics
import config


def install(
    app: FastAPI,
    templates: Jinja2Templates,
    db_path: Path,
    get_session_user: Callable,
    require_admin: Callable,
) -> None:
    if getattr(app.state, "usage_analytics_installed", False):
        return
    app.state.usage_analytics_installed = True

    @app.on_event("startup")
    def initialize_usage_analytics() -> None:
        with analytics.connect(db_path) as conn:
            analytics.init_db(conn)

        if config.ANALYTICS_ENABLED and config.ANALYTICS_RETENTION_DAYS > 0:
            try:
                removed = analytics.prune_old_requests(
                    db_path,
                    config.ANALYTICS_RETENTION_DAYS,
                )
                if removed:
                    print(
                        "Usage analytics retention removed "
                        f"{removed} old requests."
                    )
            except Exception as exc:
                print(f"Usage analytics retention failed: {exc}")

    @app.middleware("http")
    async def usage_analytics_middleware(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)

        if not config.ANALYTICS_ENABLED:
            return response

        path = request.url.path
        user_agent = request.headers.get("user-agent", "")
        if analytics.is_ignored_path(path, config.ANALYTICS_IGNORE_PATHS):
            return response
        if config.ANALYTICS_IGNORE_BOTS and analytics.is_bot(user_agent):
            return response

        try:
            user = get_session_user(request)
            forwarded_for = request.headers.get("x-forwarded-for", "")
            client_ip = forwarded_for.split(",")[0].strip()
            if not client_ip and request.client:
                client_ip = request.client.host

            analytics.record_request(
                db_path,
                path=path,
                method=request.method,
                status_code=response.status_code,
                response_ms=round(
                    (time.perf_counter() - started) * 1000,
                    2,
                ),
                user_id=user["id"] if user else None,
                callsign=user["callsign"] if user else None,
                client_ip=client_ip,
                user_agent=user_agent,
                secret=config.SESSION_SECRET,
            )
        except Exception as exc:
            print(f"Usage analytics write failed: {exc}")

        return response

    @app.get("/admin/usage", response_class=HTMLResponse)
    def admin_usage(
        request: Request,
        days: int = Query(30, ge=1, le=3650),
    ):
        user = require_admin(request)
        report = analytics.get_usage_report(db_path, days)
        return templates.TemplateResponse(
            "admin_usage.html",
            {
                "request": request,
                "user": user,
                "days": days,
                "retention_days": config.ANALYTICS_RETENTION_DAYS,
                **report,
            },
        )
