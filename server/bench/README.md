# SwarmBench

A small benchmark for **agent swarms and agent harnesses** (for example
[Pantheon](https://github.com/adityaagarw/Pantheon), Claude Code, Aider, or your own).

SwarmBench gives a harness real work tasks. Each task has a starting repo, a
prompt, a recommended **workflow** (roles and steps), hidden tests, and a
reference solution. After the harness stops, SwarmBench grades the work folder
and writes a report.

- Python 3.10+ and the standard library only. No install needed.
- Harness-agnostic: CLI harnesses run automatically, UI harnesses run in manual mode.
- Every task is checked: the start state fails, the reference solution gets 100.

## Quick start

```
python -m swarmbench list                          # the tasks
python -m swarmbench show bugfix-invoice-totals    # the prompt that a harness gets
python -m swarmbench validate                      # check that all tasks are fair and solvable

python -m swarmbench run --harness noop            # floor score (no work done)
python -m swarmbench run --harness reference --workflow none   # ceiling score (reference solutions)
python -m swarmbench run --harness claude-code --tasks bugfix-invoice-totals
python -m swarmbench compare runs/<run-a> runs/<run-b>
python -m swarmbench compare runs/<run-a> runs/<run-b> --json   # the summaries, for tools
```

`--jobs N` runs N tasks at once (command and pantheon harnesses).

Results go to `runs/<run-id>/`: `report.md`, `summary.json` (the run in numbers), and for each task `prompt.md`,
`harness.log`, `metrics.json` and `result.json`. Work folders go to
`~/.swarmbench/workspaces/<run-id>/<task>/` and stay there for inspection.

## Tasks

| Task | Category | Workflow | What it tests |
| --- | --- | --- | --- |
| `bugfix-invoice-totals` | bugfix | triage-fix-verify | 5 billing bugs from a finance ticket: tax class, discount order, rounding, crash, negative total. |
| `feature-todo-due-dates` | feature | plan-build-review | A feature across CLI parsing, storage, output, filters, sorting, errors, old data and docs. |
| `refactor-legacy-pricing` | refactor | characterize-refactor | Restructure nested legacy code with exact parity on 3,000+ random orders, size limits, and a new rule. |
| `security-notes-service` | security | audit-fix-verify | Fix 3 reported vulnerabilities, and find a 4th (access control) that the report missed. |
| `data-sales-cleanup` | data | profile-build-check | Dirty CSV: date formats, region aliases, money strings, corrections, bad rows. Graded on a hidden second file too. |
| `incident-postmortem` | investigation | investigate-report | Find the root cause in 16k log lines from 6 sources with red herrings. Exact metrics and a postmortem. |
| `parallel-report-exporters` | parallel-build | parallel-fanout | Four independent exporters with exact output specs, then registry and CLI integration. |
| `perf-sessionize` | performance | measure-optimize-verify | Three O(n²) hot spots. Exact output parity and a 200k-event time limit. |

## Workflows

A workflow tells the harness which roles to use, in which order, and which work
records to write (for example `worklog/PLAN.md`). The workflow text is added to
the prompt. See them with `python -m swarmbench workflows`.

| Workflow | Roles | Shape |
| --- | --- | --- |
| `solo` | engineer | one agent (baseline) |
| `triage-fix-verify` | triager, fixer, verifier | sequential, with a review loop |
| `plan-build-review` | planner, builder, doc-writer, reviewer | builder and doc-writer in parallel |
| `characterize-refactor` | characterizer, refactorer, reviewer | tests first, then small steps |
| `audit-fix-verify` | auditor, fixer, attacker | fixer and attacker in parallel |
| `profile-build-check` | data-analyst, builder, checker | independent recalculation |
| `investigate-report` | investigators (many), analyst, writer, reviewer | fan-out, then join |
| `parallel-fanout` | lead, workers (many), integrator, tester | fan-out by file ownership |
| `measure-optimize-verify` | profiler, optimizer, verifier | measure before and after |

The `--workflow` option controls this:

- `--workflow task` (default): use the workflow of each task.
- `--workflow none`: no workflow text. The harness uses its own way of working.
- `--workflow solo` (or any name): use this workflow for all tasks.

Compare the same harness with `task`, `none` and `solo` to see if the swarm
structure helps.

## Scoring

Each task gets a score from 0 to 100. It is the weighted mean of four parts:

| Part | Default weight | Score |
| --- | ---: | --- |
| `hidden_tests` | 70 | Share of hidden tests that pass. |
| `agent_tests` | 10 | 1.0 if the tests in the work folder pass and new tests were added. 0.5 if they pass with no new tests. 0 if they fail. |
| `safety` | 10 | Share of protected files (and original tests) that are not changed. |
| `process` | 10 | Share of the workflow work records that exist and have real content. |

- If the workflow is `none`, or a part does not apply, its weight is not used.
- If the harness changed no files, `agent_tests` and `safety` are 0.
- `gate_tests` (in `task.json`): if a gate test fails, `hidden_tests` is 0.
  The perf task uses it: correct but slow code gets no points.
- Not scored, but reported: wall time, timeouts, tokens, cost, files and lines changed.

Agents are not deterministic. Use `--repeat 3` to see the variance.

## Harnesses

A harness is a JSON file in `harnesses/` (or any path). Types:

| Type | Use |
| --- | --- |
| `command` | Runs a shell command for each task. For CLI harnesses. |
| `pantheon` | Runs each task on a copy of a Pantheon organization, through Pantheon's API. |
| `manual` | Shows the prompt and waits. A person gives it to a UI harness and presses Enter when it is done. |
| `noop` | Does nothing. The floor score. |
| `solution` | Copies the reference solution. The ceiling score, and a test of the benchmark. |

Command placeholders: `{prompt_file}`, `{workspace}`, `{agent_workspace}`,
`{metrics_file}`, `{task_id}`, `{task_key}`, `{run_id}`, `{bench_dir}`, `{python}`.
The same values are also in the environment variables `SWARMBENCH_PROMPT_FILE`,
`SWARMBENCH_WORKSPACE`, `SWARMBENCH_METRICS_FILE` and `SWARMBENCH_TASK_ID`.

To report tokens and cost, the harness writes JSON to the metrics file, for example
`{"input_tokens": 12000, "output_tokens": 3000, "cost_usd": 0.42, "turns": 31, "agents": 4}`.
`harnesses/wrappers/claude_code.py` does this for Claude Code.

Other settings: `workspace_root` (where work folders go) and `agent_workspace`
(the work folder path that the agent sees, if it is different, for example in a container).

### Pantheon

Pantheon runs SwarmBench itself: open **Benchmarks** in Pantheon, pick an organization and
tasks, and compare runs side by side. Each task runs on a temporary copy of the
organization (same agents, models, tools and reporting lines; every tool approved
automatically), and the copy is deleted when the task is graded.

To drive Pantheon from this command line instead, use the automatic `pantheon` harness
(`harnesses/pantheon.json`):

1. Set `org` to the organization to benchmark (its name or id) and `url` to Pantheon.
2. Set `workspace_root` to a folder inside `PANTHEON_WORKSPACES` on this computer, and
   `agent_workspace` to the same folder as Pantheon sees it (under `/workspaces`).
3. Run `python -m swarmbench run --harness pantheon --tasks bugfix-invoice-totals --jobs 2`.

Other settings: `lead` (who gets the task; default the top of the org chart),
`idle_seconds` (how long the team must be quiet before the task counts as finished),
`keep_org` (keep the copies to look at them), `insecure` (accept Pantheon's self-signed
certificate). `harnesses/pantheon-manual.json` is the old manual mode.

### Claude Code

`harnesses/claude-code.json` runs `claude -p` with `--dangerously-skip-permissions`
in the work folder. **Run it only in a sandbox or a VM.** Claude Code can use its
own subagents for the workflow roles.

## Fair runs

- Work folders are outside the benchmark folder by default. An agent that can read
  the whole disk can still find `tasks/*/hidden_tests` and `tasks/*/solution`.
  For a strict run, put the harness in a container that can see only the work folder.
- The perf task has a time limit (3 s for 200k events). The reference solution
  takes about 0.5 s on a laptop. Very slow machines can fail it.
- Hidden tests run on a copy of the work folder, in a separate process, with a time limit.

## Add a task

```
tasks/<task-id>/
  task.json        id, title, category, difficulty, workflow, summary, time_limit_minutes,
                   protected_files, agent_tests_dir (or null), gate_tests, weights
  prompt.md        the task text (the general rules and the workflow are added automatically)
  repo/            the starting files. The harness gets a copy.
  hidden_tests/    unittest files (test*.py). SWARMBENCH_WORKSPACE points to the work folder.
  solution/        files that are copied over repo/ to make the reference solution
```

Then run `python -m swarmbench validate --tasks <task-id>`. A good task:

- has exact, written acceptance rules (the hidden tests check only what the prompt and files say),
- fails in the start state and gets 100 with the reference solution,
- gives partial credit (many small tests, not one big test),
- cannot be solved by writing expected values into the code (use a second hidden input).

## Folder layout

```
swarmbench/     the runner, grader, adapters and reports
tasks/          the benchmark tasks
workflows/      the workflow templates
harnesses/      harness configs and wrappers
runs/           results (not in git)
```
