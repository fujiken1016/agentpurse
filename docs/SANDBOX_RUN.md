# Sandbox run (PayPal sandbox), 2026-10-18 20:32:47 JST

1. OAuth token: OK (client-credentials grant against api-m.sandbox.paypal.com)
3. Checkout order for 12.00: status **ORDER_CREATED**, PayPal order `4K665048T1086350S`
   Payer approval page (open as the sandbox buyer): https://www.sandbox.paypal.com/checkoutnow?token=4K665048T1086350S
   NOT APPROVED YET (recorded as incomplete, not as a pass). Human step: (1) open the URL above in a browser and log in with the sandbox BUYER (personal) account, (2) press Pay/Approve, (3) run `python3 live/sandbox_run.py --skip-demo` again: it resumes this order, captures it, then does the payout.
4. Payouts API, agent pays sb***@personal.example.com with no human click: status **FAILED**
**FAILED: payout did not go through: 422 {"name":"INSUFFICIENT_FUNDS","message":"Sender does not have sufficient funds. Please add funds and retry.","debug_id":"ca4c73986ef3f","information_link":"https://developer.paypal.com/docs/api/payments.payouts-batch/#errors","links":[]}. The sandbox business account has no balance yet: capture the checkout first (step 3) or add funds in the sandbox dashboard.**
5. Audit: hash chain OK (6 rows), payments vs chain OK, head `6:b70b2d7a41277a1d...`

**Result: INCOMPLETE / FAILED, see lines marked FAILED or 'not approved yet'**

## What this run proved, and what is still open (2026-10-18)
* **Proved against the real PayPal sandbox:** OAuth token; the whole `demo.py` flow (create order with `PAY_NOW`, PayPal returned a real order id and the `payer-action` link, idempotent replay, duplicate guard, audit chain) with no shape mismatch; `GET order`; the Checkout order `4K665048T1086350S` opens PayPal's real checkout page (screen still: `docs/sandbox-checkout-login.jpg`, the login page the buyer sees).
* **Payouts reached the real API** and PayPal answered `INSUFFICIENT_FUNDS` (HTTP 422): the request shape was accepted, the sandbox business account simply has no balance. A captured checkout puts money in that account, so the script now captures first, then pays out.
* **Open, needs one human action (3 lines):** (1) open the approval URL above in a browser and log in with the sandbox **buyer** (personal) account, (2) press Pay, (3) run `python3 live/sandbox_run.py --skip-demo`: it resumes this order, captures it, then sends the payout and rewrites this file. The run is recorded as INCOMPLETE until then.
