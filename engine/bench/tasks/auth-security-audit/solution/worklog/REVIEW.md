# Attack review

Tried to get around every fix: changed every digit of a token, dropped and emptied the
signature, replayed tokens past 8 hours, reused, expired and superseded reset tokens,
seeded `random` before resets, switched username case and used unknown names against the
lockout, sent X-Role, asked for other users' profiles, and uploaded traversal, absolute,
backslash, double-dot, non-image and over-long names. Old SHA-256 users still sign in and
are upgraded to PBKDF2.
