from pathlib import Path

p = Path("app.py")
text = p.read_text()

old = '''            for line in raw_output.splitlines():
                clean = line.strip()

                if not clean:
                    continue

                if clean.startswith("TITUS1:") or clean == "Nodes":
                    continue

                for token in clean.split():'''

new = '''            in_nodes_section = False

            for line in raw_output.splitlines():
                clean = line.strip()

                if not clean:
                    continue

                if clean == "Nodes" or clean.endswith(":Nodes"):
                    in_nodes_section = True
                    continue

                if not in_nodes_section:
                    continue

                if clean.startswith("TITUS1:"):
                    continue

                for token in clean.split():'''

if old in text:
    text = text.replace(old, new, 1)
    p.write_text(text)
    print("Updated /nodes parser to ignore telnet login banner")
else:
    print("Could not find expected /nodes parser block")
