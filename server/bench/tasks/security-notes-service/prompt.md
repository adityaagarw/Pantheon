An external security review of the notes service found problems.
Read `SECURITY_REPORT.md`. The rules for the service are in `README.md`.

Do this:

1. Fix all findings in the report.
2. The review had a limited scope. Review all the code in `notes/` against the
   rules in the README, and fix any other security problem that you find.
3. Keep the normal features working. Keep the public methods of `NotesService`.
4. Add tests in `tests/` that try each attack and show that it no longer works.
