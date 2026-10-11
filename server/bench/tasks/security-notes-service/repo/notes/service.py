"""Notes service: users, login and private notes."""

import hashlib
import sqlite3
from pathlib import Path

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
        hashed = hashlib.md5(password.encode()).hexdigest()
        cur = self.db.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed))
        self.db.commit()
        return cur.lastrowid

    def login(self, username, password):
        """Return the user id, or None if the login fails."""
        row = self.db.execute("SELECT id, password FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            return None
        if hashlib.md5(password.encode()).hexdigest() == row["password"]:
            return row["id"]
        return None

    # Notes

    def add_note(self, user_id, title, body):
        cur = self.db.execute("INSERT INTO notes (user_id, title, body) VALUES (?, ?, ?)", (user_id, title, body))
        self.db.commit()
        return cur.lastrowid

    def get_note(self, user_id, note_id):
        """Return the note as a dict, or None."""
        row = self.db.execute("SELECT id, user_id, title, body FROM notes WHERE id = ?", (note_id,)).fetchone()
        return dict(row) if row else None

    def list_notes(self, user_id):
        rows = self.db.execute(
            "SELECT id, user_id, title, body FROM notes WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def search_notes(self, user_id, query):
        """Find notes of the user where the title or the body contains the query."""
        sql = (
            f"SELECT id, user_id, title, body FROM notes WHERE user_id = {int(user_id)} "
            f"AND (title LIKE '%{query}%' OR body LIKE '%{query}%') ORDER BY id"
        )
        return [dict(r) for r in self.db.execute(sql).fetchall()]

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
        note = self.get_note(user_id, note_id)
        if note is None:
            raise LookupError("note not found")
        path = self.export_dir / filename
        path.write_text(f"{note['title']}\n\n{note['body']}\n", encoding="utf-8")
        return path
