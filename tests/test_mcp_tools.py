"""MCP surface: tool definitions are well-formed, errors are the right kind, a real subprocess speaks the protocol."""
import io, json, os, subprocess, sys, tempfile, unittest
from agentpurse import mcp_server as M
from helpers import req, make

ROOT = os.path.join(os.path.dirname(__file__), "..")


def run(svc, *msgs):
    out = io.StringIO()
    M.serve(svc, io.StringIO("\n".join(m if isinstance(m, str) else json.dumps(m) for m in msgs) + "\n"), out)
    return [json.loads(l) for l in out.getvalue().splitlines()]


def call(name, args, i=1):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}


class Definitions(unittest.TestCase):
    def test_every_tool_is_well_formed(self):
        names = set()
        for t in M.TOOLS:
            names.add(t["name"])
            self.assertRegex(t["name"], r"^[a-z_]+$")
            self.assertGreater(len(t["description"]), 30, t["name"])
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertIn("readOnlyHint", t["annotations"])
            for r in t["inputSchema"].get("required", []):
                self.assertIn(r, t["inputSchema"]["properties"])
            for k, v in t["inputSchema"]["properties"].items():
                self.assertIn("type", v, f"{t['name']}.{k}")
        self.assertEqual(len(names), len(M.TOOLS))

    def test_read_only_tools_do_not_write(self):
        svc, pp, t = make()
        for name, args in (("check_budget", {}), ("simulate_payment", req()), ("list_payments", {})):
            self.assertTrue(M._BY_NAME[name]["annotations"]["readOnlyHint"])
            M.call_tool(svc, name, args)
        self.assertEqual(svc.ledger.list(), [])

    def test_capture_is_marked_destructive_non_idempotent(self):
        a = M._BY_NAME["capture_payment"]["annotations"]
        self.assertTrue(a["destructiveHint"]); self.assertFalse(a["idempotentHint"])


class Protocol(unittest.TestCase):
    def setUp(self):
        self.svc, self.pp, self.t = make()

    def test_initialize_gives_instructions_and_negotiates_version(self):
        r = run(self.svc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
                {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
        self.assertEqual(r[0]["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(r[1]["result"]["protocolVersion"], M.PROTOCOLS[0])
        self.assertIn("DENIED result is final", r[0]["result"]["instructions"])

    def test_garbage_line_gets_parse_error_and_server_keeps_going(self):
        r = run(self.svc, "{not json", {"jsonrpc": "2.0", "id": 5, "method": "ping"}, "[]", "42")
        self.assertEqual(r[0]["error"]["code"], -32700)
        self.assertEqual(r[1]["result"], {})
        self.assertEqual(len(r), 2)  # the two valid-JSON non-requests get no reply

    def test_unknown_method_and_unknown_tool_codes(self):
        r = run(self.svc, {"jsonrpc": "2.0", "id": 1, "method": "nope"}, call("steal_money", {}, 2))
        self.assertEqual(r[0]["error"]["code"], -32601)
        self.assertEqual(r[1]["error"]["code"], -32602)

    def test_bad_arguments_are_tool_errors_the_model_can_read(self):
        r = run(self.svc, call("propose_payment", {"amount": "1.00"}, 1),
                call("propose_payment", dict(req(), bogus=1), 2),
                call("payment_status", {"payment_id": 7}, 3),
                call("propose_payment", "oops", 4))
        for x in r:
            self.assertTrue(x["result"]["isError"], x)
        self.assertIn("missing required", r[0]["result"]["content"][0]["text"])
        self.assertIn("unknown argument", r[1]["result"]["content"][0]["text"])
        self.assertEqual(self.svc.ledger.list(), [])

    def test_numeric_amount_is_accepted(self):
        r = run(self.svc, call("propose_payment", dict(req(), amount=12.5)))
        self.assertEqual(r[0]["result"]["structuredContent"]["status"], "ORDER_CREATED")

    def test_structured_and_text_content_agree(self):
        r = run(self.svc, call("propose_payment", req()))[0]["result"]
        self.assertEqual(json.loads(r["content"][0]["text"]), r["structuredContent"])

    def test_list_results_are_wrapped_for_structured_content(self):
        r = run(self.svc, call("list_payments", {}))[0]["result"]
        self.assertEqual(r["structuredContent"], {"result": []})

    def test_full_agent_session_with_audit_trail(self):
        r = run(self.svc,
                call("check_budget", {}, 1),
                call("simulate_payment", req(amount="30.00"), 2),
                call("propose_payment", req(amount="30.00", idempotency_key="job-7"), 3),
                call("propose_payment", req(amount="30.00", idempotency_key="job-7"), 4))
        sim, p1, p2 = (x["result"]["structuredContent"] for x in r[1:])
        self.assertEqual(sim["verdict"], "needs_human")
        self.assertEqual(p1["payment_id"], p2["payment_id"])
        self.assertEqual(p1["status"], "PENDING_HUMAN")
        t = run(self.svc, call("audit_trail", {"payment_id": p1["payment_id"]}))[0]["result"]["structuredContent"]
        self.assertTrue(t["chain_ok"])
        self.assertEqual(t["events"][0]["event"], "decision:needs_human")
        c = run(self.svc, call("cancel_payment", {"payment_id": p1["payment_id"], "reason": "no longer needed"}))
        self.assertEqual(c[0]["result"]["structuredContent"]["status"], "CANCELLED")


class RealProcess(unittest.TestCase):
    def test_stdio_subprocess_end_to_end(self):
        db = os.path.join(tempfile.mkdtemp(), "e2e.db")
        env = {k: v for k, v in os.environ.items() if not k.startswith("PAYPAL_")}
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                call("propose_payment", req(), 2), call("propose_payment", req(payee_id="evil"), 3)]
        p = subprocess.run([sys.executable, "-m", "agentpurse.mcp_server", "--db", db], input="\n".join(map(json.dumps, msgs)) + "\n",
                           capture_output=True, text=True, cwd=ROOT, env=env, timeout=30)
        rs = [json.loads(l) for l in p.stdout.splitlines()]
        self.assertEqual([r["id"] for r in rs], [1, 2, 3])
        self.assertEqual(rs[1]["result"]["structuredContent"]["status"], "ORDER_CREATED")
        self.assertEqual(rs[2]["result"]["structuredContent"]["status"], "DENIED")
        self.assertIn("PayPal mode: mock", p.stderr)
        a = subprocess.run([sys.executable, "-m", "agentpurse.audit", "--db", db], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(a.returncode, 0)
        self.assertIn("hash chain: OK", a.stdout)


if __name__ == "__main__":
    unittest.main()
