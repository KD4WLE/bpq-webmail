from pathlib import Path

p = Path("app.py")
text = p.read_text()

if "def bpq_command(" in text:
    print("bpq_command helper already exists")
else:
    helper = r'''
def bpq_command(user, commands, timeout=10, settle=0.5):
    """Run one or more BPQ telnet commands and return combined output."""
    start = time.time()
    output = ""
    if isinstance(commands, str):
        commands = [commands]

    tn = telnetlib.Telnet(BPQ_POP3_HOST, 8010, timeout=timeout)
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
        print(f"BPQ commands {commands} took {time.time() - start:.2f}s")
        return output
    finally:
        try:
            tn.close()
        except Exception:
            pass

'''
    marker = "def ttl_cache_get("
    idx = text.find(marker)
    if idx == -1:
        raise SystemExit("Could not find ttl_cache_get marker")
    text = text[:idx] + helper + "\n" + text[idx:]
    p.write_text(text)
    print("Inserted bpq_command helper")

