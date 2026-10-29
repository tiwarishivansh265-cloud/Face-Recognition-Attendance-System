
import os
import sqlite3
from pathlib import Path
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, abort
)
from werkzeug.security import generate_password_hash, check_password_hash


BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "database" / "attendance.db"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("COOKIE_SECURE", "false").lower() == "true"
)

if not app.config["SECRET_KEY"]:
    # Development only. Set SECRET_KEY in the environment for deployment.
    app.config["SECRET_KEY"] = "development-only-change-this-key"


def get_db():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DATABASE))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL
            )
        """)

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
                longitude REAL,
                FOREIGN KEY (student_id)
                    REFERENCES students(student_id),
                UNIQUE (student_id, attendance_date)
            )
        """)

        # Create the initial admin only when credentials are configured.
        admin_username = os.environ.get("ADMIN_USERNAME", "admin")
        admin_password = os.environ.get("ADMIN_PASSWORD")

        if admin_password and len(admin_password) >= 8:
            existing = conn.execute(
                "SELECT username FROM admins WHERE username = ?",
                (admin_username,)
            ).fetchone()

            if existing is None:
                conn.execute(
                    "INSERT INTO admins (username, password_hash) "
                    "VALUES (?, ?)",
                    (
                        admin_username,
                        generate_password_hash(admin_password)
                    )
                )


def login_required(role=None):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("role"):
                return redirect(url_for("login"))

            if role and session.get("role") != role:
                abort(403)

            return view(*args, **kwargs)
        return wrapped
    return decorator


def current_student():
    student_id = session.get("student_id")

    if not student_id:
        return None

    with get_db() as conn:
        return conn.execute(
            "SELECT student_id, name FROM students WHERE student_id = ?",
            (student_id,)
        ).fetchone()


@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "")

        with get_db() as conn:
            if role == "admin":
                account = conn.execute(
                    "SELECT username, password_hash FROM admins "
                    "WHERE username = ?",
                    (username,)
                ).fetchone()

                if account and check_password_hash(
                    account["password_hash"], password
                ):
                    session.clear()
                    session["role"] = "admin"
                    session["username"] = account["username"]
                    return redirect(url_for("admin_dashboard"))

            elif role == "student":
                account = conn.execute(
                    "SELECT student_id, name, password_hash "
                    "FROM students WHERE student_id = ?",
                    (username,)
                ).fetchone()

                if account and check_password_hash(
                    account["password_hash"], password
                ):
                    session.clear()
                    session["role"] = "student"
                    session["student_id"] = account["student_id"]
                    session["username"] = account["name"]
                    return redirect(url_for("student_dashboard"))

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
    student = current_student()

    if student is None:
        session.clear()
        return redirect(url_for("login"))

    return render_template(
        "student_dashboard.html",
        student=student
    )


@app.route("/student-registration", methods=["GET", "POST"])
@login_required("admin")
def student_registration():
    if request.method == "POST":
        student_id = request.form.get("student_id", "").strip()
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")

        if not student_id or not name or len(password) < 8:
            flash("Enter valid details and a password of at least 8 characters.")
            return render_template("student_registration.html")

        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO students "
                    "(student_id, name, password_hash) VALUES (?, ?, ?)",
                    (
                        student_id,
                        name,
                        generate_password_hash(password)
                    )
                )
            flash("Student registered successfully.")

        except sqlite3.IntegrityError:
            flash("Student ID already exists.")

    return render_template("student_registration.html")


@app.route("/face-collection")
@login_required("admin")
def face_collection():
    return render_template("face_collection.html")


@app.route("/student-records")
@login_required("admin")
def student_records():
    with get_db() as conn:
        students = conn.execute(
            "SELECT student_id, name FROM students ORDER BY name"
        ).fetchall()

    return render_template("student_records.html", students=students)


@app.route("/attendance-records")
@login_required("admin")
def attendance_records():
    with get_db() as conn:
        records = conn.execute("""
            SELECT student_id, attendance_date, attendance_time,
                   latitude, longitude
            FROM attendance
            ORDER BY attendance_date DESC, attendance_time DESC
        """).fetchall()

    return render_template("attendance.html", records=records)


@app.route("/reports")
@login_required("admin")
def reports():
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    with get_db() as conn:
        student_count = conn.execute(
            "SELECT COUNT(*) FROM students"
        ).fetchone()[0]

        if start_date and end_date:
            try:
                start = datetime.strptime(start_date, "%Y-%m-%d").date()
                end = datetime.strptime(end_date, "%Y-%m-%d").date()

                if start > end:
                    flash("Start date must not be after end date.")
                    attendance_count = 0
                else:
                    attendance_count = conn.execute(
                        "SELECT COUNT(*) FROM attendance "
                        "WHERE attendance_date BETWEEN ? AND ?",
                        (start_date, end_date)
                    ).fetchone()[0]
            except ValueError:
                flash("Invalid date range.")
                attendance_count = 0
        else:
            attendance_count = conn.execute(
                "SELECT COUNT(*) FROM attendance"
            ).fetchone()[0]

    return render_template(
        "reports.html",
        student_count=student_count,
        attendance_count=attendance_count,
        start_date=start_date,
        end_date=end_date
    )


@app.route("/change-password", methods=["GET", "POST"])
@login_required()
def change_password():
    role = session.get("role")

    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        if len(new) < 8:
            flash("New password must contain at least 8 characters.")
        elif new != confirm:
            flash("New passwords do not match.")
        else:
            with get_db() as conn:
                if role == "admin":
                    account = conn.execute(
                        "SELECT password_hash FROM admins WHERE username = ?",
                        (session.get("username"),)
                    ).fetchone()
                    table, key = "admins", session.get("username")
                    column = "username"
                else:
                    account = conn.execute(
                        "SELECT password_hash FROM students "
                        "WHERE student_id = ?",
                        (session.get("student_id"),)
                    ).fetchone()
                    table, key = "students", session.get("student_id")
                    column = "student_id"

                if not account or not check_password_hash(
                    account["password_hash"], current
                ):
                    flash("Current password is incorrect.")
                else:
                    conn.execute(
                        f"UPDATE {table} SET password_hash = ? "
                        f"WHERE {column} = ?",
                        (generate_password_hash(new), key)
                    )
                    flash("Password updated successfully.")

    return render_template("change_password.html")


@app.route("/my-attendance")
@login_required("student")
def my_attendance():
    student = current_student()

    if student is None:
        session.clear()
        return redirect(url_for("login"))

    with get_db() as conn:
        records = conn.execute("""
            SELECT attendance_date, attendance_time, latitude, longitude
            FROM attendance
            WHERE student_id = ?
            ORDER BY attendance_date DESC, attendance_time DESC
        """, (student["student_id"],)).fetchall()

    return render_template("attendance.html", records=records)


@app.route("/my-profile")
@login_required("student")
def my_profile():
    student = current_student()

    if student is None:
        session.clear()
        return redirect(url_for("login"))

    return render_template(
        "student_dashboard.html",
        student=student
    )


@app.route("/mark-attendance", methods=["GET", "POST"])
@login_required("student")
def mark_attendance():
    if request.method == "GET":
        flash("Face verification is not yet implemented.")
        return redirect(url_for("my_attendance"))

    # This endpoint does not perform face recognition.
    # Do not use it for real attendance until verified face matching
    # and an appropriate anti-spoofing process are implemented.
    flash("Attendance marking is not enabled until face verification is implemented.")
    return redirect(url_for("my_attendance"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/health")
def health():
    return {"status": "ok"}


# Student records template is created in a later step.
# Initialize the local database when the application starts.
init_db()


if __name__ == "__main__":
    app.run(debug=False)
