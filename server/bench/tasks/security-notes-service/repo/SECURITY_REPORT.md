# External security review: notes service

Reviewer: external contractor (2 hours, limited scope)

## Findings

### F1 (High): SQL injection in `search_notes`

The query text goes into the SQL string. A user can read the notes of all users
with a query like `x%' OR 1=1 --`.

### F2 (High): Path traversal in `export_note`

The file name is joined to the export folder without a check. A file name like
`../../somewhere/file.txt` writes outside the export folder.

### F3 (Medium): Weak password storage

Passwords are stored as unsalted MD5. MD5 is fast to crack, and equal passwords
have equal hashes. The login check also uses a normal `==` compare.

## Not in scope

The reviewer did not check the other functions. The team should review them.
