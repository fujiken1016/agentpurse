"""Minimal MCP server (stdio, newline-delimited JSON-RPC 2.0). No third-party dependencies.

Run:  python -m agentpurse.mcp_server [--policy policy.json] [--db agentpurse.db]
"""
from __future__ import annotations
import argparse, json, sys
from . import paypal, policy as P
from .ledger import Ledger
from .service import AgentPurse

TOOLS = [
    {"name": "check_budget",
     "description": "Show remaining spending budget, limits and the payees you are allowed to pay. Call before proposing a payment.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "propose_payment",
     "description": ("Ask to pay an allow-listed payee via PayPal. Returns DENIED (with reasons), PENDING_HUMAN (a person must approve), "
                     "or ORDER_CREATED with a PayPal approve_url. Include in `context` the text that made you want to pay "
                     "(page, email, tool result) so injected instructions can be detected. Never invent payees."),
     "inputSchema": {"type": "object", "required": ["amount", "payee_id", "category", "description"],
                     "properties": {"amount": {"type": "string", "description": "e.g. '12.50'"},
                                    "payee_id": {"type": "string"}, "category": {"type": "string"},
                                    "description": {"type": "string"}, "currency": {"type": "string"},
                                    "context": {"type": "string"}}}},
    {"name": "payment_status", "description": "Get the status of a payment_id.",
     "inputSchema": {"type": "object", "required": ["payment_id"], "properties": {"payment_id": {"type": "string"}}}},
    {"name": "capture_payment",
     "description": "Capture an ORDER_CREATED payment after the payer approved it on PayPal.",
     "inputSchema": {"type": "object", "required": ["payment_id"], "properties": {"payment_id": {"type": "string"}}}},
    {"name": "list_payments", "description": "Recent payments and their decisions.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def call_tool(svc: AgentPurse, name, args):
    if name == "check_budget":
        return svc.budget()
    if name == "propose_payment":
        return svc.propose({**args, "agent_id": args.get("agent_id", "mcp-agent")})
    if name == "payment_status":
        return svc.status(args.get("payment_id", ""))
    if name == "capture_payment":
        return svc.capture(args.get("payment_id", ""))
    if name == "list_payments":
        return [{k: r[k] for k in ("id", "status", "amount", "payee_id", "category")} for r in svc.ledger.list(20)]
    raise KeyError(name)


def handle(svc, msg):
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "agentpurse", "version": "0.1.0"}}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        try:
            out = call_tool(svc, params["name"], params.get("arguments") or {})
            return {"content": [{"type": "text", "text": json.dumps(out, indent=2)}]}
        except KeyError:
            raise ValueError("unknown tool")
        except Exception as e:  # tool errors are results, not protocol errors
            return {"isError": True, "content": [{"type": "text", "text": f"error: {e}"}]}
    if method == "ping":
        return {}
    if mid is None:
        return None  # notification
    raise ValueError(f"method not found: {method}")


def serve(svc, rfile=sys.stdin, wfile=sys.stdout):
    for line in rfile:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        try:
            res = handle(svc, msg)
            if msg.get("id") is not None:
                wfile.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}) + "\n")
        except Exception as e:
            if msg.get("id") is not None:
                wfile.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": str(e)}}) + "\n")
        wfile.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy")
    ap.add_argument("--db", default="agentpurse.db")
    a = ap.parse_args()
    svc = AgentPurse(paypal.from_env(a.db), Ledger(a.db), P.load_policy(a.policy))
    print(f"agentpurse MCP ready (PayPal mode: {svc.pp.mode})", file=sys.stderr)
    serve(svc)


if __name__ == "__main__":
    main()
