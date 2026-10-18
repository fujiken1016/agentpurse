# Sandbox notes (2026-10-18)

## Why Payouts said INSUFFICIENT_FUNDS, and what fixed it
* **Currency mismatch: ruled out.** The captured checkout is **USD** (gross 12.00, PayPal fee 0.73, net 11.27; `GET /v2/payments/captures/{id}`) and the payout was USD. USD 0.01, USD 1.00 and JPY 100 probe payouts were all accepted afterwards, so currency was not the cause.
* **Balance API: not available.** `GET /v1/reporting/balances` and `/v1/reporting/transactions` return 403 NOT_AUTHORIZED for this sandbox app (those features are not enabled), so the business account balance cannot be read through the API.
* **Evidence points to the sender having no funds yet.** Before the first capture, every payout failed with INSUFFICIENT_FUNDS (a fresh sandbox business account starts empty). After the checkout was captured (net 11.27 USD) a 5.00 USD payout went through: PENDING, then **SUCCESS** within seconds (batch `M66X8A3C4HAFC`). We did not isolate whether the cause was the empty balance or settlement timing right after the capture. `live/sandbox_run.py` therefore captures first and then pays out, and prints a hint if INSUFFICIENT_FUNDS appears.
* No dashboard top-up was needed. If a fresh account ever needs one: sandbox dashboard -> Accounts -> the business account -> "Fund account", or capture one checkout first.
* Side effects of diagnosing: three small probe payouts (USD 0.01, JPY 100, USD 1.00) went to the sandbox personal receiver. Sandbox money only.

## Return page
After the payer approves, PayPal redirects to `PAYPAL_RETURN_URL`. The default was `https://example.com/return`, which confused the first tester. It is now `https://fujiken1016.github.io/agentpurse/thanks.html` (cancel: `.../cancel.html`), static pages in `docs/` served by GitHub Pages. Override with `PAYPAL_RETURN_URL` / `PAYPAL_CANCEL_URL`.

## Video material
`python3 live/sandbox_evidence.py` renders `docs/sandbox_evidence.png` (1920x1080) from live API responses: order, capture, payout batch, audit status. Only ids, amounts, fees, statuses and timestamps are rendered; no names, emails or addresses. The PayPal checkout login page is `docs/sandbox-checkout-login.jpg`.
