# Face-Recognition-Attendance-System
Recognition Attendance Management System using Flask

# Face Recognition Attendance Management System (FRAMS)

A web-based attendance management project built with Python Flask.

## Project Overview

FRAMS is designed to help manage student registration, face enrollment,
attendance records, and administrative operations through a web interface.

## Planned Features

- Admin and student login
- Admin dashboard
- Student registration
- Face data collection using a webcam
- Face recognition and attendance marking
- Attendance history and reports
- GPS latitude and longitude capture with permission
- Password management
- SQLite database

## Technology Stack

- Python 3.12
- Flask
- HTML and CSS
- JavaScript
- SQLite
- OpenCV
- face-recognition library

## Project Structure

```text
FRAMS_NEW/
├── app.py
├── config.py
├── requirements.txt
├── .gitignore
├── README.md
├── templates/
│   ├── login.html
│   ├── admin_dashboard.html
│   ├── student_dashboard.html
│   ├── student_registration.html
│   ├── face_collection.html
│   ├── attendance.html
│   ├── reports.html
│   └── change_password.html
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── camera.js
│       └── location.js
├── database/
├── face_data/
└── tests/
```

## Installation

1. Install Python 3.12.
2. Clone this repository.
3. Create a virtual environment.
4. Install the dependencies.
5. Configure environment variables.
6. Run the Flask application.

Example commands for Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Open `http://127.0.0.1:5000` in your browser.

## Current Development Status

The initial HTML interfaces and starter Flask code are being created.
Backend routes, authentication, database integration, face recognition,
GPS attendance storage, and testing still need to be completed.

## Privacy and Security

- Do not commit student face images or biometric data.
- Do not commit passwords, secret keys, or real student records.
- Request permission before accessing the camera or location.
- Use secure password hashing and authentication.
- Configure production settings before deployment.

## Disclaimer

This is an educational software project. Face recognition and attendance
records must be tested for accuracy, security, and privacy before real use.
