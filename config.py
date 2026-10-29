

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


class Config:
    # Set a strong secret key in the environment before deployment.
    SECRET_KEY = os.environ.get("SECRET_KEY")

    # SQLite database location on the local machine or server.
    DATABASE = str(BASE_DIR / "database" / "attendance.db")

    # Upload limits for future enrollment features.
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024

    # Disable debug mode by default.
    DEBUG = False

    # Session cookie security settings.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = (
        os.environ.get("COOKIE_SECURE", "false").lower() == "true"
    )
