"""Notes service: users, login and private notes."""

import hashlib
import hmac
import os
import sqlite3
from pathlib import Path, PurePosixPath, PureWindowsPath

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    body TEXT NOT NULL
);
"""

PBKDF2_ITERATIONS = 200_000


def hash_password(password, salt=None, iterations=PBKDF2_ITERATIONS):
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        scheme, iterations, salt_hex, _ = stored.split("$")
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    expected = hash_password(password, bytes.fromhex(salt_hex), int(iterations))
    return hmac.compare_digest(expected, stored)


def escape_like(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def safe_file_name(filename):
    """Allow only a plain file name: no folders, no drive, no '..'."""
    name = str(filename)
    if not name or name in (".", ".."):
        raise ValueError("invalid file name")
    if PurePosixPath(name).name != name or PureWindowsPath(name).name != name:
        raise ValueError("file name must not contain folders")
    return name


class NotesService:
    def __init__(self, db_path, export_dir):
        self.db = sqlite3.connect(str(db_path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def close(self):
        self.db.close()

    # Users

    def create_user(self, username, password):
        cur = self.db.execute("INSERT INTO users (username, password) VALUES (?, ?)",
                              (username, hash_password(password)))
        self.db.commit()
        return cur.lastrowid

    def login(self, username, password):
        """Return the user id, or None if the login fails."""
        row = self.db.execute("SELECT id, password FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            hash_password(password)  # same work, so timing does not show if the user exists
            return None
        return row["id"] if verify_password(password, row["password"]) else None

    # Notes

    def add_note(self, user_id, title, body):
        cur = self.db.execute("INSERT INTO notes (user_id, title, body) VALUES (?, ?, ?)", (user_id, title, body))
        self.db.commit()
        return cur.lastrowid

    def get_note(self, user_id, note_id):
        """Return the note as a dict, or None if it does not exist or is not the user's note."""
        row = self.db.execute(
            "SELECT id, user_id, title, body FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id)
        ).fetchone()
        return dict(row) if row else None

    def list_notes(self, user_id):
        rows = self.db.execute(
            "SELECT id, user_id, title, body FROM notes WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def search_notes(self, user_id, query):
        """Find notes of the user where the title or the body contains the query."""
        pattern = f"%{escape_like(query)}%"
        rows = self.db.execute(
            "SELECT id, user_id, title, body FROM notes WHERE user_id = ? "
            "AND (title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\') ORDER BY id",
            (user_id, pattern, pattern),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_note(self, user_id, note_id, body):
        cur = self.db.execute("UPDATE notes SET body = ? WHERE id = ? AND user_id = ?", (body, note_id, user_id))
        self.db.commit()
        return cur.rowcount == 1

    def delete_note(self, user_id, note_id):
        cur = self.db.execute("DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id))
        self.db.commit()
        return cur.rowcount == 1

    def export_note(self, user_id, note_id, filename):
        """Write the note to a text file in the export folder. Return the path."""
        name = safe_file_name(filename)
        note = self.get_note(user_id, note_id)
        if note is None:
            raise LookupError("note not found")
        path = self.export_dir / name
        path.write_text(f"{note['title']}\n\n{note['body']}\n", encoding="utf-8")
        return path
