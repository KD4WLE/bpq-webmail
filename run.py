"""Production application entry point.

Import the existing BPQ Portal application, then attach optional integrations
that are intentionally kept outside the large legacy ``app.py`` module.
"""

import app as portal
import usage_analytics_integration


usage_analytics_integration.install(
    portal.app,
    portal.templates,
    portal.DB_PATH,
    portal.get_session_user,
    portal.require_admin,
)

app = portal.app
