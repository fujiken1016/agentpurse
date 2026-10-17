#!/usr/bin/env python3
"""One-shot MCP client for agents that can only run shell commands.
Starts the real AgentPurse MCP server over stdio, performs initialize + tools/call, prints the tool result, appends a
transcript line to $AP_LOG. State lives in the SQLite file, so separate calls see each other.

  python3 live/mcp_call.py check_budget
  python3 live/mcp_call.py propose_payment '{"amount":"12.00","payee_id":"acme-api",...}'
Env: AP_DB (sqlite path), AP_POLICY (policy json), AP_LOG (jsonl transcript).
"""
import json, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    name = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    cmd = [sys.executable, "-m", "agentpurse.mcp_server", "--db", os.environ["AP_DB"]]
    if os.environ.get("AP_POLICY"):
        cmd += ["--policy", os.environ["AP_POLICY"]]
    p = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

    def rpc(i, method, params):
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n")
        p.stdin.flush()
        return json.loads(p.stdout.readline())

    rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "mcp_call", "version": "1"}})
    res = rpc(2, "tools/call", {"name": name, "arguments": args})
    p.stdin.close()
    p.wait(timeout=10)
    out = res.get("result", res)
    text = out["content"][0]["text"] if "content" in out else json.dumps(out)
    if os.environ.get("AP_LOG"):
        with open(os.environ["AP_LOG"], "a") as f:
            f.write(json.dumps({"t": time.strftime("%H:%M:%S"), "tool": name, "args": args, "isError": bool(out.get("isError")),
                                "result": json.loads(text) if text.lstrip().startswith(("{", "[")) else text}) + "\n")
    print(text)


if __name__ == "__main__":
    main()
