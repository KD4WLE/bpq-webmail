from pathlib import Path

p = Path("app.py")
text = p.read_text()

# Add helper if missing
helper = '''
def log_elapsed(label, start):
    try:
        print(f"PERF {label}: {time.time() - start:.2f}s")
    except Exception:
        pass

'''

if "def log_elapsed(" not in text:
    marker = "def bpq_command("
    text = text.replace(marker, helper + "\n" + marker, 1)
    print("Added log_elapsed helper")

# Add better timing inside bpq_command
text = text.replace(
'''        tn = telnetlib.Telnet(BPQ_POP3_HOST, 8010, timeout=timeout)''',
'''        connect_start = time.time()
    tn = telnetlib.Telnet(BPQ_POP3_HOST, 8010, timeout=timeout)
    log_elapsed("bpq connect", connect_start)''',
1
)

text = text.replace(
'''        print(f"BPQ commands {commands} took {time.time() - start:.2f}s")
        return output''',
'''        log_elapsed(f"bpq commands {commands}", start)
        return output''',
1
)

p.write_text(text)
