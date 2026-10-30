"""Create or reset the local FRAMS administrator account.

Only a Werkzeug password hash is stored in SQLite. Passwords are entered
interactively and must never be committed to the repository.
"""
from getpass import getpass
from pathlib import Path
import sqlite3
import sys

from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "database" / "attendance.db"


def main():
    username = input("Admin ID [admin]: ").strip() or "admin"
    if len(username) < 3:
        print("Admin ID must be at least 3 characters.")
        return 1

    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(DATABASE), timeout=15) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL
            )
        """)
        existing = conn.execute(
            "SELECT username FROM admins WHERE username = ?", (username,)
        ).fetchone()
        if existing:
            reset = input(
                f"Admin '{username}' already exists. Reset its password? [y/N]: "
            ).strip().lower()
            if reset != "y":
                print("Existing admin left unchanged.")
                return 0

        password = getpass("Create admin password (at least 8 characters): ")
        confirm = getpass("Confirm admin password: ")
        if len(password) < 8:
            print("Password must contain at least 8 characters.")
            return 1
        if password != confirm:
            print("Passwords do not match.")
            return 1

        conn.execute("""
            INSERT INTO admins (username, password_hash)
            VALUES (?, ?)
            ON CONFLICT(username) DO UPDATE SET
                password_hash = excluded.password_hash
        """, (username, generate_password_hash(password)))

    print("\\nAdmin account is ready.")
    print(f"Admin ID: {username}")
    print("Password: the password you just entered (not stored as plain text).")
    print("Keep it private and change it from the dashboard after login.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
