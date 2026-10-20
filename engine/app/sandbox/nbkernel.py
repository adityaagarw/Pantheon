"""Pantheon notebook kernel, with no dependencies: on this computer (started by the engine) or
inside the sandbox desktop (the cua-desktop pack).

A small HTTP server (port 8890) gives every notebook its own Python worker process
with a persistent namespace, like a Jupyter kernel:

    POST /execute   {"notebook", "code", "timeout"} -> {"count", "status", "outputs"}
    POST /interrupt {"notebook"}     POST /restart {"notebook"}     GET /health

Outputs are {"type": "stream", "name", "text"}, {"type": "result", "text", "html"?},
{"type": "image", "data": <base64 PNG>} (matplotlib figures) and
{"type": "error", "ename", "evalue", "traceback"}.

The engine starts it itself (PANTHEON_KERNEL_HOST=127.0.0.1, PANTHEON_KERNEL_PARENT: it
stops when the engine does), or copies it into the sandbox and starts it with --spawn.
"""

import ast
import base64
import io
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = 1
PORT = int(os.environ.get("PANTHEON_KERNEL_PORT", "8890"))
HOST = os.environ.get("PANTHEON_KERNEL_HOST", "0.0.0.0")  # noqa: S104 - the sandbox's network
HOME = os.environ.get("PANTHEON_KERNEL_HOME", "/tmp/pantheon-kernel")
WORKDIR = os.environ.get("PANTHEON_KERNEL_WORKDIR") or (
    "/home/cua/shared" if os.path.isdir("/home/cua/shared") else os.path.expanduser("~"))
WINDOWS = sys.platform == "win32"
MAX_TEXT = 100_000
IDLE_SECONDS = 4 * 3600


# --- worker: one per notebook ----------------------------------------------------------------


def _clip(text: str) -> str:
    if len(text) <= MAX_TEXT:
        return text
    return text[: MAX_TEXT // 2] + f"\n…[{len(text) - MAX_TEXT} characters omitted]…\n" \
        + text[-MAX_TEXT // 2:]


def _figures() -> list[dict]:
    plt = sys.modules.get("matplotlib.pyplot")
    if plt is None:
        return []
    out = []
    for num in plt.get_fignums():
        buf = io.BytesIO()
        try:
            plt.figure(num).savefig(buf, format="png", bbox_inches="tight", dpi=100)
            out.append({"type": "image", "data": base64.b64encode(buf.getvalue()).decode()})
        except Exception as e:  # noqa: BLE001 - a broken figure shouldn't lose the cell
            out.append({"type": "stream", "name": "stderr", "text": f"(figure failed: {e})\n"})
    plt.close("all")
    return out


def _result(value) -> dict:
    out = {"type": "result", "text": _clip(repr(value))}
    html = getattr(value, "_repr_html_", None)
    if callable(html):
        try:
            h = html()
            if isinstance(h, str):
                out["html"] = _clip(h)
        except Exception:  # noqa: BLE001
            pass
    return out


def _flag(token: str) -> str:
    return os.path.join(HOME, f"interrupt-{token}")


def _watch_interrupt() -> None:
    import _thread

    # A token, not the pid: on Windows a venv's python.exe starts the real one as a child.
    flag = _flag(os.environ["PANTHEON_KERNEL_TOKEN"])
    while True:
        time.sleep(0.2)
        if os.path.exists(flag):
            try:
                os.remove(flag)
            except OSError:
                pass
            _thread.interrupt_main()


def worker() -> None:
    os.environ.setdefault("MPLBACKEND", "Agg")
    if WINDOWS:  # Windows can't send SIGINT to a child: the server leaves a file instead
        threading.Thread(target=_watch_interrupt, daemon=True).start()
    reply = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    ns: dict = {"__name__": "__main__"}
    count = 0
    while True:
        try:
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            continue  # an interrupt that arrived between cells
        if not line:
            return
        code = json.loads(line)["code"]
        count += 1
        outputs: list[dict] = []
        out, err = io.StringIO(), io.StringIO()
        real = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        status, value = "ok", None
        try:
            tree = ast.parse(code, f"<cell {count}>", "exec")
            last = None
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                last = ast.Expression(tree.body.pop().value)
            exec(compile(tree, f"<cell {count}>", "exec"), ns)  # noqa: S102 - it's a notebook
            if last is not None:
                value = eval(compile(last, f"<cell {count}>", "eval"), ns)  # noqa: S307
        except BaseException as e:  # noqa: BLE001 - including KeyboardInterrupt and SystemExit
            status = "error"
            tb = e.__traceback__.tb_next if e.__traceback__ else None  # drop this frame
            outputs.append({"type": "error", "ename": type(e).__name__, "evalue": str(e),
                            "traceback": _clip("".join(traceback.format_exception(type(e), e, tb)))})
        finally:
            sys.stdout, sys.stderr = real
        streams = []
        if out.getvalue():
            streams.append({"type": "stream", "name": "stdout", "text": _clip(out.getvalue())})
        if err.getvalue():
            streams.append({"type": "stream", "name": "stderr", "text": _clip(err.getvalue())})
        outputs = streams + outputs + _figures()
        if value is not None:
            ns["_"] = value
            outputs.append(_result(value))
        reply.write(json.dumps({"count": count, "status": status, "outputs": outputs}) + "\n")


# --- server: routes cells to workers ---------------------------------------------------------


class Kernel:
    def __init__(self, notebook: str) -> None:
        self.notebook = notebook
        self.lock = threading.Lock()
        self.used = time.time()
        self.proc: subprocess.Popen | None = None
        self.token = f"{os.getpid()}-{id(self)}"
        self.replies: queue.Queue = queue.Queue()

    def start(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--worker"], cwd=WORKDIR,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, encoding="utf-8",
            env={**os.environ, "PANTHEON_KERNEL_TOKEN": self.token})
        self.replies = queue.Queue()
        threading.Thread(target=self._read, args=(self.proc, self.replies), daemon=True).start()

    @staticmethod
    def _read(proc: subprocess.Popen, q: queue.Queue) -> None:
        for line in proc.stdout:
            q.put(line)
        q.put(None)  # the worker exited

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self.proc = None

    def interrupt(self) -> None:
        if self.alive():
            if WINDOWS:
                os.makedirs(HOME, exist_ok=True)
                open(_flag(self.token), "w").close()  # noqa: SIM115
            else:
                self.proc.send_signal(signal.SIGINT)

    def execute(self, code: str, timeout: float) -> dict:
        with self.lock:
            self.used = time.time()
            if not self.alive():
                self.start()
            self.proc.stdin.write(json.dumps({"code": code}) + "\n")
            self.proc.stdin.flush()
            try:
                line = self.replies.get(timeout=timeout)
            except queue.Empty:
                self.interrupt()
                try:
                    line = self.replies.get(timeout=5)
                    reply = json.loads(line) if line else None
                except queue.Empty:
                    reply = None
                if reply is None:
                    self.stop()
                    return _failed("TimeoutError", f"the cell ran longer than {int(timeout)} s; "
                                   "the kernel was restarted, so variables are gone")
                reply["outputs"].append({"type": "stream", "name": "stderr",
                                         "text": f"(stopped after {int(timeout)} s)\n"})
                return reply
            if line is None:
                self.stop()
                return _failed("KernelDied", "the kernel stopped (out of memory, or the code "
                               "exited); it restarts on the next run, so variables are gone")
            return json.loads(line)


def _failed(ename: str, evalue: str) -> dict:
    return {"count": None, "status": "error",
            "outputs": [{"type": "error", "ename": ename, "evalue": evalue, "traceback": ""}]}


kernels: dict[str, Kernel] = {}
registry = threading.Lock()


def kernel(notebook: str) -> Kernel:
    with registry:
        k = kernels.get(notebook)
        if k is None:
            k = kernels[notebook] = Kernel(notebook)
        return k


def reaper() -> None:
    while True:
        time.sleep(300)
        with registry:
            for nb, k in list(kernels.items()):
                if time.time() - k.used > IDLE_SECONDS and not k.lock.locked():
                    k.stop()
                    del kernels[nb]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, {"ok": True, "version": VERSION, "workdir": WORKDIR,
                             "notebooks": [nb for nb, k in kernels.items() if k.alive()]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            nb = str(body.get("notebook") or "default")
            if self.path == "/execute":
                timeout = min(float(body.get("timeout") or 300), 3600)
                self._send(200, kernel(nb).execute(str(body.get("code") or ""), timeout))
            elif self.path == "/interrupt":
                kernel(nb).interrupt()
                self._send(200, {"ok": True})
            elif self.path == "/restart":
                k = kernel(nb)
                k.interrupt()
                with k.lock:
                    k.stop()
                self._send(200, {"ok": True})
            elif self.path == "/quit":
                self._send(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": f"{type(e).__name__}: {e}"})


def _watch_parent(pid: int) -> None:
    """Stop when the engine that started this kernel is gone."""
    while True:
        time.sleep(5)
        try:
            if WINDOWS:
                import ctypes

                h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # query limited info
                code = ctypes.c_ulong()
                got = bool(h) and ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
                alive = bool(got) and code.value == 259  # STILL_ACTIVE
                if h:
                    ctypes.windll.kernel32.CloseHandle(h)
            else:
                os.kill(pid, 0)
                alive = True
        except OSError:
            alive = False
        if not alive:
            os._exit(0)


def serve() -> None:
    threading.Thread(target=reaper, daemon=True).start()
    if parent := os.environ.get("PANTHEON_KERNEL_PARENT"):
        threading.Thread(target=_watch_parent, args=(int(parent),), daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    finally:
        for k in kernels.values():
            k.stop()


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        worker()
    elif sys.argv[1:2] == ["--spawn"]:  # detach: the computer-server waits for inherited sockets
        os.makedirs(HOME, exist_ok=True)
        log = open(os.path.join(HOME, "kernel.log"), "a")  # noqa: SIM115
        subprocess.Popen([sys.executable, os.path.abspath(__file__)], stdin=subprocess.DEVNULL,
                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    else:
        serve()
