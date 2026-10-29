import os
import re
import base64
import binascii
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime
from functools import wraps

import cv2
import numpy as np
import face_recognition

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, abort
)
from werkzeug.security import generate_password_hash, check_password_hash


BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "database" / "attendance.db"
FACE_DIR = BASE_DIR / "face_data"

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get(
        "SECRET_KEY", "development-only-change-this-key"
    ),
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(
        os.environ.get("COOKIE_SECURE", "false").lower() == "true"
    ),
)


# DATABASE

def get_db():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DATABASE), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def add_missing_columns(conn, table, fields):
    existing = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})")
    }

    for column, definition in fields.items():
        if column not in existing:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )


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

        add_missing_columns(conn, "students", {
            "user_id": "TEXT",
            "student_class": "TEXT",
            "division": "TEXT",
            "mobile": "TEXT",
            "email": "TEXT",
            "face_photo_path": "TEXT",
        })

        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_students_user_id
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
                FOREIGN KEY(student_id)
                    REFERENCES students(student_id),
                UNIQUE(student_id, attendance_date)
            )
        """)

        add_missing_columns(conn, "attendance", {
            "latitude": "REAL",
            "longitude": "REAL",
        })

        admin_username = os.environ.get("ADMIN_USERNAME", "admin")
        admin_password = os.environ.get("ADMIN_PASSWORD")

        if admin_password and len(admin_password) >= 8:
            conn.execute("""
                INSERT OR IGNORE INTO admins
                    (username, password_hash)
                VALUES (?, ?)
            """, (
                admin_username,
                generate_password_hash(admin_password)
            ))


# LOGIN HELPERS

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
                   division, mobile, email, face_photo_path
            FROM students
            WHERE student_id = ?
        """, (student_id,)).fetchone()


# FACE PHOTO VALIDATION

def decode_face_photo(data_url):
    if not data_url or len(data_url) > 4_000_000:
        raise ValueError("Please capture a valid face photo.")

    match = re.fullmatch(
        r"data:image/(jpeg|png);base64,([A-Za-z0-9+/=]+)",
        data_url
    )

    if not match:
        raise ValueError(
            "Invalid photo format. Capture the photo again."
        )

    try:
        image_bytes = base64.b64decode(
            match.group(2), validate=True
        )
    except (binascii.Error, ValueError) as error:
        raise ValueError(
            "Captured photo could not be decoded."
        ) from error

    if not image_bytes or len(image_bytes) > 2_500_000:
        raise ValueError("Photo is empty or too large.")

    if match.group(1) == "jpeg":
        if not image_bytes.startswith(b"\xff\xd8\xff"):
            raise ValueError("Invalid JPEG image.")
        extension = ".jpg"
    else:
        if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Invalid PNG image.")
        extension = ".png"

    frame = cv2.imdecode(
        np.frombuffer(image_bytes, dtype=np.uint8),
        cv2.IMREAD_COLOR
    )

    if frame is None:
        raise ValueError("Could not read the captured image.")

    height, width = frame.shape[:2]

    if height < 80 or width < 80 or height * width > 12_000_000:
        raise ValueError("Invalid image dimensions.")

    return image_bytes, frame, extension


def get_single_face_encoding(frame):
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    encodings = face_recognition.face_encodings(rgb)

    if len(encodings) != 1:
        raise ValueError(
            "Photo must contain exactly one clear face."
        )

    return encodings[0]


def save_face_photo(data_url, student_id):
    image_bytes, frame, extension = decode_face_photo(data_url)

    # Make sure the photo actually contains one detectable face.
    get_single_face_encoding(frame)

    FACE_DIR.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4().hex}{extension}"
    destination = FACE_DIR / filename
    destination.write_bytes(image_bytes)

    return str(Path("face_data") / filename)


def enrolled_photo_path(relative_path):
    if not relative_path:
        raise ValueError(
            "Face is not enrolled. Contact the administrator."
        )

    path = (BASE_DIR / relative_path).resolve()

    if FACE_DIR.resolve() not in path.parents or not path.is_file():
        raise ValueError(
            "Enrolled face photo is missing or invalid."
        )

    return path


# LOGIN / LOGOUT

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
                    FROM admins
                    WHERE username = ?
                """, (username,)).fetchone()

                if account and check_password_hash(
                    account["password_hash"], password
                ):
                    session.clear()
                    session["role"] = "admin"
                    session["username"] = account["username"]

                    return redirect(
                        url_for("admin_dashboard")
                    )

            elif role == "student":
                account = conn.execute("""
                    SELECT student_id, user_id, name, password_hash
                    FROM students
                    WHERE user_id = ? OR student_id = ?
                    LIMIT 1
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

                    return redirect(
                        url_for("student_dashboard")
                    )

        flash("Invalid login details.")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("login"))


# ADMIN DASHBOARD

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


# STUDENT DASHBOARD

@app.route("/student-dashboard")
@login_required("student")
def student_dashboard():
    student = current_student()

    if student is None:
        session.clear()
        return redirect(url_for("login"))

    return render_template(
        "student_dashboard.html", student=student
    )


@app.route("/my-profile")
@login_required("student")
def my_profile():
    student = current_student()

    if student is None:
        session.clear()
        return redirect(url_for("login"))

    return render_template(
        "student_dashboard.html", student=student
    )

# STUDENT REGISTRATION

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
            mobile, email, user_id, password
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
            flash("Student ID or Login User ID is too long.")
            return render_template("student_registration.html")

        if not face_photo:
            flash("Please capture the student's face photo first.")
            return render_template("student_registration.html")

        photo_path = None

        try:
            photo_path = save_face_photo(face_photo, student_id)

            with get_db() as conn:
                conn.execute("""
                    INSERT INTO students (
                        student_id, name, password_hash, user_id,
                        student_class, division, mobile, email,
                        face_photo_path
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

            flash("Student registered and face enrolled successfully.")
            return redirect(url_for("student_records"))

        except sqlite3.IntegrityError:
            if photo_path:
                (BASE_DIR / photo_path).unlink(missing_ok=True)

            flash("Student ID or User ID already exists.")

        except (ValueError, OSError) as error:
            if photo_path:
                (BASE_DIR / photo_path).unlink(missing_ok=True)

            flash(str(error))

    return render_template("student_registration.html")


# FACE COLLECTION

@app.route("/face-collection", methods=["GET", "POST"])
@login_required("admin")
def face_collection():
    if request.method == "POST":
        student_id = request.form.get("student_id", "").strip()
        data_url = request.form.get("face_photo", "")

        with get_db() as conn:
            student = conn.execute("""
                SELECT student_id, face_photo_path
                FROM students
                WHERE student_id = ?
            """, (student_id,)).fetchone()

        if student is None:
            flash("Student ID not found. Register the student first.")
            return redirect(url_for("face_collection"))

        photo_path = None

        try:
            photo_path = save_face_photo(data_url, student_id)

            with get_db() as conn:
                conn.execute("""
                    UPDATE students
                    SET face_photo_path = ?
                    WHERE student_id = ?
                """, (photo_path, student_id))

            old_path = student["face_photo_path"]

            if old_path and old_path != photo_path:
                try:
                    old_file = enrolled_photo_path(old_path)
                    old_file.unlink(missing_ok=True)
                except ValueError:
                    pass

            flash(
                "Face enrolled successfully. Student can now mark attendance."
            )
            return redirect(url_for("face_collection"))

        except (ValueError, OSError, sqlite3.Error) as error:
            if photo_path:
                (BASE_DIR / photo_path).unlink(missing_ok=True)

            app.logger.warning("Face enrollment failed: %s", error)
            flash("Face enrollment failed: " + str(error))

    with get_db() as conn:
        students = conn.execute("""
            SELECT student_id, name
            FROM students
            ORDER BY name
        """).fetchall()

    return render_template("face_collection.html", students=students)


# STUDENT RECORDS

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


# ATTENDANCE RECORDS FOR ADMIN

@app.route("/attendance-records")
@login_required("admin")
def attendance_records():
    with get_db() as conn:
        records = conn.execute("""
            SELECT a.student_id, s.name, a.attendance_date,
                   a.attendance_time, a.latitude, a.longitude
            FROM attendance a
            JOIN students s ON s.student_id = a.student_id
            ORDER BY a.attendance_date DESC, a.attendance_time DESC
        """).fetchall()

    return render_template("attendance.html", records=records)


# REPORTS

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
                start = datetime.strptime(
                    start_date, "%Y-%m-%d"
                ).date()

                end = datetime.strptime(
                    end_date, "%Y-%m-%d"
                ).date()

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


# CHANGE PASSWORD

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
                    table = "admins"
                    key_column = "username"
                    key_value = session.get("username")
                else:
                    table = "students"
                    key_column = "student_id"
                    key_value = session.get("student_id")

                account = conn.execute(
                    f"SELECT password_hash FROM {table} "
                    f"WHERE {key_column} = ?",
                    (key_value,)
                ).fetchone()

                if not account or not check_password_hash(
                    account["password_hash"], current
                ):
                    flash("Current password is incorrect.")
                else:
                    conn.execute(
                        f"UPDATE {table} SET password_hash = ? "
                        f"WHERE {key_column} = ?",
                        (generate_password_hash(new), key_value)
                    )
                    flash("Password updated successfully.")

    return render_template("change_password.html")


# STUDENT ATTENDANCE HISTORY

@app.route("/my-attendance")
@login_required("student")
def my_attendance():
    student = current_student()

    if student is None:
        session.clear()
        return redirect(url_for("login"))

    with get_db() as conn:
        records = conn.execute("""
            SELECT student_id, attendance_date, attendance_time,
                   latitude, longitude
            FROM attendance
            WHERE student_id = ?
            ORDER BY attendance_date DESC, attendance_time DESC
        """, (student["student_id"],)).fetchall()

    return render_template("attendance.html", records=records)


# LIVE FACE ATTENDANCE

@app.route("/mark-attendance", methods=["GET", "POST"])
@login_required("student")
def mark_attendance():
    if request.method == "GET":
        return render_template("live_attendance.html")

    student = current_student()

    if student is None:
        session.clear()
        return redirect(url_for("login"))

    try:
        _, captured_bgr, _ = decode_face_photo(
            request.form.get("face_photo", "")
        )

        captured_encoding = get_single_face_encoding(captured_bgr)
        reference_path = enrolled_photo_path(
            student["face_photo_path"]
        )

        reference_image = face_recognition.load_image_file(
            str(reference_path)
        )
        reference_encodings = face_recognition.face_encodings(
            reference_image
        )

        if len(reference_encodings) != 1:
            raise ValueError(
                "Enrolled photo must contain exactly one clear face. "
                "Ask admin to enroll again."
            )

        distance = float(
            face_recognition.face_distance(
                [reference_encodings[0]], captured_encoding
            )[0]
        )

        if distance > 0.45:
            flash("Face did not match. Attendance was not marked.")
            return redirect(url_for("mark_attendance"))

        lat_text = request.form.get("latitude", "").strip()
        lon_text = request.form.get("longitude", "").strip()

        latitude = None
        longitude = None

        if lat_text or lon_text:
            if not lat_text or not lon_text:
                raise ValueError(
                    "GPS must provide both coordinates, or leave both blank."
                )

            try:
                latitude = float(lat_text)
                longitude = float(lon_text)
            except ValueError as error:
                raise ValueError("Invalid GPS coordinates.") from error

            if not (
                -90 <= latitude <= 90
                and -180 <= longitude <= 180
            ):
                raise ValueError("GPS coordinates are out of range.")

        now = datetime.now()

        with get_db() as conn:
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
                flash("Attendance already marked today.")
                return redirect(url_for("my_attendance"))

        flash("Face matched! Attendance saved successfully.")
        return redirect(url_for("my_attendance"))

    except ValueError as error:
        flash(str(error))
        return redirect(url_for("mark_attendance"))

    except Exception:
        app.logger.exception(
            "Attendance verification/storage failed"
        )
        flash(
            "Attendance could not be saved. Check the Flask terminal error."
        )
        return redirect(url_for("mark_attendance"))


# FORGOT PASSWORD

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        flash(
            "For account security, contact the administrator "
            "to reset your password."
        )
        return redirect(url_for("login"))

    return render_template("forgot_password.html")


# HEALTH CHECK

@app.route("/health")
def health():
    return {"status": "ok"}


# INITIALIZE DATABASE

init_db()


if __name__ == "__main__":
    app.run(
        debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    )
