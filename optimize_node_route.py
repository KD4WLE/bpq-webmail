from pathlib import Path

p = Path("app.py")
text = p.read_text()

old = '''        tn = telnetlib.Telnet(BPQ_POP3_HOST, 8010, timeout=10)

        tn.read_until(b"Username:", timeout=10)
        tn.write((user["bpq_user"] + "\\r").encode())

        tn.read_until(b"Password:", timeout=10)
        tn.write((user["bpq_password"] + "\\r").encode())

        time.sleep(1)
        tn.read_very_eager()

        tn.write(b"po\\r")
        time.sleep(1)
        raw_ports = tn.read_very_eager().decode(errors="ignore")

        tn.write(b"users\\r")
        time.sleep(1)
        raw_users = tn.read_very_eager().decode(errors="ignore")

        tn.write(b"bye\\r")
        tn.close()
'''

new = '''        combined_output = bpq_command(user, ["po", "users"], timeout=10, settle=1.0)

        marker = "users"
        if marker in combined_output.lower():
            # Best effort split: keep full combined output available if prompt text varies.
            raw_ports = combined_output
            raw_users = combined_output
        else:
            raw_ports = combined_output
            raw_users = combined_output
'''

if old in text:
    text = text.replace(old, new, 1)
    print("Optimized /node route to use bpq_command")
else:
    print("Could not find /node telnet block; no replacement made")

text = text.replace(
'''    return ttl_cache_set("nodes", templates.TemplateResponse(
        "nodes.html",''',
'''    return templates.TemplateResponse(
        "nodes.html",'''
)

text = text.replace(
'''        },
    ))''',
'''        },
    )''',
1
)

p.write_text(text)
