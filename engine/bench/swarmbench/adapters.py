"""Harness adapters.

An adapter starts one harness on one task. It gets a RunContext and returns
a dict with exit_code, timed_out and wall_seconds.

Adapter types (set "type" in the harness JSON file):

- command:  run a shell command. Use it for CLI harnesses.
- pantheon: run the task on a Pantheon organization through its API, automatically.
- manual:   show the prompt and wait for a person. Use it for UI harnesses.
- noop:     do nothing. A baseline: it shows the score of the untouched task.
- solution: copy the reference solution. Used by 'swarmbench validate'.
"""

import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .tasks import BENCH_DIR, BenchError
from .utils import copy_tree, write_json


@dataclass
class RunContext:
    task: object
    run_id: str
    task_key: str
    workspace: Path
    agent_workspace: str
    prompt_file: Path
    metrics_file: Path
    log_file: Path
    timeout_seconds: int

    def placeholders(self):
        return {
            "{prompt_file}": str(self.prompt_file),
            "{workspace}": str(self.workspace),
            "{agent_workspace}": self.agent_workspace,
            "{metrics_file}": str(self.metrics_file),
            "{task_id}": self.task.id,
            "{task_key}": self.task_key,
            "{run_id}": self.run_id,
            "{bench_dir}": str(BENCH_DIR),
            "{python}": sys.executable,
        }

    def fill(self, text):
        for key, value in self.placeholders().items():
            text = text.replace(key, value)
        return text


def _kill_tree(proc):
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            proc.kill()


class CommandAdapter:
    parallel_ok = True

    def __init__(self, config):
        self.command = config["command"]
        self.cwd = config.get("cwd", "{workspace}")
        self.env = config.get("env", {})

    def run(self, ctx):
        command = ctx.fill(self.command)
        env = dict(os.environ)
        env.update({k: ctx.fill(v) for k, v in self.env.items()})
        env.update({
            "SWARMBENCH_PROMPT_FILE": str(ctx.prompt_file),
            "SWARMBENCH_WORKSPACE": str(ctx.workspace),
            "SWARMBENCH_METRICS_FILE": str(ctx.metrics_file),
            "SWARMBENCH_TASK_ID": ctx.task.id,
        })
        start = time.monotonic()
        timed_out = False
        with open(ctx.log_file, "w", encoding="utf-8", errors="replace") as log:
            log.write(f"$ {command}\n\n")
            log.flush()
            extra = {} if os.name == "nt" else {"start_new_session": True}
            proc = subprocess.Popen(command, shell=True, cwd=ctx.fill(self.cwd), env=env,
                                    stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **extra)
            try:
                exit_code = proc.wait(timeout=ctx.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_tree(proc)
                exit_code = proc.wait()
        return {"exit_code": exit_code, "timed_out": timed_out, "wall_seconds": round(time.monotonic() - start, 1)}


class ManualAdapter:
    """For harnesses with a UI. A person gives the prompt to the harness."""

    parallel_ok = False  # one person, one prompt at a time

    def __init__(self, config):
        self.instructions = config.get("instructions", "Give the prompt to the harness.")

    def run(self, ctx):
        print("\n" + "=" * 70)
        print(f"MANUAL TASK: {ctx.task.id}  ({ctx.task_key})")
        print(f"Work folder (host):  {ctx.workspace}")
        print(f"Work folder (agent): {ctx.agent_workspace}")
        print(f"Prompt file:         {ctx.prompt_file}")
        print("-" * 70)
        print(ctx.fill(self.instructions))
        print("-" * 70)
        print(ctx.prompt_file.read_text(encoding="utf-8"))
        print("=" * 70)
        input("Press Enter when you give the prompt to the harness (the timer starts) ... ")
        start = time.monotonic()
        answer = input("Press Enter when the harness is done. Type 'timeout' if it did not finish: ").strip().lower()
        wall = round(time.monotonic() - start, 1)
        metrics = {}
        for key, label in (("input_tokens", "Input tokens"), ("output_tokens", "Output tokens"),
                           ("cost_usd", "Cost in USD"), ("agents", "Number of agents"), ("turns", "Number of turns")):
            value = input(f"  {label} (optional, Enter to skip): ").strip()
            if value:
                try:
                    metrics[key] = float(value) if "." in value else int(value)
                except ValueError:
                    pass
        if metrics:
            write_json(ctx.metrics_file, metrics)
        ctx.log_file.write_text(f"manual run, answer={answer!r}\n", encoding="utf-8")
        return {"exit_code": 0, "timed_out": answer == "timeout", "wall_seconds": wall}


class PantheonAdapter:
    """Runs each task on a copy of a Pantheon organization, through Pantheon's API.

    Pantheon makes a temporary copy of the organization with the work folder as its
    workspace, gives the prompt to the team lead, approves tool use, waits until the
    team is quiet (or the time is up), and reports tokens, cost, turns and agents.
    The work folder must be one that Pantheon's agents can see: set ``workspace_root``
    to a folder inside PANTHEON_WORKSPACES and ``agent_workspace`` to the same folder
    as Pantheon sees it (under /workspaces).
    """

    parallel_ok = True

    def __init__(self, config):
        self.url = str(config.get("url", "https://localhost:3711")).rstrip("/")
        if not config.get("org"):
            raise BenchError("error: the pantheon harness needs \"org\": the organization to benchmark")
        self.org = config["org"]
        self.lead = config.get("lead")
        self.keep_org = bool(config.get("keep_org", False))
        self.poll_seconds = float(config.get("poll_seconds", 5))
        self.idle_seconds = int(config.get("idle_seconds", 45))
        self.context = ssl._create_unverified_context() if config.get("insecure", True) else None

    def _call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60, context=self.context) as resp:
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Pantheon {method} {path}: {e.code} {e.read().decode(errors='replace')[:500]}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"can't reach Pantheon at {self.url}: {e.reason}") from None

    def run(self, ctx):
        start = time.monotonic()
        with open(ctx.log_file, "w", encoding="utf-8") as log:
            try:
                attempt = self._call("POST", "/api/v1/bench/attempts", {
                    "org": self.org, "lead": self.lead, "label": ctx.task_key,
                    "prompt": ctx.prompt_file.read_text(encoding="utf-8"),
                    "workspace": ctx.agent_workspace, "timeoutSeconds": ctx.timeout_seconds,
                    "idleSeconds": self.idle_seconds, "keepOrg": self.keep_org})
            except RuntimeError as e:
                log.write(f"{e}\n")
                return {"exit_code": 1, "timed_out": False, "wall_seconds": 0.0, "error": str(e)}
            log.write(f"attempt {attempt['id']} on a copy of {attempt.get('orgName', self.org)}\n")
            seen = None
            try:
                while attempt["status"] in ("starting", "running"):
                    time.sleep(self.poll_seconds)
                    if time.monotonic() - start > ctx.timeout_seconds + 180:
                        self._call("POST", f"/api/v1/bench/attempts/{attempt['id']}/cancel")
                    attempt = self._call("GET", f"/api/v1/bench/attempts/{attempt['id']}")
                    line = f"{attempt['status']}: {attempt.get('detail', '')}"
                    if line != seen:
                        log.write(f"[{round(time.monotonic() - start)} s] {line}\n")
                        log.flush()
                        seen = line
            except KeyboardInterrupt:
                self._call("POST", f"/api/v1/bench/attempts/{attempt['id']}/cancel")
                raise
            if attempt.get("metrics"):
                write_json(ctx.metrics_file, attempt["metrics"])
            log.write(f"finished: {attempt['status']}\n")
        return {"exit_code": 0 if attempt["status"] == "done" else 1,
                "timed_out": attempt["status"] == "timeout",
                "wall_seconds": round(time.monotonic() - start, 1)}


class NoopAdapter:
    def __init__(self, config):
        pass

    def run(self, ctx):
        ctx.log_file.write_text("noop harness: no changes\n", encoding="utf-8")
        return {"exit_code": 0, "timed_out": False, "wall_seconds": 0.0}


class SolutionAdapter:
    def __init__(self, config):
        pass

    def run(self, ctx):
        start = time.monotonic()
        if ctx.task.solution_path.exists():
            copy_tree(ctx.task.solution_path, ctx.workspace)
        ctx.log_file.write_text("solution harness: copied reference solution\n", encoding="utf-8")
        return {"exit_code": 0, "timed_out": False, "wall_seconds": round(time.monotonic() - start, 1)}


ADAPTERS = {
    "command": CommandAdapter,
    "pantheon": PantheonAdapter,
    "manual": ManualAdapter,
    "noop": NoopAdapter,
    "solution": SolutionAdapter,
}


def make_adapter(config):
    kind = config.get("type")
    if kind not in ADAPTERS:
        raise BenchError(f"error: unknown harness type '{kind}'. Use one of: {', '.join(ADAPTERS)}")
    return ADAPTERS[kind](config)
