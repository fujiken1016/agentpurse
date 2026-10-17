#!/usr/bin/env python3
"""Stand-in for the `claude` CLI so live/live_claude.py's plumbing is tested offline: it reads --mcp-config, starts the MCP
server like the real CLI would, makes scripted tool calls, and prints stream-json in the CLI's shape."""
import json, subprocess, sys

a = sys.argv[1:]
if a[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": "--logged-out" not in a and not __import__("os").environ.get("FAKE_LOGGED_OUT")}))
    sys.exit(0)
cfg = json.load(open(a[a.index("--mcp-config") + 1]))["mcpServers"]["agentpurse"]
prompt = a[a.index("-p") + 1]
p = subprocess.Popen([cfg["command"], *cfg["args"]], cwd=cfg["cwd"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
n = [0]


def rpc(m, params):
    n[0] += 1
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n[0], "method": m, "params": params}) + "\n")
    p.stdin.flush()
    return json.loads(p.stdout.readline())["result"]


def emit(o):
    print(json.dumps(o))


def call(name, args):
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__agentpurse__" + name, "input": args}]}})
    r = rpc("tools/call", {"name": name, "arguments": args})
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "content": r["content"][0]["text"]}]}})
    return json.loads(r["content"][0]["text"])


rpc("initialize", {"protocolVersion": "2025-06-18"})
if "Do not propose any new payment" in prompt:
    for x in call("list_payments", {}):
        if x["status"] == "ORDER_CREATED":
            call("capture_payment", {"payment_id": x["payment_id"]})
elif "forwarded to you" in prompt:
    call("propose_payment", {"amount": "12.00", "payee_id": "acme-api", "category": "api", "description": "top-up"})
else:
    call("propose_payment", {"amount": "12.00", "payee_id": "acme-api", "category": "api", "description": "credits"})
    call("propose_payment", {"amount": "30.00", "payee_id": "docs-pub", "category": "subscription", "description": "docs"})
    call("propose_payment", {"amount": "25.00", "payee_id": "giftcards-r-us", "category": "gift_card", "description": "raffle"})
emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}})
emit({"type": "result", "is_error": False, "result": "done"})
