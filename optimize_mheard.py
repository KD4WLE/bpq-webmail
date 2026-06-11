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

        for pnum in selected_ports:
            if pnum not in ports:
                continue

            tn.write((f"mh {pnum}\\r").encode())
            time.sleep(1)
            output = tn.read_very_eager().decode(errors="ignore")

            for line in output.splitlines():
                parts = line.split()
                if len(parts) >= 2 and ":" in parts[1]:
                    heard.append({
                        "port": pnum,
                        "port_name": ports[pnum],
                        "callsign": parts[0],
                        "last_heard": parts[1],
                        "extra": " ".join(parts[2:]) if len(parts) > 2 else "",
                    })

        tn.write(b"bye\\r")
        tn.close()
'''

new = '''        for pnum in selected_ports:
            if pnum not in ports:
                continue

            output = bpq_command(user, f"mh {pnum}", timeout=10, settle=1.0)

            for line in output.splitlines():
                parts = line.split()

                if len(parts) >= 2 and ":" in parts[1]:
                    heard.append({
                        "port": pnum,
                        "port_name": ports[pnum],
                        "callsign": parts[0],
                        "last_heard": parts[1],
                        "extra": " ".join(parts[2:]) if len(parts) > 2 else "",
                    })
'''

if old in text:
    text = text.replace(old, new, 1)
    print("Optimized /mheard route")
else:
    print("Could not find expected mheard block")

p.write_text(text)
