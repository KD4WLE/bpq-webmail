from pathlib import Path

p = Path("app.py")
text = p.read_text()

text = text.replace(
'''                if clean == "Nodes" or clean.endswith(":Nodes"):
                    in_nodes_section = True
                    continue''',
'''                if clean == "Nodes" or clean.endswith("} Nodes") or clean.endswith(":Nodes") or clean.lower().endswith(" nodes"):
                    in_nodes_section = True
                    continue'''
)

p.write_text(text)
