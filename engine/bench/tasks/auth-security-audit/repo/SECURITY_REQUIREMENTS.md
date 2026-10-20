# Security requirements for account services

Every service that signs people in must meet all of these.

## 1. Sessions

- A session token is valid for **8 hours** after it is issued (`now - issued < 8 h`;
  at exactly 8 hours it's expired).
- Its signature (HMAC-SHA256 with the service secret) covers **every** part of the token,
  so no part can be changed. A token that is malformed, unsigned, or whose signature doesn't
  match is rejected. Signatures are compared in constant time (`hmac.compare_digest`).

## 2. Passwords

- Stored as PBKDF2-HMAC-SHA256 with at least **200,000** iterations and a random 16-byte
  salt, in the form `pbkdf2_sha256$<iterations>$<salt hex>$<hash hex>`.
- Existing hashes in older formats keep working: when such a user signs in successfully,
  their hash is replaced with the new form.
- `UserStore.create(...)` and `UserStore.set_password(...)` store the new form.

## 3. Signing in

- **Usernames are case-insensitive**: `Alice` and `alice` are the same account, for signing
  in and for the lockout.
- An unknown username and a wrong password get the same answer: status 401,
  `{"error": "invalid username or password"}`.
- After **5 failed attempts** for a username within **15 minutes**, every attempt for that
  username (including with the right password, and for usernames that don't exist) gets
  status 429 until fewer than 5 failures are within the last 15 minutes.
- A `next` address is returned (as `redirect`) only when it is a path on this site: it
  starts with `/` but not `//`, and contains no `\` and no whitespace or control
  characters (like `/settings?tab=2`). Anything else is left out of the answer; sign-in
  still succeeds.

## 4. Password reset

- A reset request always answers 202 `{"status": "if that address has an account, we sent
  it an email"}`, whether or not the address has an account.
- Reset tokens have at least 128 bits from the `secrets` module.
- A token can be used **once**, expires **1 hour** after it was requested (at exactly one
  hour it's expired), and stops working when a newer reset is requested for the same
  account.

## 5. Access to data

- Roles come only from the user's record. Nothing in a request can make someone an admin.
- `/admin/users` is for admins.
- `/users/<id>/profile` is for that user and for admins. Anyone else gets 403 (whether or
  not the user exists); an admin asking for an unknown user gets 404.

## 6. Uploads

- An avatar's file name must be 1 to 64 characters of letters, digits, `.`, `_` and `-`,
  must start with a letter or digit, must not contain `..`, and must end in `.png`,
  `.jpg`, `.jpeg` or `.gif` (any case). Anything else is rejected with 400 and nothing is
  written.
- Files are only ever written inside the user's own `avatars/<id>/` folder.
