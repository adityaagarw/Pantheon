The account service failed its penetration test (`PENTEST_REPORT.md`). Fix every finding,
then bring the service up to `SECURITY_REQUIREMENTS.md`: the testers say they ran out of
time, and the requirements cover more than they checked.

Existing users must still be able to sign in with their current passwords, and everything
other services rely on (see `README.md`) must keep working.

Do this:

1. Audit the code against the report and the requirements, and list every issue you find
   with where it is, including ones the report doesn't mention.
2. Fix them. Independent fixes can be made at the same time.
3. Attack your own fixes: try to get around each one, and add a test in `tests/`
   (unittest) for each issue.
4. Don't change `PENTEST_REPORT.md` or `SECURITY_REQUIREMENTS.md`.
