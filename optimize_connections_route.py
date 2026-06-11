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

        tn.write(b"users\\r")
        time.sleep(2)
        output = tn.read_very_eager().decode(errors="ignore")

        tn.write(b"bye\\r")
        tn.close()
'''

new = '''        output = bpq_command(user, "users", timeout=10, settle=1.0)
'''

if old in text:
    text = text.replace(old, new, 1)
    print("Optimized /connections route to use bpq_command")
else:
    print("Could not find /connections telnet block; no replacement made")

p.write_text(text)
