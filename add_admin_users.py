from pathlib import Path

app = Path("app.py")
text = app.read_text()

admin_code = r'''

@app.get("/admin/users")
def admin_users(request: Request):
    user = current_user(request)
    if not user or not user["is_admin"]:
        return RedirectResponse("/login", status_code=302)

    conn = get_db()
    users = conn.execute(
        "select id, username, callsign, bpq_user, approved, is_admin from users order by username"
    ).fetchall()
    return templates.TemplateResponse("admin_users.html", {"request": request, "user": user, "users": users})


@app.get("/admin/users/new")
def admin_new_user_form(request: Request):
    user = current_user(request)
    if not user or not user["is_admin"]:
        return RedirectResponse("/login", status_code=302)

    return templates.TemplateResponse("admin_user_form.html", {"request": request, "user": user, "edit_user": None})


@app.post("/admin/users/new")
def admin_create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    callsign: str = Form(...),
    bpq_user: str = Form(...),
    bpq_password: str = Form(...),
    approved: str = Form(None),
    is_admin: str = Form(None),
):
    user = current_user(request)
    if not user or not user["is_admin"]:
        return RedirectResponse("/login", status_code=302)

    conn = get_db()
    conn.execute(
        """insert into users
           (username, password_hash, callsign, bpq_user, bpq_password, approved, is_admin)
           values (?, ?, ?, ?, ?, ?, ?)""",
        (
            username.strip(),
            pwd_context.hash(password),
            callsign.strip().upper(),
            bpq_user.strip().upper(),
            bpq_password,
            1 if approved else 0,
            1 if is_admin else 0,
        ),
    )
    conn.commit()
    return RedirectResponse("/admin/users", status_code=302)


@app.get("/admin/users/edit/{user_id}")
def admin_edit_user_form(request: Request, user_id: int):
    user = current_user(request)
    if not user or not user["is_admin"]:
        return RedirectResponse("/login", status_code=302)

    conn = get_db()
    edit_user = conn.execute(
        "select id, username, callsign, bpq_user, approved, is_admin from users where id=?",
        (user_id,),
    ).fetchone()

    if not edit_user:
        return RedirectResponse("/admin/users", status_code=302)

    return templates.TemplateResponse("admin_user_form.html", {"request": request, "user": user, "edit_user": edit_user})


@app.post("/admin/users/edit/{user_id}")
def admin_update_user(
    request: Request,
    user_id: int,
    username: str = Form(...),
    password: str = Form(""),
    callsign: str = Form(...),
    bpq_user: str = Form(...),
    bpq_password: str = Form(""),
    approved: str = Form(None),
    is_admin: str = Form(None),
):
    user = current_user(request)
    if not user or not user["is_admin"]:
        return RedirectResponse("/login", status_code=302)

    conn = get_db()

    if password.strip():
        conn.execute(
            "update users set username=?, password_hash=?, callsign=?, bpq_user=?, approved=?, is_admin=? where id=?",
            (
                username.strip(),
                pwd_context.hash(password),
                callsign.strip().upper(),
                bpq_user.strip().upper(),
                1 if approved else 0,
                1 if is_admin else 0,
                user_id,
            ),
        )
    else:
        conn.execute(
            "update users set username=?, callsign=?, bpq_user=?, approved=?, is_admin=? where id=?",
            (
                username.strip(),
                callsign.strip().upper(),
                bpq_user.strip().upper(),
                1 if approved else 0,
                1 if is_admin else 0,
                user_id,
            ),
        )

    if bpq_password.strip():
        conn.execute("update users set bpq_password=? where id=?", (bpq_password, user_id))

    conn.commit()
    return RedirectResponse("/admin/users", status_code=302)


@app.post("/admin/users/delete/{user_id}")
def admin_delete_user(request: Request, user_id: int):
    user = current_user(request)
    if not user or not user["is_admin"]:
        return RedirectResponse("/login", status_code=302)

    if user["id"] == user_id:
        return RedirectResponse("/admin/users", status_code=302)

    conn = get_db()
    conn.execute("delete from users where id=?", (user_id,))
    conn.commit()
    return RedirectResponse("/admin/users", status_code=302)
'''

if 'def admin_users(' not in text:
    text += admin_code
    app.write_text(text)
    print("Admin routes added.")
else:
    print("Admin routes already exist.")
