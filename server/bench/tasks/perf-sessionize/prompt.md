The nightly analytics job is too slow. With 2 million events it runs for more than 6 hours.
The code is in `analytics/sessions.py`.

Goal: `sessionize`, then `bounce_rate`, then `top_pages` on **200,000 events** must take
**less than 3 seconds** in total on a normal laptop.

Do this:

1. Measure the current speed. Find all slow parts.
2. Make the code fast. Keep the public functions and their arguments.
3. The output must be exactly the same as before for every input, including the order
   of sessions, the order of pages, and events with the same time.
4. Add tests in `tests/` that compare the new code with the old behavior.
