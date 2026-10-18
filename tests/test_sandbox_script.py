import os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "live", "sandbox_run.py")


def run(*a, env=None):
    return subprocess.run([sys.executable, SCRIPT, *a], capture_output=True, text=True, timeout=120, env={**os.environ, **(env or {})})


class SandboxScript(unittest.TestCase):
    def test_mock_rehearsal_passes_all_steps(self):
        out = os.path.join(tempfile.mkdtemp(), "r.md")
        p = run("--mock", "--out", out, "--skip-demo", "--wait-payer", "0")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(out) as fh:
            t = fh.read()
        for s in ("PAID", "CAPTURED", "hash chain OK", "all steps passed"):
            self.assertIn(s, t)

    def test_missing_keys_fail_with_a_clear_reason(self):
        p = run("--env", os.path.join(tempfile.mkdtemp(), "none.env"))
        self.assertEqual(p.returncode, 1)
        self.assertIn("FAILED: no keys yet", p.stdout)

    def test_incomplete_env_names_the_missing_variable(self):
        f = os.path.join(tempfile.mkdtemp(), "e.env")
        with open(f, "w") as fh:
            fh.write("PAYPAL_CLIENT_ID=abc\n")
        p = run("--env", f)
        self.assertEqual(p.returncode, 1)
        self.assertIn("PAYPAL_CLIENT_SECRET", p.stdout)
        self.assertNotIn("abc", p.stdout)

    def test_report_never_contains_secrets(self):
        from importlib.machinery import SourceFileLoader
        m = SourceFileLoader("sandbox_run", SCRIPT).load_module()
        r = m.Report(["s3cr3t-value"])
        with __import__("contextlib").redirect_stdout(__import__("io").StringIO()):
            r.add("token s3cr3t-value leaked?")
        self.assertNotIn("s3cr3t-value", "\n".join(r.lines))
        self.assertEqual(m.mask("alice@example.com"), "al***@example.com")


if __name__ == "__main__":
    unittest.main()
