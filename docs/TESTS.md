# Tests

Run: `python3 -m unittest discover -s tests` (Python 3.9+, no dependencies; CI runs 3.9 and 3.12).

| Suite | Tests |
|---|---|
| Policy rules: boundaries, hostile input (NaN, look-alike payees, zero-width tricks), policy-file validation | 26 |
| Service flows: duplicates, idempotency, human flow, expiry, cancel, audit chain | 32 |
| Dashboard security and approval flow | 8 |
| PayPal client against a fake transport (request shapes, token refresh, errors) | 10 |
| MCP protocol, tool schemas, real stdio subprocess | 12 |
| Cross-process race (8 real processes), CAPTURE_PENDING / ORDER_UNKNOWN, audit scope, capture re-check, AI reviewer outside the lock | 15 |
| Round-2 fixes (lost capture response, budget window, audit scope per column, atomic audit, lock timeout, thread safety, reviewer cache) | 18 |
| Live driver `live/live_claude.py` against a stub CLI | 4 |
| AI reviewer CLI wrapper (argv, isolation) | 2 |
| Payouts, reservation outside the lock, new language-neutral signals, sandbox script rehearsal | 19 |
| Spaced-out / multilingual injection phrases, false-positive guard | 3 |
| Original suite (wiring, ledger, demo flows) | 20 |
| Review-round-1 hardening (invisible Unicode, key reuse, cancel loops, compare-and-swap, payee) | 11 |
| AI reviewer can only escalate (fake advisor) | 9 |

