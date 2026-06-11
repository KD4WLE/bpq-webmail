from pathlib import Path

p = Path("app.py")
text = p.read_text()

# Add SQLite performance pragmas inside db()
old = '''conn.row_factory = sqlite3.Row
        return conn'''
new = '''conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn'''

if old in text and "PRAGMA journal_mode=WAL" not in text:
    text = text.replace(old, new)
    print("Added SQLite WAL/busy_timeout pragmas")
else:
    print("SQLite pragmas already present or db() layout differs")

# Add indexes near CREATE TABLE section
indexes = '''
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_to_user ON messages(to_user)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_from_user ON messages(from_user)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at)")
'''

if "idx_users_username" not in text:
    marker = "        conn.commit()"
    text = text.replace(marker, indexes + marker, 1)
    print("Added DB indexes")
else:
    print("Indexes already present")

p.write_text(text)
