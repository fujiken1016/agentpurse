#!/usr/bin/env python3
"""Re-run live/live_claude.py unchanged, but stream the claude CLI output and timestamp every event (real clock) for the video."""
import json, os, subprocess, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "live"))
import live_claude as L
LOG = []  # (phase_tag, t_seconds_since_start, event)
T0 = [None]
def run_claude(binp, model, cfg, prompt, events, out_dir, tag, work_dir):
    cmd = [binp, "-p", prompt, "--model", model, "--mcp-config", cfg, "--strict-mcp-config",
           "--allowedTools", "mcp__agentpurse__*", "Read", "--output-format", "stream-json", "--verbose", "--no-session-persistence"]
    p = subprocess.Popen(cmd, cwd=work_dir, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True)
    if T0[0] is None: T0[0] = time.time()
    lines = []
    for l in p.stdout:
        try: e = json.loads(l)
        except ValueError: continue
        lines.append(e); LOG.append({"tag": tag, "t": round(time.time() - T0[0], 2), "e": e})
    p.wait()
    LOG.append({"tag": tag, "t": round(time.time() - T0[0], 2), "end": True})
    if p.returncode != 0: L.fail("claude exited %s" % p.returncode)
    events.extend(lines); return lines
L.run_claude = run_claude
out = sys.argv[1]; args = sys.argv[2:]
rc = L.main(args + ["--out", out])
json.dump(LOG, open(os.path.join(out, "timed.json"), "w"))
sys.exit(rc)
