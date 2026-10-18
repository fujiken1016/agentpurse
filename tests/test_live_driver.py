import json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE = os.path.join(ROOT, "tests", "fake_claude.py")


def run(*extra, env=None):
    d = tempfile.mkdtemp()
    os.chmod(FAKE, 0o755)
    p = subprocess.run([sys.executable, os.path.join(ROOT, "live", "live_claude.py"), "--claude-bin", FAKE, "--out", d, *extra],
                       capture_output=True, text=True, env={**os.environ, **(env or {})}, timeout=120)
    return p, d


class LiveDriver(unittest.TestCase):
    """live/live_claude.py against a stub CLI: proves the orchestration works the moment a real login exists."""

    def test_scenario_a_runs_end_to_end(self):
        p, d = run("--keep-raw")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(os.path.join(d, "transcript.md")) as f:
            t = f.read()
        for s in ("ORDER_CREATED", "PENDING_HUMAN", "DENIED", "Human step", "Payer approves", "CAPTURED"):
            self.assertIn(s, t)
        with open(os.path.join(d, "events.jsonl")) as f:
            self.assertGreater(len(f.read().splitlines()), 8)

    def test_scenario_b(self):
        p, d = run("--scenario", "b")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(os.path.join(d, "transcript.md")) as f:
            self.assertIn("ORDER_CREATED", f.read())

    def test_not_logged_in_fails_clearly(self):
        p, d = run(env={"FAKE_LOGGED_OUT": "1"})
        self.assertEqual(p.returncode, 1)
        self.assertIn("FAILED: the Claude Code CLI is not logged in", p.stdout)

    def test_advisor_flag_reaches_the_mcp_config(self):
        p, d = run("--advisor")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
