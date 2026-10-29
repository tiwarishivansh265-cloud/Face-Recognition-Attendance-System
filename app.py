import cv2
import numpy as np
import face_recognition
import os
import re
import base64
import binascii
import sqlite3
import uuid

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
FACE_DIR = BASE_DIR / "face_data"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY", "development-only-change-this-key"
)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("COOKIE_SECURE", "false").lower() == "true"
)


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

        # Safely add new columns to an existing database.
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(students)")
        }

        migrations = {
            "user_id": "TEXT",
            "student_class": "TEXT",
            "division": "TEXT",
            "mobile": "TEXT",
            "email": "TEXT",
            "face_photo_path": "TEXT",
        }

        for column, sql_type in migrations.items():
            if column not in columns:
                conn.execute(
                    f"ALTER TABLE students ADD COLUMN {column} {sql_type}"
                )

        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_students_user_id
            ON students(user_id)
            WHERE user_id IS NOT NULL
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

        admin_username = os.environ.get("ADMIN_USERNAME", "admin")
        admin_password = os.environ.get("ADMIN_PASSWORD")

        if admin_password and len(admin_password) >= 8:
            conn.execute("""
                INSERT OR IGNORE INTO admins (username, password_hash)
                VALUES (?, ?)
            """, (
                admin_username,
                generate_password_hash(admin_password)
            ))


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
        return conn.execute("""
            SELECT student_id, user_id, name, student_class,
                   division, mobile, email
            FROM students
            WHERE student_id = ?
        """, (student_id,)).fetchone()


def save_face_photo(data_url, student_id):
    """Validate a captured JPEG/PNG data URL and save it locally."""
    if not data_url or len(data_url) > 4_000_000:
        raise ValueError("Please capture a valid face photo.")

    match = re.fullmatch(
        r"data:image/(jpeg|png);base64,([A-Za-z0-9+/=]+)",
        data_url
    )

    if not match:
        raise ValueError("Invalid photo format. Capture the photo again.")

    image_type, encoded = match.groups()

    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("The captured photo could not be decoded.")

    if not image_bytes or len(image_bytes) > 2_500_000:
        raise ValueError("The photo is empty or too large.")

    if image_type == "jpeg":
        valid_signature = image_bytes.startswith(b"\xff\xd8\xff")
        extension = ".jpg"
    else:
        valid_signature = image_bytes.startswith(
            b"\x89PNG\r\n\x1a\n"
        )
        extension = ".png"

    if not valid_signature:
        raise ValueError("The photo file is invalid.")

    FACE_DIR.mkdir(parents=True, exist_ok=True)

    # Never use a user-supplied filename.
    filename = f"{uuid.uuid4().hex}{extension}"
    destination = FACE_DIR / filename
    destination.write_bytes(image_bytes)

    # Store a relative path, not an absolute computer path.
    return str(Path("face_data") / filename)


@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "")

        with get_db() as conn:
            if role == "admin":
                account = conn.execute("""
                    SELECT username, password_hash
                    FROM admins WHERE username = ?
                """, (username,)).fetchone()

                if account and check_password_hash(
                    account["password_hash"], password
                ):
                    session.clear()
                    session["role"] = "admin"
                    session["username"] = account["username"]
                    return redirect(url_for("admin_dashboard"))

            elif role == "student":
                # New accounts use user_id. student_id remains supported
                # for older accounts.
                account = conn.execute("""
                    SELECT student_id, user_id, name, password_hash
                    FROM students
                    WHERE user_id = ? OR student_id = ?
                """, (username, username)).fetchone()

                if account and check_password_hash(
                    account["password_hash"], password
                ):
                    session.clear()
                    session["role"] = "student"
                    session["student_id"] = account["student_id"]
                    session["username"] = (
                        account["user_id"] or account["student_id"]
                    )
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

    return render_template("student_dashboard.html", student=student)


@app.route("/student-registration", methods=["GET", "POST"])
@login_required("admin")
def student_registration():
    if request.method == "POST":
        student_id = request.form.get("student_id", "").strip()
        name = request.form.get("name", "").strip()
        student_class = request.form.get("student_class", "").strip()
        division = request.form.get("division", "").strip()
        mobile = request.form.get("mobile", "").strip()
        email = request.form.get("email", "").strip().lower()
        user_id = request.form.get("user_id", "").strip()
        password = request.form.get("password", "")
        face_photo = request.form.get("face_photo", "")

        if not all([
            student_id, name, student_class, division,
            mobile, email, user_id
        ]):
            flash("Please complete all student details.")
            return render_template("student_registration.html")

        if not re.fullmatch(r"[0-9]{10}", mobile):
            flash("Enter a valid 10-digit mobile number.")
            return render_template("student_registration.html")

        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Enter a valid email address.")
            return render_template("student_registration.html")

        if len(password) < 8:
            flash("Password must contain at least 8 characters.")
            return render_template("student_registration.html")

        if len(student_id) > 30 or len(user_id) > 50:
            flash("Student ID or User ID is too long.")
            return render_template("student_registration.html")

        if not face_photo:
            flash("Please capture the student's face photo first.")
            return render_template("student_registration.html")

        photo_path = None

        try:
            # Validate and save the photo before inserting the student.
            photo_path = save_face_photo(face_photo, student_id)

            with get_db() as conn:
                conn.execute("""
                    INSERT INTO students (
                        student_id, name, password_hash,
                        user_id, student_class, division,
                        mobile, email, face_photo_path
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    student_id,
                    name,
                    generate_password_hash(password),
                    user_id,
                    student_class,
                    division,
                    mobile,
                    email,
                    photo_path
                ))

            flash("Student details and face photo saved successfully.")

        except sqlite3.IntegrityError:
            if photo_path:
                (BASE_DIR / photo_path).unlink(missing_ok=True)

            flash("Student ID or Login User ID already exists.")

        except ValueError as error:
            flash(str(error))

        except OSError:
            if photo_path:
                (BASE_DIR / photo_path).unlink(missing_ok=True)

            flash("Could not save the face photo. Please try again.")

    return render_template("student_registration.html")


@app.route("/face-collection")
@login_required("admin")
def face_collection():
    return render_template("face_collection.html")


@app.route("/student-records")
@login_required("admin")
def student_records():
    with get_db() as conn:
        students = conn.execute("""
            SELECT student_id, user_id, name, student_class,
                   division, mobile, email
            FROM students
            ORDER BY name
        """).fetchall()

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
                    attendance_count = conn.execute("""
                        SELECT COUNT(*) FROM attendance
                        WHERE attendance_date BETWEEN ? AND ?
                    """, (start_date, end_date)).fetchone()[0]
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
                    account = conn.execute("""
                        SELECT password_hash FROM admins
                        WHERE username = ?
                    """, (session.get("username"),)).fetchone()
                    table, key = "admins", session.get("username")
                    column = "username"
                else:
                    account = conn.execute("""
                        SELECT password_hash FROM students
                        WHERE student_id = ?
                    """, (session.get("student_id"),)).fetchone()
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

    return render_template("student_dashboard.html", student=student)



@app.route("/mark-attendance", methods=["GET", "POST"])
@login_required("student")
def mark_attendance():
    if request.method == "GET":
        return render_template("attendance.html", records=[])

    student = current_student()

    if student is None:
        session.clear()
        return redirect(url_for("login"))

    data_url = request.form.get("face_photo", "")
    latitude_text = request.form.get("latitude", "").strip()
    longitude_text = request.form.get("longitude", "").strip()

    if not data_url.startswith("data:image/"):
        flash("Please capture your face photo first.")
        return redirect(url_for("my_attendance"))

    try:
        # Decode the captured image in memory.
        header, encoded = data_url.split(",", 1)

        if len(encoded) > 4_000_000:
            raise ValueError("Captured photo is too large.")

        image_bytes = base64.b64decode(encoded, validate=True)
        if len(image_bytes) > 2_500_000:
            raise ValueError("Captured photo is too large.")

        image_array = np.frombuffer(image_bytes, dtype=np.uint8)
        captured_bgr = cv2.imdecode(image_array, cv2.IMREAD_COLOR)

        if captured_bgr is None:
            raise ValueError("Invalid captured image.")

        captured_rgb = cv2.cvtColor(captured_bgr, cv2.COLOR_BGR2RGB)
        captured_faces = face_recognition.face_encodings(captured_rgb)

        if len(captured_faces) != 1:
            flash("Please capture a clear photo showing exactly one face.")
            return redirect(url_for("my_attendance"))

        with get_db() as conn:
            account = conn.execute("""
                SELECT face_photo_path
                FROM students
                WHERE student_id = ?
            """, (student["student_id"],)).fetchone()

            if not account or not account["face_photo_path"]:
                flash("No enrolled face photo found. Contact the administrator.")
                return redirect(url_for("my_attendance"))

            reference_path = BASE_DIR / account["face_photo_path"]

            if not reference_path.is_file():
                flash("Enrolled face photo is unavailable. Contact the administrator.")
                return redirect(url_for("my_attendance"))

            reference_image = face_recognition.load_image_file(
                str(reference_path)
            )
            reference_faces = face_recognition.face_encodings(reference_image)

            if len(reference_faces) != 1:
                flash("The enrolled face photo is invalid. Contact the administrator.")
                return redirect(url_for("my_attendance"))

            distances = face_recognition.face_distance(
                [reference_faces[0]], captured_faces[0]
            )

            # Conservative starting threshold; test and tune before real use.
            if len(distances) != 1 or float(distances[0]) > 0.45:
                flash("Face did not match. Attendance was not marked.")
                return redirect(url_for("my_attendance"))

            latitude = None
            longitude = None

            if latitude_text or longitude_text:
                try:
                    latitude = float(latitude_text)
                    longitude = float(longitude_text)

                    if not (-90 <= latitude <= 90 and
                            -180 <= longitude <= 180):
                        raise ValueError
                except ValueError:
                    flash("Invalid GPS coordinates.")
                    return redirect(url_for("my_attendance"))

            now = datetime.now()
            try:
                conn.execute("""
                    INSERT INTO attendance (
                        student_id, attendance_date, attendance_time,
                        latitude, longitude
                    )
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    student["student_id"],
                    now.strftime("%Y-%m-%d"),
                    now.strftime("%H:%M:%S"),
                    latitude,
                    longitude
                ))
            except sqlite3.IntegrityError:
                flash("Attendance has already been marked today.")
                return redirect(url_for("my_attendance"))

        flash("Face matched. Attendance marked successfully.")

    except (ValueError, binascii.Error):
        flash("Could not read the captured photo. Please try again.")
    except Exception:
        app.logger.exception("Face verification failed")
        flash("Face verification could not be completed. Try again.")

    return redirect(url_for("my_attendance"))
    


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/health")
def health():
    return {"status": "ok"}


init_db()


if __name__ == "__main__":
    app.run(debug=False)
