

from flask import Flask, render_template, request, redirect, url_for, session, flash
import os
import sqlite3
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get(
    "SECRET_KEY",
    "dev-only-change-this-secret"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "database", "attendance.db")


def get_db():
    os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                attendance_date TEXT NOT NULL,
                attendance_time TEXT NOT NULL,
                latitude REAL,
                longitude REAL
            )
        """)


def login_required(role=None):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "role" not in session:
                return redirect(url_for("login"))

            if role and session.get("role") != role:
                flash("You are not authorized to access this page.")
                return redirect(url_for("login"))

            return view(*args, **kwargs)
        return wrapped
    return decorator


@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "")

        # Temporary admin login for initial UI testing only.
        # Replace with database-backed, hashed-password authentication.
        if (
            role == "admin"
            and username == os.environ.get("ADMIN_USERNAME", "admin")
            and password == os.environ.get("ADMIN_PASSWORD")
            and os.environ.get("ADMIN_PASSWORD")
        ):
            session.clear()
            session["role"] = "admin"
            session["username"] = username
            return redirect(url_for("admin_dashboard"))

        flash("Invalid login details.")
    
    return render_template("login.html")


@app.route("/admin-dashboard")
@login_required("admin")
def admin_dashboard():
    with get_db() as conn:
        student_count = conn.execute(
            "SELECT COUNT(*) FROM students"
        ).fetchone()[0]
        attendance_count = conn.execute(
            "SELECT COUNT(*) FROM attendance"
        ).fetchone()[0]

    return render_template(
        "admin_dashboard.html",
        student_count=student_count,
        attendance_count=attendance_count
    )


@app.route("/student-dashboard")
@login_required("student")
def student_dashboard():
    return render_template("student_dashboard.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
