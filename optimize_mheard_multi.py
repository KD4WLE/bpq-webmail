from pathlib import Path

p = Path("app.py")
text = p.read_text()

old = '''            output = bpq_command(user, f"mh {pnum}", timeout=10, settle=1.0)'''

new = '''            output = bpq_command(user, f"mh {pnum}", timeout=10, settle=0.4)'''

text = text.replace(old, new)

# Also make mheard cache key include selected port
text = text.replace(
'''    cached = ttl_cache_get("mheard", 20)
    if cached is not None:
        return cached''',
'''    cache_key = f"mheard:{port}"
    cached = ttl_cache_get(cache_key, 30)
    if cached is not None:
        return cached'''
)

text = text.replace(
'''    return ttl_cache_set("mheard", templates.TemplateResponse(''',
'''    return ttl_cache_set(cache_key, templates.TemplateResponse('''
)

p.write_text(text)
