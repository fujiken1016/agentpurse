#!/usr/bin/env python3
"""Drive AgentPurse with a real Claude through the Claude Code CLI as an MCP client (`claude -p`), no extra API key.

  python3 live/live_claude.py [--claude-bin PATH] [--model sonnet] [--out live/run]

Phase 1: Claude does live/TASK.md (allowed purchase, human-approval purchase, forbidden purchase).
Between phases this script plays the human (approve the pending request) and the payer (mock PayPal approval).
Phase 2: Claude captures what is now approved. Writes <out>/transcript.md and <out>/events.jsonl.
Fails loudly ("FAILED: ...") if the CLI is not logged in.
"""
import argparse, json, os, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def run_claude(binp, model, cfg, prompt, events):
    cmd = [binp, "-p", prompt, "--model", model, "--mcp-config", cfg, "--strict-mcp-config",
           "--allowedTools", "mcp__agentpurse__*", "Read", "--output-format", "stream-json", "--verbose",
           "--no-session-persistence"]
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=900)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.startswith("{")]
    if p.returncode != 0 or not lines or any(l.get("is_error") for l in lines if l.get("type") == "result"):
        res = [l.get("result") for l in lines if l.get("type") == "result"]
        raise SystemExit(f"FAILED: claude exited {p.returncode}: {(res[-1] if res else p.stderr or p.stdout)[:300]}")
    events.extend(lines)
    return lines


def render(lines):
    out = []
    for l in lines:
        for b in (l.get("message", {}) or {}).get("content", []) if isinstance(l.get("message"), dict) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and l["type"] == "assistant":
                out.append(f"**Claude:** {b['text']}\n")
            elif b.get("type") == "tool_use":
                out.append(f"**call** `{b['name'].replace('mcp__agentpurse__', '')}` `{json.dumps(b['input'])}`\n")
            elif b.get("type") == "tool_result":
                c = b.get("content")
                c = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c)
                out.append("```json\n" + c[:1500] + "\n```\n")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claude-bin", default=os.environ.get("CLAUDE_CODE_EXECPATH") or shutil.which("claude") or "claude")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--out", default=os.path.join(ROOT, "live", "run"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "live.db")
    cfg = os.path.join(tmp, "mcp.json")
    json.dump({"mcpServers": {"agentpurse": {"command": sys.executable, "args": [
        "-m", "agentpurse.mcp_server", "--db", db, "--policy", os.path.join(ROOT, "policy.example.json")], "cwd": ROOT}}},
        open(cfg, "w"))
    events, md = [], ["# Live run transcript (Claude via `claude -p` MCP client)\n"]
    task = open(os.path.join(ROOT, "live", "TASK.md")).read()
    md.append("## Phase 1: shopping\n" + render(run_claude(a.claude_bin, a.model, cfg, task, events)))
    from agentpurse import paypal, policy as P
    from agentpurse.ledger import Ledger
    from agentpurse.service import AgentPurse
    svc = AgentPurse(paypal.MockPayPal(db), Ledger(db), P.load_policy(os.path.join(ROOT, "policy.example.json")))
    for r in svc.ledger.list(50):
        if r["status"] == "PENDING_HUMAN":
            md.append(f"\n## Human step: budget owner approves {r['id']} ({r['amount']} to {r['payee_id']})\n")
            res = svc.human_decide(r["id"], True, actor="human:budget-owner")
            if res.get("status") == "ORDER_CREATED":
                svc.pp.payer_approve(svc.ledger.get(r["id"])["order_id"])
                md.append("Payer approves the order on (mock) PayPal.\n")
    md.append("## Phase 2: capture\n" + render(run_claude(a.claude_bin, a.model, cfg,
        "Call list_payments. Capture every payment whose status is ORDER_CREATED, then show audit_trail for one captured "
        "payment and report in 3 lines. Do not propose any new payment.", events)))
    open(os.path.join(a.out, "transcript.md"), "w").write("\n".join(md))
    open(os.path.join(a.out, "events.jsonl"), "w").write("\n".join(json.dumps(e) for e in events))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
