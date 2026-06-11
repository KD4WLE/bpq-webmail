from pathlib import Path

p = Path("app.py")
text = p.read_text()

# Remove TemplateResponse-level TTL cache from /nodes because q/page/refresh can vary.
text = text.replace(
'''def nodes(request: Request, q: str = Query(""), page: int = Query(1, ge=1), refresh: int = Query(0)):
    cached = ttl_cache_get("nodes", 30)
    if cached is not None:
        return cached

    user = require_user(request)''',
'''def nodes(request: Request, q: str = Query(""), page: int = Query(1, ge=1), refresh: int = Query(0)):
    user = require_user(request)'''
)

old = '''            tn = telnetlib.Telnet(BPQ_POP3_HOST, 8010, timeout=15)

            tn.read_until(b"Username:", timeout=10)
            tn.write((user["bpq_user"] + "\\r").encode())

            tn.read_until(b"Password:", timeout=10)
            tn.write((user["bpq_password"] + "\\r").encode())

            time.sleep(1)
            tn.read_very_eager()

            tn.write(b"nodes\\r")
            time.sleep(4)
            raw_output = tn.read_very_eager().decode(errors="ignore")

            tn.write(b"bye\\r")
            tn.close()
'''

new = '''            raw_output = bpq_command(user, "nodes", timeout=15, settle=1.0)
'''

if old in text:
    text = text.replace(old, new)
    print("Optimized /nodes route to use bpq_command")
else:
    print("Could not find old /nodes telnet block; no replacement made")

p.write_text(text)
