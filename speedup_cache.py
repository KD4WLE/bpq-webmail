from pathlib import Path

p = Path("app.py")
text = p.read_text()

# Add imports if needed
if "import time" not in text:
    text = text.replace("import os\n", "import os\nimport time\n", 1)

cache_block = '''
# Simple in-process TTL cache for slow BPQ/telnet views
_CACHE = {}

def ttl_cache_get(key, ttl_seconds):
    item = _CACHE.get(key)
    if not item:
        return None
    ts, value = item
    if time.time() - ts > ttl_seconds:
        _CACHE.pop(key, None)
        return None
    return value

def ttl_cache_set(key, value):
    _CACHE[key] = (time.time(), value)
    return value

'''

if "def ttl_cache_get(" not in text:
    marker = "app = FastAPI"
    idx = text.find(marker)
    text = text[:idx] + cache_block + text[idx:]
    print("Added TTL cache helpers")
else:
    print("TTL cache helpers already present")

p.write_text(text)
