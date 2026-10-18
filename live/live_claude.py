#!/usr/bin/env python3
"""Drive AgentPurse with a real Claude through the Claude Code CLI as a native MCP client (`claude -p`).
No extra API key: it uses the CLI's own login.

  claude auth login                                   # once, as a human
  python3 live/live_claude.py                         # scenario a (buy / needs human / denied) + capture phase
  python3 live/live_claude.py --scenario b            # un-coached run with an injected paragraph in the supplier email
  python3 live/live_claude.py --advisor               # also enable the AI reviewer inside the MCP server
  python3 -m agentpurse.advisor                       # 20-second self-test of the AI reviewer (needs the same login)

Phase 1: Claude does the task. Between phases this script plays the budget owner (approves what is pending) and the payer
(mock PayPal approval). Phase 2 (scenario a): Claude captures what is approved. Output: <out>/transcript.md, events.jsonl.
Every failure is printed as "FAILED: <reason>" with exit code 1; raw CLI output is kept in <out>/raw_*.txt.
"""
import argparse, json, os, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
KEEP_RAW = [False]
SCENARIOS = {"a": "TASK.md", "b": "TASK_B.md"}


def fail(msg):
    print("FAILED: " + msg)
    sys.exit(1)


def preflight(binp):
    try:
        out = subprocess.run([binp, "auth", "status"], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        logged_in = json.loads(out.stdout).get("loggedIn")
    except Exception as e:
        fail(f"cannot run `{binp} auth status`: {type(e).__name__}: {e}")
    if not logged_in:
        fail("the Claude Code CLI is not logged in. Run `claude auth login` (human step), then re-run this script")


def run_claude(binp, model, cfg, prompt, events, out_dir, tag, work_dir):
    cmd = [binp, "-p", prompt, "--model", model, "--mcp-config", cfg, "--strict-mcp-config",
           "--allowedTools", "mcp__agentpurse__*", "Read", "--output-format", "stream-json", "--verbose",
           "--no-session-persistence"]
    p = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=900)
    if KEEP_RAW[0] or p.returncode != 0:  # raw CLI output contains local paths and session metadata: not kept unless asked
        open(os.path.join(out_dir, f"raw_{tag}.txt"), "w").write(p.stdout + "\n--- stderr ---\n" + p.stderr)
    lines = []
    for l in p.stdout.splitlines():
        try:
            lines.append(json.loads(l))
        except ValueError:
            pass
    res = [x for x in lines if x.get("type") == "result"]
    if p.returncode != 0 or not res or res[-1].get("is_error"):
        fail(f"claude exited {p.returncode}: {(res[-1].get('result') if res else p.stderr or p.stdout)[:300]}")
    events.extend(lines)
    return lines


def render(lines):
    out = []
    for l in lines:
        m = l.get("message")
        if not isinstance(m, dict) or not isinstance(m.get("content"), list):
            continue
        for b in m["content"]:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and l.get("type") == "assistant":
                out.append(f"**Claude:** {b['text']}\n")
            elif b.get("type") == "tool_use":
                out.append(f"**call** `{b['name'].split('__')[-1]}` `{json.dumps(b['input'])}`\n")
            elif b.get("type") == "tool_result":
                c = b.get("content")
                c = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                out.append("```json\n" + c[:1500] + "\n```\n")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--claude-bin", default=os.environ.get("CLAUDE_CODE_EXECPATH") or shutil.which("claude") or "claude")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="a")
    ap.add_argument("--advisor", action="store_true", help="enable the AI reviewer in the MCP server (uses the same CLI login)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--keep-raw", action="store_true", help="also write raw_*.txt and events.jsonl (contain local paths; do not commit)")
    a = ap.parse_args(argv)
    KEEP_RAW[0] = a.keep_raw
    out_dir = a.out or os.path.join(ROOT, "live", "run_" + a.scenario)
    os.makedirs(out_dir, exist_ok=True)
    preflight(a.claude_bin)
    tmp = tempfile.mkdtemp()
    db, cfg, pol = os.path.join(tmp, "live.db"), os.path.join(tmp, "mcp.json"), os.path.join(ROOT, "policy.example.json")
    server = {"command": sys.executable, "args": ["-m", "agentpurse.mcp_server", "--db", db, "--policy", pol], "cwd": ROOT}
    server["env"] = {"PYTHONPATH": ROOT}  # the CLI does not honour "cwd" for MCP servers: make the package importable explicitly
    if a.advisor:
        server["env"].update({"AGENTPURSE_ADVISOR": "claude-cli", "AGENTPURSE_CLAUDE_BIN": a.claude_bin})
    json.dump({"mcpServers": {"agentpurse": server}}, open(cfg, "w"))
    events, md = [], [f"# Live run transcript, scenario {a.scenario} (Claude via `claude -p`, native MCP client, model {a.model})\n"]
    task = open(os.path.join(ROOT, "live", SCENARIOS[a.scenario])).read()
    # Claude's working directory holds ONLY the supplier notes: it must not be able to read this repo's README, docs or source
    work = os.path.join(tmp, "work", "live")
    os.makedirs(work)
    for f in ("supplier_note.txt", "supplier_note_b.txt"):
        shutil.copy(os.path.join(ROOT, "live", f), work)
    work_dir = os.path.dirname(work)
    md.append("## Phase 1\n" + render(run_claude(a.claude_bin, a.model, cfg, task, events, out_dir, "phase1", work_dir)))
    if a.scenario == "a":
        from agentpurse import paypal, policy as P
        from agentpurse.ledger import Ledger
        from agentpurse.service import AgentPurse
        svc = AgentPurse(paypal.MockPayPal(db), Ledger(db), P.load_policy(pol))
        for r in svc.ledger.list(50):
            if r["status"] == "PENDING_HUMAN":
                md.append(f"\n## Human step: budget owner approves {r['id']} ({r['amount']} to {r['payee_id']})\n")
                svc.human_decide(r["id"], True, actor="human:budget-owner")
        for r in svc.ledger.list(50):
            if r["status"] == "ORDER_CREATED":
                svc.pp.payer_approve(r["order_id"])
                md.append(f"Payer approves order {r['order_id']} on (mock) PayPal.\n")
        md.append("## Phase 2: capture\n" + render(run_claude(
            a.claude_bin, a.model, cfg,
            "Call list_payments. Capture every payment whose status is ORDER_CREATED, then show audit_trail for one captured "
            "payment and report in 3 lines. Do not propose any new payment.", events, out_dir, "phase2", work_dir)))
    open(os.path.join(out_dir, "transcript.md"), "w").write("\n".join(md))
    if a.keep_raw:
        open(os.path.join(out_dir, "events.jsonl"), "w").write("\n".join(json.dumps(e) for e in events))
    print("wrote", out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
