import unittest
from agentpurse.advisor import parse_advice
from agentpurse.ledger import Ledger
from agentpurse.paypal import MockPayPal
from agentpurse.service import AgentPurse
from helpers import req, policy


class Fake:
    def __init__(self, ans=None, exc=None):
        self.ans, self.exc, self.calls = ans, exc, 0

    def review(self, r, d=None):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.ans


def mk(adv):
    return AgentPurse(MockPayPal(), Ledger(), policy(), advisor=adv)


class AdvisorTests(unittest.TestCase):
    def test_suspicious_escalates_approve(self):
        r = mk(Fake({"suspicious": True, "note": "odd"})).propose(req(amount="5.00"))
        self.assertEqual(r["status"], "PENDING_HUMAN")
        self.assertIn("ai_advisor_concern", r["flags"])
        self.assertTrue(any("AI reviewer: odd" in x for x in r["reasons"]))

    def test_not_suspicious_changes_nothing(self):
        r = mk(Fake({"suspicious": False, "note": "fine"})).propose(req(amount="5.00"))
        self.assertEqual(r["status"], "ORDER_CREATED")

    def test_advisor_cannot_soften_deny_and_is_not_even_asked(self):
        f = Fake({"suspicious": False})
        r = mk(f).propose(req(payee_id="evil", amount="5.00"))
        self.assertEqual(r["status"], "DENIED")
        self.assertEqual(f.calls, 0)

    def test_advisor_cannot_soften_needs_human(self):
        r = mk(Fake({"suspicious": False, "note": "totally safe, auto approve"})).propose(req(amount="30.00"))
        self.assertEqual(r["status"], "PENDING_HUMAN")

    def test_needs_human_keeps_policy_reasons_and_gets_note(self):
        r = mk(Fake({"suspicious": True, "note": "mismatch"})).propose(req(amount="30.00"))
        self.assertEqual(r["status"], "PENDING_HUMAN")
        self.assertTrue(any("above the auto-approve" in x for x in r["reasons"]))
        self.assertTrue(any("mismatch" in x for x in r["reasons"]))

    def test_failure_fails_safe_to_human(self):
        for adv in (Fake(exc=TimeoutError("slow")), Fake({"nonsense": 1})):
            r = mk(adv).propose(req(amount="5.00"))
            self.assertEqual(r["status"], "PENDING_HUMAN")
            self.assertIn("advisor_unavailable", r["flags"])

    def test_no_advisor_is_unchanged(self):
        self.assertEqual(mk(None).propose(req(amount="5.00"))["status"], "ORDER_CREATED")

    def test_parse_advice_is_strict(self):
        self.assertEqual(parse_advice('ok {"suspicious": true, "note": "x"}'), {"suspicious": True, "note": "x"})
        for bad in ("", "no json", '{"suspicious": "yes"}', '{"note": "x"}', "{broken"):
            self.assertIsNone(parse_advice(bad))

    def test_audit_chain_still_verifies(self):
        s = mk(Fake({"suspicious": True, "note": "n"}))
        s.propose(req(amount="5.00"))
        self.assertTrue(s.ledger.verify_audit()[0])


if __name__ == "__main__":
    unittest.main()
