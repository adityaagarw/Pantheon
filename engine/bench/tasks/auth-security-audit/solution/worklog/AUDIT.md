# Audit

Reported: F1 tokens never expire (tokens.verify ignores the time); F2 reset tokens from
random seeded with the clock (app.reset_request); F3 avatar path traversal (app.avatar);
F4 open redirect (app.login returns any next); F5 enumeration (login and reset messages);
F6 one round of SHA-256 (users.hash_password).

Not reported:
- tokens.sign covers only the user id, so the expiry can be changed; an empty signature
  skips the check entirely;
- reset tokens are never removed: reusable, never expire, older ones stay valid;
- the lockout is keyed on the username as typed, so "BOB" dodges bob's lockout, and
  unknown usernames are never locked (another enumeration signal);
- is_admin trusts an X-Role header;
- /users/<id>/profile shows anyone's email to any signed-in user;
- signatures and hashes are compared with ==.
