# Face Recognition Attendance Management System (FRAMS)

A Flask-based web application for student registration, face enrollment, attendance records, faculty lecture management, and GPS-assisted attendance verification.

## Current features

- Role-based login for administrator, student, and faculty.
- Admin dashboard with student and attendance counts.
- Student registration with password hashing and face-photo enrollment.
- Face collection page with webcam capture and green alignment guide.
- Student attendance history and admin attendance reports.
- Faculty account management, class/subject assignments, lecture scheduling, lecture status management, and lecture attendance reports.
- Live attendance flow that captures a face photo and GPS coordinates, then verifies the enrolled face and classroom radius on the server.
- Password change page for authenticated roles.
- SQLite database initialized automatically by the application.
- Health endpoint at `/health`.

## Technology

- Python 3.12
- Flask
- SQLite
- OpenCV, NumPy, and `face-recognition`
- HTML, CSS, and JavaScript

## Project setup (Windows PowerShell)

Open a terminal in the repository folder.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The `face-recognition` package depends on `dlib`. If installation fails on Windows, use a compatible Python 3.12/dlib setup before running the app.

## Configure the first administrator

The application does **not** create a default admin with a hard-coded password. Configure a username and a strong password in the same PowerShell terminal before the first run:

```powershell
$env:ADMIN_USERNAME = "admin"
$env:ADMIN_PASSWORD = "Choose-A-Strong-Password-Here"
$env:SECRET_KEY = "Replace-With-A-Long-Random-Secret"
.\.venv\Scripts\python.exe app.py
```

Replace the example password and secret with your own values. Do not commit real credentials to GitHub. The admin account is inserted when the database is initialized and only if the username does not already exist. If the database already exists without an admin account, configure the environment variables and run the app again; use a controlled local database setup to create the initial account.

Open `http://127.0.0.1:5000` in your browser. Camera and GPS features generally require localhost or HTTPS and the user's permission.

## Routes

- `/` — login
- `/admin-dashboard` — admin dashboard
- `/student-dashboard` — student dashboard
- `/student-registration` — student registration
- `/face-collection` — face enrollment
- `/student-records` — admin student records
- `/attendance-records` — admin attendance records
- `/reports` — admin reports
- `/faculty-management` — admin faculty management
- `/faculty-dashboard` — faculty dashboard
- `/faculty/lectures` — faculty lecture list
- `/faculty/lectures/new` — schedule a lecture
- `/faculty-assignments` — faculty class/subject assignments
- `/mark-attendance` — student live face/GPS attendance
- `/my-attendance` — student attendance history
- `/change-password` — authenticated password change
- `/forgot-password` — password reset instructions
- `/health` — simple health check

## Privacy and security

- Never commit student face images, biometric data, passwords, or real student records.
- Keep `SECRET_KEY` and administrator credentials out of source control.
- Request camera and location permission before accessing them.
- Use HTTPS for deployed camera/GPS workflows.
- This is an educational project. Test authentication, face matching, GPS accuracy, and privacy safeguards before using it for real attendance decisions.

## Development status

The routes and templates are connected in the repository. The app still needs to be run and tested in the target Windows/Python environment, including dependency installation, database initialization, role-based flows, camera access, and GPS behavior. A successful GitHub commit does not replace local runtime testing.

## Automated checks

GitHub Actions runs a lightweight check on pushes and pull requests to `main`. It verifies that `app.py` compiles with Python 3.12 and that every statically referenced template and Flask endpoint exists. These checks do not install OpenCV/dlib or replace a full local test of login, database, camera, face matching, or GPS behavior.

Workflow: [FRAMS quality checks](.github/workflows/quality-check.yml)
