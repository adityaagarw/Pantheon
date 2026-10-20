# ruff: noqa: E501  (protocol messages, one per line as the programs print them)
"""Stand-ins for the agent programs Pantheon connects to (app/agents/connect), speaking
their protocols the way the real ones do, so the connectors are tested without accounts.

Each is written to a file and started by the connector like the real program. They log
what they were started with to ``<script>.log`` (one JSON line per start).
"""

CLAUDE = r'''
import json, os, queue, sys, threading, time

LOG = __file__ + ".log"
args = sys.argv[1:]
with open(LOG, "a") as f:
    f.write(json.dumps(args) + "\n")
with open(__file__ + ".env.log", "a") as f:  # which account it was started with
    f.write(json.dumps({"config": os.environ.get("CLAUDE_CONFIG_DIR"),
                        "key": os.environ.get("ANTHROPIC_API_KEY")}) + "\n")
if "--resume" in args and args[args.index("--resume") + 1] == "gone":
    sys.stderr.write("No conversation found with session ID: gone\n")
    sys.exit(1)
system = ""
if "--append-system-prompt-file" in args:
    system = open(args[args.index("--append-system-prompt-file") + 1], encoding="utf-8").read()

lines = queue.Queue()
def reader():
    for line in sys.stdin:
        lines.put(json.loads(line))
    lines.put(None)
threading.Thread(target=reader, daemon=True).start()

def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

def get(timeout=None):
    try:
        return lines.get(timeout=timeout)
    except queue.Empty:
        return "empty"

out({"type": "system", "subtype": "init", "session_id": "sess-1"})
n = 0
while True:
    msg = get()
    if msg is None:
        break
    if msg.get("type") == "control_request":
        out({"type": "control_response", "response": {"subtype": "success", "request_id": msg["request_id"], "response": {}}})
        continue
    if msg.get("type") != "user":
        continue
    out({"type": "user", "isReplay": True, "uuid": msg.get("uuid"), "message": msg["message"]})
    text = msg["message"]["content"]
    n += 1
    said = []
    if "LIMIT" in text:
        out({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": int(time.time()) + 3600}})
        out({"type": "result", "subtype": "success", "is_error": True, "result": "Claude AI usage limit reached", "session_id": "sess-1"})
        continue
    out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "hmm"}}})
    if "RISKY" in text:
        out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "rm -rf build"}}]}})
        out({"type": "control_request", "request_id": "perm1", "request": {"subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "rm -rf build"}}})
        while True:
            ans = get()
            if ans is None or ans == "empty":
                break
            if ans.get("type") == "control_response":
                break
            lines.put(ans)
        allowed = ans not in (None, "empty") and ans["response"]["response"]["behavior"] == "allow"
        out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "removed build" if allowed else "denied", "is_error": not allowed}]}})
        said.append("ran it" if allowed else "was refused")
    if "SLOW" in text:
        end = time.time() + 6
        while time.time() < end:
            more = get(timeout=0.2)
            if more in ("empty", None):
                continue
            if more.get("type") == "user":
                out({"type": "user", "isReplay": True, "uuid": more.get("uuid"), "message": more["message"]})
                said.append("also: " + more["message"]["content"].splitlines()[-1])
                break
    if "PANTHEON-RULES" in system or "Working in Pantheon" in system:
        said.append("knows-pantheon")
    if "What you did lately" in system:
        said.append("knows-recent")
    out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}}})
    out({"type": "result", "subtype": "success", "is_error": False, "session_id": "sess-1",
         "result": "reply %d: %s [%s]" % (n, text.splitlines()[-1][:60], ", ".join(said)),
         "total_cost_usd": 0.01, "usage": {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 7}})
'''

CODEX = r'''
import json, sys

LOG = __file__ + ".log"
args = sys.argv[1:]
if args[:2] == ["exec", "--help"]:
    print("Usage: codex exec [OPTIONS] [PROMPT] [COMMAND]\n  resume  Resume a previous session\n      --json  Print events to stdout as JSONL")
    sys.exit(0)
with open(LOG, "a") as f:
    f.write(json.dumps(args) + "\n")
prompt = sys.stdin.read()
last = args[args.index("--output-last-message") + 1]
def out(obj):
    print(json.dumps(obj), flush=True)
if "LIMIT" in prompt:
    out({"type": "turn.failed", "error": {"message": "You've hit your usage limit. Try again in 2 hours."}})
    sys.exit(1)
thread = args[args.index("resume") + 1] if "resume" in args else "thread-9"
out({"type": "thread.started", "thread_id": thread})
out({"type": "item.completed", "item": {"id": "r1", "type": "reasoning", "text": "thinking it over"}})
out({"type": "item.started", "item": {"id": "c1", "type": "command_execution", "command": "ls"}})
out({"type": "item.completed", "item": {"id": "c1", "type": "command_execution", "command": "ls", "aggregated_output": "a.py", "exit_code": 0}})
out({"type": "item.completed", "item": {"id": "f1", "type": "file_change", "changes": [{"path": "a.py", "kind": "update"}], "status": "completed"}})
answer = "codex says: " + prompt.strip().splitlines()[-1] + (" (resumed)" if "resume" in args else "")
out({"type": "item.completed", "item": {"id": "m1", "type": "agent_message", "text": answer}})
out({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 30}})
open(last, "w").write(answer)
'''

CLAUDE_LOGIN = r'''
import json, os, sys

# "claude auth login" and "claude auth status --json", keeping the sign-in in its folder.
args = sys.argv[1:]
creds = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], ".credentials.json")
if args[:2] == ["auth", "status"]:
    signed = os.path.exists(creds)
    print(json.dumps({"loggedIn": signed, "authMethod": "claude.ai" if signed else "none",
                      **({"email": "me@example.com", "subscriptionType": "max"} if signed else {})}))
    sys.exit(0 if signed else 1)
if args[:2] == ["auth", "login"]:
    print("Opening the browser to sign in. If it didn't open, visit:", flush=True)
    url = "https://claude.ai/oauth/authorize?code=true&state=abc"
    sys.stderr.write("\x1b]8;;%s\x1b\\%s\x1b]8;;\x1b\\\n" % (url, url))  # a terminal hyperlink
    sys.stderr.flush()
    print("Paste code here if prompted >", flush=True)
    code = sys.stdin.readline().strip()
    if code != "good-code":
        print("Invalid code", flush=True)
        sys.exit(1)
    open(creds, "w").write("{}")
    print("Login successful.", flush=True)
'''

AGY = r'''
import json, os, sys, time

LOG = __file__ + ".log"
args = sys.argv[1:]
home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
plug = os.path.join(home, ".gemini", "config", "plugins", "pantheon")
settings_file = os.path.join(home, ".gemini", "antigravity-cli", "settings.json")
allowed = (json.load(open(settings_file)).get("permissions") or {}).get("allow", []) if os.path.exists(settings_file) else []
with open(LOG, "a") as f:
    f.write(json.dumps({"args": args, "home": home, "allow": allowed}) + "\n")
def out(obj):
    print(json.dumps(obj), flush=True)
def result(conv, status, response="", error="", denied=None):
    r = {"conversation_id": conv, "status": status, "response": response, "num_turns": 1,
         "usage": {"input_tokens": 50, "output_tokens": 6, "thinking_tokens": 3, "cache_read_tokens": 4, "total_tokens": 63}}
    if error:
        r["error"] = error
    if denied:
        r["denied_actions"] = denied
    out({"event": "result", "result": r})
conv = args[args.index("--conversation") + 1] if "--conversation" in args else "conv-1"
out({"event": "init", "conversation_id": conv, "init": {"cwd": os.getcwd(), "tools": ["run_command", "call_mcp_tool"]}})
if conv == "gone":
    result(conv, "ERROR", error="conversation gone not found")
    sys.exit(1)
n = 0
step = [0]
def s(**kw):
    step[0] += 1
    out({"event": "step_update", "step_update": {"conversation_id": conv, "step_index": step[0], **kw}})
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("event") != "user":
        continue
    text = msg["message"]["content"]
    n += 1
    s(state="DONE", step_type="user_input")
    if "LIMIT" in text:
        result(conv, "ERROR", error="RESOURCE_EXHAUSTED: You have exhausted your quota. It resets in 2h.")
        continue
    flags, denied = [], []
    if "RISKY" in text or "approved what you asked" in text:
        rule = "command(rm -rf build)"
        params = {"CommandLine": "rm -rf build", "Cwd": os.getcwd()}
        s(state="ACTIVE", step_type="tool", tool_name="run_command", tool_info={"name": "run_command", "parameters": params})
        if rule in allowed or "--dangerously-skip-permissions" in args:
            s(state="DONE", step_type="tool", tool_name="run_command", tool_info={"name": "run_command", "parameters": params, "output": "removed build"})
            flags.append("ran it")
        else:
            s(state="ERROR", step_type="tool", tool_name="run_command", tool_info={"name": "run_command", "parameters": params, "error": {"type": "TOOL_ERROR",
              "message": 'permission check failed for command "rm -rf build": user denied permission to run command:\nrm -rf build\nDo not attempt to circumvent this denial by rephrasing the command.'}})
            denied.append({"action": "command", "display_name": "RunCommand"})
            flags.append("was refused")
    if "did not allow" in text:
        flags.append("told no")
    if "SLOW" in text:
        time.sleep(4)
    s(state="DONE", step_type="tool", tool_name="call_mcp_tool", tool_info={"name": "call_mcp_tool", "parameters": {"ServerName": "pantheon_pantheon", "ToolName": "list_tasks"}, "output": "no tasks"})
    rules = os.path.join(plug, "rules", "AGENTS.md")
    if os.path.exists(rules) and "Working in Pantheon" in open(rules, encoding="utf-8").read():
        flags.append("knows-pantheon")
    mcp = os.path.join(plug, "mcp_config.json")
    if os.path.exists(mcp) and "pantheon" in json.load(open(mcp))["mcpServers"]:
        flags.append("has-bridge")
    if "mcp(pantheon_pantheon/*)" in allowed:
        flags.append("may-use-bridge")
    reply = "agy %d: %s [%s]" % (n, text.strip().splitlines()[-1][:60], ", ".join(flags))
    s(state="ACTIVE", step_type="agent_response", text_delta=reply[:5])
    s(state="DONE", step_type="agent_response", text_delta=reply[5:], usage={"input_tokens": 50, "output_tokens": 6})
    result(conv, "SUCCESS", reply + "\n", denied=denied)
'''

ACP = r'''
import json, sys, threading, queue

LOG = __file__ + ".log"
with open(LOG, "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
inbox = queue.Queue()
def reader():
    for line in sys.stdin:
        inbox.put(json.loads(line))
    inbox.put(None)
threading.Thread(target=reader, daemon=True).start()
def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()
def update(sid, u):
    out({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": u}})
import os
STORE = __file__ + ".sessions.json"  # like a real agent, its sessions outlive the process
sessions = json.load(open(STORE)) if os.path.exists(STORE) else {"old-1": ["history"]}
def save():
    json.dump(sessions, open(STORE, "w"))
nid = [100]
def ask(method, params):
    nid[0] += 1
    out({"jsonrpc": "2.0", "id": nid[0], "method": method, "params": params})
    while True:
        m = inbox.get()
        if m is None:
            sys.exit(0)
        if m.get("id") == nid[0] and "method" not in m:
            return m.get("result") or {}
        inbox.put(m)
while True:
    m = inbox.get()
    if m is None:
        break
    method, p, rid = m.get("method"), m.get("params") or {}, m.get("id")
    if method == "initialize":
        out({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": 1, "agentInfo": {"name": "fake-acp", "version": "1.0"},
             "agentCapabilities": {"loadSession": True, "sessionCapabilities": {"resume": {}}}}})
    elif method == "session/new":
        with open(LOG, "a") as f:
            f.write(json.dumps({"new": p["cwd"], "mcp": [s["name"] for s in p["mcpServers"]]}) + "\n")
        sessions["s-%d" % len(sessions)] = []
        save()
        out({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": "s-%d" % (len(sessions) - 1),
             "models": {"currentModelId": "a:small", "availableModels": [{"modelId": "a:small", "name": "Small"}, {"modelId": "a:big", "name": "Big"}]},
             "modes": {"currentModeId": "default", "availableModes": [{"id": "default"}, {"id": "accept_edits"}]}}})
    elif method == "session/resume":
        if p["sessionId"] in sessions:
            out({"jsonrpc": "2.0", "id": rid, "result": {}})
        else:
            out({"jsonrpc": "2.0", "id": rid, "error": {"code": -32002, "message": "session not found"}})
    elif method in ("session/set_model", "session/set_mode"):
        with open(LOG, "a") as f:
            f.write(json.dumps({method: p.get("modelId") or p.get("modeId")}) + "\n")
        out({"jsonrpc": "2.0", "id": rid, "result": {}})
    elif method == "session/prompt":
        sid = p["sessionId"]
        text = p["prompt"][0]["text"]
        sessions.setdefault(sid, []).append(text)
        save()
        update(sid, {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "pondering"}})
        update(sid, {"sessionUpdate": "plan", "entries": [{"content": "look", "status": "completed"}, {"content": "answer", "status": "in_progress"}]})
        update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Read notes.md", "kind": "read", "status": "pending", "rawInput": {"path": "notes.md"}})
        update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed", "content": [{"type": "content", "content": {"type": "text", "text": "the notes"}}]})
        verdict = ""
        if "RISKY" in text:
            update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t2", "title": "Run rm -rf build", "kind": "execute", "status": "pending", "rawInput": {"command": "rm -rf build"}})
            res = ask("session/request_permission", {"sessionId": sid, "toolCall": {"toolCallId": "t2", "title": "Run rm -rf build", "kind": "execute", "rawInput": {"command": "rm -rf build"}},
                      "options": [{"optionId": "yes", "name": "Allow", "kind": "allow_once"}, {"optionId": "no", "name": "Reject", "kind": "reject_once"}]})
            allowed = (res.get("outcome") or {}).get("optionId") == "yes"
            update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t2", "status": "completed" if allowed else "failed",
                         "content": [{"type": "content", "content": {"type": "text", "text": "removed" if allowed else "rejected"}}]})
            verdict = " ran it" if allowed else " was refused"
        if "LIMIT" in text:
            out({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "rate limit exceeded, try again in 30 minutes"}})
            continue
        knows = " knows-pantheon" if "Working in Pantheon" in text else ""
        reply = "acp %s turn %d: %s%s%s" % (sid, len(sessions[sid]), text.strip().splitlines()[-1][:50], verdict, knows)
        update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": reply}})
        out({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn", "usage": {"inputTokens": 40, "outputTokens": 9}}})
'''
