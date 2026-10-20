# Account service

Sign-in, sessions, password reset, profiles and avatars. `authsvc/app.py` is the front
door: the HTTP server (not in this repo) turns each request into
`App.dispatch(method, path, headers, body)`.

Other services rely on:

- `authsvc.users.UserStore(path)` with `create(username, email, password, role="user")`,
  `by_username`, `by_email`, `get` and `set_password`;
- `App(data_dir, secret, clock=time.time)`, its routes, and `app.outbox` (where the
  emails it sends are kept for the mailer);
- the existing users' passwords, which must keep working.

```
python -m unittest discover -s tests
```

Python 3.10+, standard library only.
