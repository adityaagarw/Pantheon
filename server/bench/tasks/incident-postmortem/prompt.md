You are the incident review team for incident INC-0914.
The evidence is in `logs/` and `alerts.json`. `README.md` explains the system.

Find out what happened and why. Write two files:

## 1. `postmortem.md`

A blameless postmortem for engineers and managers. Use these Markdown headings:

- `## Summary`
- `## Impact` (with numbers)
- `## Timeline` (UTC times)
- `## Root Cause`
- `## Resolution`
- `## Action Items` (at least 3 items, as a list)

## 2. `findings.json`

Use exactly these keys. Calculate the numbers from the raw logs with a script.

| Key | Meaning |
| --- | --- |
| `incident_start` | Start of the first minute in which more than 5% of gateway requests got a 5xx status. Format `2026-09-14T14:00:00Z`. |
| `detected_at` | The time when the first alert **for this incident** fired. |
| `mitigated_at` | The time when the action that fixed the problem finished. |
| `recovered_at` | Start of the first minute, at or after `mitigated_at`, in which less than 1% of gateway requests got a 5xx status. |
| `root_cause_service` | The name of the service that caused the incident. |
| `trigger_version` | The version of that service that caused the incident, for example `"1.2.3"`. |
| `root_cause_summary` | One or two sentences about the technical cause. |
| `peak_error_rate_percent` | The highest 5xx rate of any one minute at the gateway, in percent (for example `12.5`). |
| `failed_requests` | Number of gateway requests with a 5xx status from `incident_start` up to (not including) `recovered_at`. |
| `affected_endpoints` | Sorted list of gateway paths that returned at least one 5xx status in that same period. |
| `time_to_detect_minutes` | Minutes from `incident_start` to `detected_at`. |
| `time_to_mitigate_minutes` | Minutes from `incident_start` to `mitigated_at`. |

A minute is a clock minute (for example 14:05:00.000 to 14:05:59.999).
Be careful: not every warning and not every change in the logs is part of this incident.
