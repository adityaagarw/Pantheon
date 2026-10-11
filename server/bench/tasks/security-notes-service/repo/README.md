# notes

A small service for private notes. The web app calls `NotesService`.

```python
from notes.service import NotesService

service = NotesService("notes.db", "exports/")
alice = service.create_user("alice", "correct horse battery staple")
user_id = service.login("alice", "correct horse battery staple")
note_id = service.add_note(user_id, "Shopping", "Milk, bread")
service.search_notes(user_id, "milk")
service.export_note(user_id, note_id, "shopping.txt")
```

## Rules

- A note belongs to one user. **A user can only read, search, change, export or
  delete their own notes.** For a note that does not exist or that belongs to
  another user:
  - `get_note` returns `None`,
  - `update_note` and `delete_note` return `False`,
  - `export_note` raises `LookupError`.
- `search_notes` finds notes where the title or the body contains the query
  (not case sensitive for ASCII letters).
- `export_note` writes the file into the export folder. The file name must be a
  plain file name, with no folder parts. Other file names raise `ValueError`.
- Passwords must be stored in a safe way. Nobody (also not an admin with a copy of
  the database) may be able to get a password back from the stored value.

There is no production data yet. You can change the format of the stored passwords.
