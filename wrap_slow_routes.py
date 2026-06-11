from pathlib import Path

p = Path("app.py")
text = p.read_text()

repls = {
    "@app.get(\"/bulletins\")\ndef bulletins(": (
        "@app.get(\"/bulletins\")\ndef bulletins(",
        "bulletins",
        30,
    ),
    "@app.get(\"/mheard\")\ndef mheard(": (
        "@app.get(\"/mheard\")\ndef mheard(",
        "mheard",
        20,
    ),
    "@app.get(\"/connections\")\ndef connections(": (
        "@app.get(\"/connections\")\ndef connections(",
        "connections",
        10,
    ),
    "@app.get(\"/node\")\ndef node(": (
        "@app.get(\"/node\")\ndef node(",
        "node",
        10,
    ),
    "@app.get(\"/nodes\")\ndef nodes(": (
        "@app.get(\"/nodes\")\ndef nodes(",
        "nodes",
        30,
    ),
}

for marker, (_, key, ttl) in repls.items():
    idx = text.find(marker)
    if idx == -1:
        print(f"Missing route marker for {key}; skipped")
        continue

    body_start = text.find(":\n", idx) + 2
    next_line = body_start
    insert = (
        f'    cached = ttl_cache_get("{key}", {ttl})\n'
        f'    if cached is not None:\n'
        f'        return cached\n\n'
    )

    route_chunk = text[idx:text.find("@app.", body_start) if text.find("@app.", body_start) != -1 else len(text)]

    if f'ttl_cache_get("{key}"' in route_chunk:
        print(f"{key} already cached")
        continue

    text = text[:next_line] + insert + text[next_line:]
    print(f"Added cache read to {key}")

# Wrap TemplateResponse returns in selected functions.
# This only changes returns after our cache markers.
for key in ["bulletins", "mheard", "connections", "node", "nodes"]:
    marker = f'ttl_cache_get("{key}"'
    start = text.find(marker)
    if start == -1:
        continue
    end = text.find("@app.", start)
    if end == -1:
        end = len(text)
    chunk = text[start:end]

    if f"ttl_cache_set(\"{key}\"" in chunk:
        print(f"{key} return already wrapped")
        continue

    old = "    return templates.TemplateResponse("
    pos = chunk.rfind(old)
    if pos == -1:
        print(f"No TemplateResponse return found for {key}; skipped return wrap")
        continue

    abs_pos = start + pos
    text = text[:abs_pos] + f"    return ttl_cache_set(\"{key}\", templates.TemplateResponse(" + text[abs_pos + len(old):]

    # close one extra paren at the end of that return block by replacing first line that is exactly indented closing paren
    scan_start = abs_pos
    close_pos = text.find("\n    )", scan_start)
    if close_pos != -1 and close_pos < end + 2000:
        text = text[:close_pos] + "\n    ))" + text[close_pos + len("\n    )"):]
        print(f"Wrapped return for {key}")
    else:
        print(f"Could not find close paren for {key}")

p.write_text(text)
