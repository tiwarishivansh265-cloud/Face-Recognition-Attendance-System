import os
import re
import base64
import binascii
import sqlite3
import uuid
import math
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


# ==================================================
# CONFIGURATION
# ==================================================

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


# ==================================================
# DATABASE
# ==================================================

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

        # Faculty accounts
        conn.execute("""
            CREATE TABLE IF NOT EXISTS faculty (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                faculty_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                email TEXT,
                department TEXT,
                password_hash TEXT NOT NULL
            )
        """)

        # Optional faculty subject/class assignments
        conn.execute("""
            CREATE TABLE IF NOT EXISTS faculty_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                faculty_id TEXT NOT NULL,
                subject TEXT NOT NULL,
                student_class TEXT NOT NULL,
                division TEXT NOT NULL,
                UNIQUE(faculty_id, subject, student_class, division),
                FOREIGN KEY (faculty_id) REFERENCES faculty(faculty_id)
            )
        """)

        # Lecture schedules and classroom GPS location
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lecture_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                faculty_id TEXT NOT NULL,
                subject TEXT NOT NULL,
                topic TEXT NOT NULL,
                student_class TEXT NOT NULL,
                division TEXT NOT NULL,
                lecture_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                room_number TEXT,
                location_name TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                allowed_radius_m INTEGER NOT NULL DEFAULT 100,
                status TEXT NOT NULL DEFAULT 'Scheduled',
                created_at TEXT NOT NULL,
                FOREIGN KEY (faculty_id) REFERENCES faculty(faculty_id)
            )
        """)

        # One attendance record per student per lecture
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lecture_attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lecture_id INTEGER NOT NULL,
                student_id TEXT NOT NULL,
                attendance_date TEXT NOT NULL,
                attendance_time TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                distance_m REAL NOT NULL,
                face_match INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (lecture_id) REFERENCES lecture_sessions(id),
                FOREIGN KEY (student_id) REFERENCES students(student_id),
                UNIQUE (lecture_id, student_id)
            )
        """)


# ==================================================
# LOGIN HELPERS
# ==================================================

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


def current_faculty():
    faculty_id = session.get("faculty_id")
    if not faculty_id:
        return None

    with get_db() as conn:
        return conn.execute("""
            SELECT faculty_id, name, email, department
            FROM faculty
            WHERE faculty_id = ?
        """, (faculty_id,)).fetchone()


# ==================================================
# FACE IMAGE VALIDATION
# ==================================================

def decode_face_photo(data_url):
    if not data_url or len(data_url) > 4_000_000:
        raise ValueError("Please capture a valid face photo.")

    match = re.fullmatch(
        r"data:image/(jpeg|png);base64,([A-Za-z0-9+/=]+)",
        data_url
    )

    if not match:
        raise ValueError("Invalid photo format. Capture again.")

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
            "Photo must contain exactly one clear, detectable face."
        )

    return encodings[0]


def save_face_photo(data_url, student_id):
    image_bytes, frame, extension = decode_face_photo(data_url)
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
    face_dir = FACE_DIR.resolve()

    if face_dir not in path.parents or not path.is_file():
        raise ValueError("Enrolled face photo is missing or invalid.")

    return path


# ==================================================
# GPS HELPERS
# ==================================================

def validate_coordinates(latitude, longitude):
    if not (
        -90 <= latitude <= 90
        and -180 <= longitude <= 180
    ):
        raise ValueError("GPS coordinates are out of range.")


def distance_in_metres(lat1, lon1, lat2, lon2):
    """Calculate approximate ground distance using the Haversine formula."""
    earth_radius = 6_371_000

    lat1, lon1, lat2, lon2 = map(
        math.radians, [lat1, lon1, lat2, lon2]
    )

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2)
        * math.sin(dlon / 2) ** 2
    )

    return earth_radius * 2 * math.atan2(
        math.sqrt(a), math.sqrt(1 - a)
    )


# ==================================================
# LOGIN AND LOGOUT
# ==================================================

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
                    return redirect(url_for("student_dashboard"))

            elif role == "faculty":
                account = conn.execute("""
                    SELECT faculty_id, name, password_hash
                    FROM faculty WHERE faculty_id = ?
                """, (username,)).fetchone()

                if account and check_password_hash(
                    account["password_hash"], password
                ):
                    session.clear()
                    session["role"] = "faculty"
                    session["faculty_id"] = account["faculty_id"]
                    session["username"] = account["faculty_id"]
                    return redirect(url_for("faculty_dashboard"))

        flash("Invalid login details.")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("login"))

# ==================================================
# ADMIN DASHBOARD
# ==================================================

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

        faculty_count = conn.execute(
            "SELECT COUNT(*) FROM faculty"
        ).fetchone()[0]

        lecture_count = conn.execute(
            "SELECT COUNT(*) FROM lecture_sessions"
        ).fetchone()[0]

    return render_template(
        "admin_dashboard.html",
        student_count=student_count,
        attendance_count=attendance_count,
        faculty_count=faculty_count,
        lecture_count=lecture_count
    )


# ==================================================
# STUDENT DASHBOARD
# ==================================================

@app.route("/student-dashboard")
@login_required("student")
def student_dashboard():
    student = current_student()

    if student is None:
        session.clear()
        return redirect(url_for("login"))

    with get_db() as conn:
        lectures = conn.execute("""
            SELECT ls.*
            FROM lecture_sessions ls
            WHERE ls.student_class = ?
              AND ls.division = ?
              AND ls.status = 'Active'
              AND ls.lecture_date = ?
            ORDER BY ls.start_time
        """, (
            student["student_class"],
            student["division"],
            datetime.now().strftime("%Y-%m-%d")
        )).fetchall()

    return render_template(
        "student_dashboard.html",
        student=student,
        lectures=lectures
    )


@app.route("/my-profile")
@login_required("student")
def my_profile():
    student = current_student()

    if student is None:
        session.clear()
        return redirect(url_for("login"))

    return render_template("student_dashboard.html", student=student)


# ==================================================
# FACULTY ACCOUNT MANAGEMENT - ADMIN ONLY
# ==================================================

@app.route("/faculty-management", methods=["GET", "POST"])
@login_required("admin")
def faculty_management():
    if request.method == "POST":
        faculty_id = request.form.get("faculty_id", "").strip()
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        department = request.form.get("department", "").strip()
        password = request.form.get("password", "")

        if not all([faculty_id, name, password]):
            flash("Faculty ID, name and password are required.")
        elif len(password) < 8:
            flash("Faculty password must be at least 8 characters.")
        else:
            try:
                with get_db() as conn:
                    conn.execute("""
                        INSERT INTO faculty (
                            faculty_id, name, email, department,
                            password_hash
                        )
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        faculty_id, name, email, department,
                        generate_password_hash(password)
                    ))

                flash("Faculty account created successfully.")

            except sqlite3.IntegrityError:
                flash("This Faculty ID already exists.")

    with get_db() as conn:
        faculty_members = conn.execute("""
            SELECT faculty_id, name, email, department
            FROM faculty
            ORDER BY name
        """).fetchall()

    return render_template(
        "faculty_management.html",
        faculty_members=faculty_members
    )


@app.route("/faculty-delete/<faculty_id>", methods=["POST"])
@login_required("admin")
def faculty_delete(faculty_id):
    with get_db() as conn:
        assigned_lectures = conn.execute("""
            SELECT COUNT(*) FROM lecture_sessions
            WHERE faculty_id = ?
        """, (faculty_id,)).fetchone()[0]

        if assigned_lectures:
            flash(
                "Faculty has lecture records. Deactivate or retain "
                "the account to preserve records."
            )
        else:
            conn.execute(
                "DELETE FROM faculty_assignments WHERE faculty_id = ?",
                (faculty_id,)
            )
            conn.execute(
                "DELETE FROM faculty WHERE faculty_id = ?",
                (faculty_id,)
            )
            flash("Faculty account deleted.")

    return redirect(url_for("faculty_management"))


# ==================================================
# FACULTY DASHBOARD
# ==================================================

@app.route("/faculty-dashboard")
@login_required("faculty")
def faculty_dashboard():
    faculty_id = session.get("faculty_id")

    with get_db() as conn:
        lectures = conn.execute("""
            SELECT ls.*,
                   (SELECT COUNT(*)
                    FROM lecture_attendance la
                    WHERE la.lecture_id = ls.id) AS present_count
            FROM lecture_sessions ls
            WHERE ls.faculty_id = ?
            ORDER BY ls.lecture_date DESC, ls.start_time DESC
        """, (faculty_id,)).fetchall()

        student_count = conn.execute("""
            SELECT COUNT(DISTINCT s.student_id)
            FROM students s
            JOIN faculty_assignments fa
              ON fa.student_class = s.student_class
             AND fa.division = s.division
            WHERE fa.faculty_id = ?
        """, (faculty_id,)).fetchone()[0]

    return render_template(
        "faculty_dashboard.html",
        faculty=current_faculty(),
        lectures=lectures,
        student_count=student_count
    )


# ==================================================
# FACULTY CLASS / SUBJECT ASSIGNMENTS
# ==================================================

@app.route("/faculty-assignments", methods=["GET", "POST"])
@login_required("faculty")
def faculty_assignments():
    faculty_id = session.get("faculty_id")

    if request.method == "POST":
        subject = request.form.get("subject", "").strip()
        student_class = request.form.get("student_class", "").strip()
        division = request.form.get("division", "").strip()

        if not all([subject, student_class, division]):
            flash("Enter subject, class and division.")
        else:
            try:
                with get_db() as conn:
                    conn.execute("""
                        INSERT INTO faculty_assignments (
                            faculty_id, subject, student_class, division
                        )
                        VALUES (?, ?, ?, ?)
                    """, (
                        faculty_id, subject, student_class, division
                    ))
                flash("Subject/class assignment added.")

            except sqlite3.IntegrityError:
                flash("This assignment already exists.")

    with get_db() as conn:
        assignments = conn.execute("""
            SELECT * FROM faculty_assignments
            WHERE faculty_id = ?
            ORDER BY student_class, division, subject
        """, (faculty_id,)).fetchall()

    return render_template(
        "faculty_assignments.html",
        assignments=assignments
    )


# ==================================================
# STUDENT REGISTRATION
# ==================================================

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
                    student_id, name, generate_password_hash(password),
                    user_id, student_class, division, mobile, email,
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


# ==================================================
# FACE COLLECTION
# ==================================================

@app.route("/face-collection", methods=["GET", "POST"])
@login_required("admin")
def face_collection():
    if request.method == "POST":
        student_id = request.form.get("student_id", "").strip()
        data_url = request.form.get("face_photo", "").strip()

        if not student_id:
            flash("Please select a registered student.")
        elif not data_url:
            flash("Please capture the student's face first.")
        else:
            photo_path = None

            try:
                with get_db() as conn:
                    student = conn.execute("""
                        SELECT student_id, face_photo_path
                        FROM students
                        WHERE student_id = ?
                    """, (student_id,)).fetchone()

                if student is None:
                    flash("Student not found. Register the student first.")
                else:
                    old_path = student["face_photo_path"]
                    photo_path = save_face_photo(data_url, student_id)

                    with get_db() as conn:
                        conn.execute("""
                            UPDATE students
                            SET face_photo_path = ?
                            WHERE student_id = ?
                        """, (photo_path, student_id))

                    if old_path and old_path != photo_path:
                        try:
                            enrolled_photo_path(old_path).unlink(
                                missing_ok=True
                            )
                        except (ValueError, OSError):
                            app.logger.warning(
                                "Could not remove previous face photo."
                            )

                    flash("Face enrollment successful.")
                    return redirect(url_for("face_collection"))

            except (ValueError, OSError, sqlite3.Error) as error:
                if photo_path:
                    (BASE_DIR / photo_path).unlink(missing_ok=True)
                flash("Face enrollment failed: " + str(error))

    with get_db() as conn:
        students = conn.execute("""
            SELECT student_id, name
            FROM students
            ORDER BY name
        """).fetchall()

    return render_template("face_collection.html", students=students)

# ==================================================
# FACULTY LECTURE SCHEDULING
# ==================================================

@app.route("/faculty/lectures/new", methods=["GET", "POST"])
@login_required("faculty")
def create_lecture():
    faculty_id = session.get("faculty_id")

    if request.method == "POST":
        subject = request.form.get("subject", "").strip()
        topic = request.form.get("topic", "").strip()
        student_class = request.form.get("student_class", "").strip()
        division = request.form.get("division", "").strip()
        lecture_date = request.form.get("lecture_date", "").strip()
        start_time = request.form.get("start_time", "").strip()
        end_time = request.form.get("end_time", "").strip()
        room_number = request.form.get("room_number", "").strip()
        location_name = request.form.get("location_name", "").strip()
        lat_text = request.form.get("latitude", "").strip()
        lon_text = request.form.get("longitude", "").strip()
        radius_text = request.form.get("allowed_radius_m", "100").strip()

        try:
            if not all([
                subject, topic, student_class, division,
                lecture_date, start_time, end_time,
                location_name, lat_text, lon_text
            ]):
                raise ValueError("Please fill in all required fields.")

            date_value = datetime.strptime(
                lecture_date, "%Y-%m-%d"
            ).date()

            start_value = datetime.strptime(
                start_time, "%H:%M"
            ).time()

            end_value = datetime.strptime(
                end_time, "%H:%M"
            ).time()

            if end_value <= start_value:
                raise ValueError(
                    "Lecture end time must be after start time."
                )

            latitude = float(lat_text)
            longitude = float(lon_text)
            radius = int(radius_text)

            validate_coordinates(latitude, longitude)

            if radius < 10 or radius > 1000:
                raise ValueError(
                    "GPS radius must be between 10 and 1000 metres."
                )

            with get_db() as conn:
                conn.execute("""
                    INSERT INTO lecture_sessions (
                        faculty_id, subject, topic,
                        student_class, division,
                        lecture_date, start_time, end_time,
                        room_number, location_name,
                        latitude, longitude, allowed_radius_m,
                        status, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            'Scheduled', ?)
                """, (
                    faculty_id, subject, topic,
                    student_class, division,
                    date_value.isoformat(),
                    start_time, end_time,
                    room_number, location_name,
                    latitude, longitude, radius,
                    datetime.now().isoformat(timespec="seconds")
                ))

            flash("Lecture scheduled successfully.")
            return redirect(url_for("faculty_dashboard"))

        except (ValueError, sqlite3.Error) as error:
            flash(str(error))

    return render_template("lecture_form.html")


# ==================================================
# ACTIVATE / DEACTIVATE LECTURE
# ==================================================

@app.route(
    "/faculty/lectures/<int:lecture_id>/status",
    methods=["POST"]
)
@login_required("faculty")
def update_lecture_status(lecture_id):
    faculty_id = session.get("faculty_id")
    new_status = request.form.get("status", "").strip()

    allowed_statuses = {
        "Scheduled", "Active", "Completed", "Cancelled"
    }

    if new_status not in allowed_statuses:
        abort(400)

    with get_db() as conn:
        lecture = conn.execute("""
            SELECT id FROM lecture_sessions
            WHERE id = ? AND faculty_id = ?
        """, (lecture_id, faculty_id)).fetchone()

        if lecture is None:
            abort(404)

        conn.execute("""
            UPDATE lecture_sessions
            SET status = ?
            WHERE id = ? AND faculty_id = ?
        """, (new_status, lecture_id, faculty_id))

    flash("Lecture status updated.")
    return redirect(url_for("faculty_dashboard"))


# ==================================================
# FACULTY LECTURE ATTENDANCE REPORT
# ==================================================

@app.route("/faculty/lectures/<int:lecture_id>/attendance")
@login_required("faculty")
def faculty_lecture_attendance(lecture_id):
    faculty_id = session.get("faculty_id")

    with get_db() as conn:
        lecture = conn.execute("""
            SELECT *
            FROM lecture_sessions
            WHERE id = ? AND faculty_id = ?
        """, (lecture_id, faculty_id)).fetchone()

        if lecture is None:
            abort(404)

        records = conn.execute("""
            SELECT la.student_id, s.name,
                   s.student_class, s.division,
                   la.attendance_date, la.attendance_time,
                   la.latitude, la.longitude, la.distance_m
            FROM lecture_attendance la
            JOIN students s ON s.student_id = la.student_id
            WHERE la.lecture_id = ?
            ORDER BY s.name
        """, (lecture_id,)).fetchall()

    return render_template(
        "faculty_attendance.html",
        lecture=lecture,
        records=records
    )

# ==================================================
# STUDENT RECORDS
# ==================================================

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


# ==================================================
# ADMIN ATTENDANCE RECORDS
# ==================================================

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

        lecture_records = conn.execute("""
            SELECT la.student_id, s.name,
                   ls.subject, ls.topic, ls.student_class,
                   ls.division, la.attendance_date,
                   la.attendance_time, la.distance_m
            FROM lecture_attendance la
            JOIN students s ON s.student_id = la.student_id
            JOIN lecture_sessions ls ON ls.id = la.lecture_id
            ORDER BY la.attendance_date DESC, la.attendance_time DESC
        """).fetchall()

    return render_template(
        "attendance.html",
        records=records,
        lecture_records=lecture_records
    )


# ==================================================
# ADMIN REPORTS
# ==================================================

@app.route("/reports")
@login_required("admin")
def reports():
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    with get_db() as conn:
        student_count = conn.execute(
            "SELECT COUNT(*) FROM students"
        ).fetchone()[0]

        faculty_count = conn.execute(
            "SELECT COUNT(*) FROM faculty"
        ).fetchone()[0]

        if start_date and end_date:
            try:
                start = datetime.strptime(start_date, "%Y-%m-%d").date()
                end = datetime.strptime(end_date, "%Y-%m-%d").date()

                if start > end:
                    raise ValueError("Start date must not be after end date.")

                attendance_count = conn.execute("""
                    SELECT COUNT(*) FROM attendance
                    WHERE attendance_date BETWEEN ? AND ?
                """, (start_date, end_date)).fetchone()[0]

                lecture_attendance_count = conn.execute("""
                    SELECT COUNT(*) FROM lecture_attendance
                    WHERE attendance_date BETWEEN ? AND ?
                """, (start_date, end_date)).fetchone()[0]

            except ValueError as error:
                flash(str(error))
                attendance_count = 0
                lecture_attendance_count = 0
        else:
            attendance_count = conn.execute(
                "SELECT COUNT(*) FROM attendance"
            ).fetchone()[0]

            lecture_attendance_count = conn.execute(
                "SELECT COUNT(*) FROM lecture_attendance"
            ).fetchone()[0]

    return render_template(
        "reports.html",
        student_count=student_count,
        faculty_count=faculty_count,
        attendance_count=attendance_count,
        lecture_attendance_count=lecture_attendance_count,
        start_date=start_date,
        end_date=end_date
    )


# ==================================================
# CHANGE PASSWORD
# ==================================================

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
                elif role == "student":
                    table = "students"
                    key_column = "student_id"
                    key_value = session.get("student_id")
                elif role == "faculty":
                    table = "faculty"
                    key_column = "faculty_id"
                    key_value = session.get("faculty_id")
                else:
                    abort(403)

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


# ==================================================
# STUDENT ATTENDANCE HISTORY
# ==================================================

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

        lecture_records = conn.execute("""
            SELECT la.attendance_date, la.attendance_time,
                   la.latitude, la.longitude, la.distance_m,
                   ls.subject, ls.topic, ls.student_class,
                   ls.division, ls.lecture_date
            FROM lecture_attendance la
            JOIN lecture_sessions ls ON ls.id = la.lecture_id
            WHERE la.student_id = ?
            ORDER BY la.attendance_date DESC, la.attendance_time DESC
        """, (student["student_id"],)).fetchall()

    return render_template(
        "attendance.html",
        records=records,
        lecture_records=lecture_records
    )


# ==================================================
# LIVE FACE ATTENDANCE - ACTIVE LECTURE + GPS
# ==================================================

@app.route("/mark-attendance", methods=["GET", "POST"])
@login_required("student")
def mark_attendance():
    student = current_student()

    if student is None:
        session.clear()
        return redirect(url_for("login"))

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")

    with get_db() as conn:
        lectures = conn.execute("""
            SELECT *
            FROM lecture_sessions
            WHERE student_class = ?
              AND division = ?
              AND lecture_date = ?
              AND status = 'Active'
              AND start_time <= ?
              AND end_time >= ?
            ORDER BY start_time
        """, (
            student["student_class"], student["division"],
            today, current_time, current_time
        )).fetchall()

    if request.method == "GET":
        return render_template(
            "live_attendance.html",
            lectures=lectures
        )

    try:
        lecture_id = request.form.get("lecture_id", "").strip()
        if not lecture_id.isdigit():
            raise ValueError("Please select an active lecture.")

        with get_db() as conn:
            lecture = conn.execute("""
                SELECT *
                FROM lecture_sessions
                WHERE id = ?
                  AND student_class = ?
                  AND division = ?
                  AND lecture_date = ?
                  AND status = 'Active'
                  AND start_time <= ?
                  AND end_time >= ?
            """, (
                int(lecture_id),
                student["student_class"],
                student["division"],
                today,
                current_time,
                current_time
            )).fetchone()

        if lecture is None:
            raise ValueError(
                "No active lecture is available for your class "
                "at this time."
            )

        # Require current GPS coordinates.
        lat_text = request.form.get("latitude", "").strip()
        lon_text = request.form.get("longitude", "").strip()

        if not lat_text or not lon_text:
            raise ValueError("Please allow GPS location and try again.")

        try:
            latitude = float(lat_text)
            longitude = float(lon_text)
        except ValueError as error:
            raise ValueError("Invalid GPS coordinates.") from error

        validate_coordinates(latitude, longitude)

        distance = distance_in_metres(
            latitude,
            longitude,
            float(lecture["latitude"]),
            float(lecture["longitude"])
        )

        if distance > int(lecture["allowed_radius_m"]):
            raise ValueError(
                f"You are outside the permitted classroom GPS radius "
                f"({distance:.0f} m away). Attendance was not marked."
            )

        # Verify captured face against the enrolled face.
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
                "Enrolled face photo is invalid. Contact the administrator."
            )

        face_distance = float(
            face_recognition.face_distance(
                [reference_encodings[0]], captured_encoding
            )[0]
        )

        if face_distance > 0.45:
            raise ValueError(
                "Face did not match. Attendance was not marked."
            )

        with get_db() as conn:
            conn.execute("""
                INSERT INTO lecture_attendance (
                    lecture_id, student_id,
                    attendance_date, attendance_time,
                    latitude, longitude, distance_m, face_match
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                int(lecture_id),
                student["student_id"],
                now.strftime("%Y-%m-%d"),
                now.strftime("%H:%M:%S"),
                latitude,
                longitude,
                round(distance, 2)
            ))

        flash("Face and GPS verified. Lecture attendance saved.")
        return redirect(url_for("my_attendance"))

    except sqlite3.IntegrityError:
        flash("Your attendance for this lecture has already been marked.")
        return redirect(url_for("my_attendance"))

    except ValueError as error:
        flash(str(error))
        return redirect(url_for("mark_attendance"))

    except Exception:
        app.logger.exception("Lecture attendance failed")
        flash("Attendance could not be saved. Please try again.")
        return redirect(url_for("mark_attendance"))


# ==================================================
# FORGOT PASSWORD
# ==================================================

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        flash(
            "For account security, contact the administrator "
            "to reset your password."
        )
        return redirect(url_for("login"))

    return render_template("forgot_password.html")


# ==================================================
# HEALTH CHECK AND START
# ==================================================

@app.route("/health")
def health():
    return {"status": "ok"}


init_db()

if __name__ == "__main__":
    app.run(
        debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    )
