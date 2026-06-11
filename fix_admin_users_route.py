from pathlib import Path

app = Path("app.py")
text = app.read_text()

route = '''\n@app.get("/admin/users")
def admin_users(request: Request):
    user = require_user(request)
    if not user["is_admin"]:
        return RedirectResponse("/", status_code=303)

    with db() as conn:
        users = conn.execute(
            "select id, username, callsign, bpq_user, approved, is_admin from users order by username"
        ).fetchall()

    return templates.TemplateResponse(
        "admin_users.html",
        {"request": request, "user": user, "users": users},
    )

'''

if '@app.get("/admin/users")' not in text:
    marker = '@app.get("/admin/users/edit/{user_id}")'
    text = text.replace(marker, route + marker)
    app.write_text(text)
    print("Inserted /admin/users route into app.py")
else:
    print("/admin/users route already exists")

tpl = Path("templates/admin_users.html")
t = tpl.read_text()
t = t.replace('/admin/users/add', '/admin/users/new')
tpl.write_text(t)
print("Fixed admin_users.html add route")
