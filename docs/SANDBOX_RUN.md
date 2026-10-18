# Sandbox run (PayPal sandbox), 2026-10-18 20:39:25 JST

1. OAuth token: OK (client-credentials grant against api-m.sandbox.paypal.com)
2. demo.py (sandbox) exit code 0; last lines:
```
== 5c. ...and the very same request again (no key) -> duplicate guard
   payment pay_2ca4048822: {
      "status": "DENIED",
      "reasons": [
         "duplicate of pay_b34477963d (same payee, amount, category, description within 600s); use payment_status instead of paying twice"
      ],
      "flags": []
   }

audit trail of #1: ["proposed", "decision:approve", "order_created"]

budget now: {"currency": "USD", "daily_cap": "100.00", "spent_24h": "12.00", "remaining_24h": "88.00", "per_payment_cap": "50.00", "auto_approve_up_to": "20.00", "max_payments_per_hour": 5, "payments_last_hour": 1, "pending_human": 2, "pending_ttl_seconds": 86400, "payees": {"acme-api": ["api", "data"], "docs-pub": ["subscription", "content"]}, "blocked_categories": ["gift_card", "crypto", "gambling", "adult"], "human_review_categories": ["subscription"], "paypal_mode": "sandbox"}
audit head (publish it to detect deleted newest rows): 13:8a2aacd5c805dcd6b7ddd49095002ec04c799c2de28189808011e0fe9e7ea435
ledger db: /var/folders/9z/r9zwvn3d43z1c8nbyw6q06k80000gn/T/tmp8ixq7u9r/demo.db   -> view it: python -m agentpurse.dashboard --db /var/folders/9z/r9zwvn3d43z1c8nbyw6q06k80000gn/T/tmp8ixq7u9r/demo.db   |  verify log: python -m agentpurse.audit --db /var/folders/9z/r9zwvn3d43z1c8nbyw6q06k80000gn/T/tmp8ixq7u9r/demo.db
```
3. Checkout already captured in an earlier run: order `4K665048T1086350S`, capture `2GH98453S0593001M` (CAPTURED)
4. Payouts API: already done in an earlier run (payout batch `M66X8A3C4HAFC`, PAID)
5. Audit: hash chain OK (18 rows), payments vs chain OK, head `18:eef58b017e50f38f...`

Findings from the first runs (INSUFFICIENT_FUNDS before the first capture, currency check, return page): see docs/SANDBOX_NOTES.md

**Result: all steps passed**
