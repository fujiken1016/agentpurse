# Role models: what wins PayPal hackathons, and what we copied

Opened and read on 2026-10-18. Everything below is what the sources actually say; where a reason for winning is my
inference, it is marked **(inference)**.

## 1. The judging criteria we are optimising for
Source: Devpost event page https://paypalaihackathon.devpost.com/ . **The page itself returned HTTP 403 to my fetcher**, so the
criteria below come from a secondary summary (https://thegenaimagazine.com/paypal-ai-hackathon-2026-ideas-deadline-prizes)
and a search snippet quoting the Devpost judging section. Please eyeball the official page once before submitting.

Five equally weighted criteria: **Technological Implementation** (real, non-trivial use of PayPal *and* AI; "must work",
mockups do not count), **Design** (a complete product, not a proof of concept), **Potential Impact**, **Innovation**,
**Presentation** (video shows it running end to end). Requirements: public repo + OSS licence + setup steps, demo video
**under 3 minutes** on YouTube, written description. Deadline **2026-11-12 12:00 PT**. Prizes relevant to us: 1st $12,000;
**Best Use of Agentic Commerce $5,000**; Best Use of PayPal + AI $5,000; Best Demo Delivery $5,000. One main prize + one sponsor prize per project.

## 2. Past winners (PayPal Dev Days 2025 hackathon)
Source: https://developer.paypal.com/community/blog/winners-dev-days-hackathon/ (read 2026-10-18).

| Place | Team / project | What the post says | What we take from it |
|---|---|---|---|
| 1st | **CrawlerPal**: "a paywall AI agent assistant" built with PayPal APIs | Only that it "wowed the judges with their innovative project". No API list, no rationale. | The winner was **agentic + about who pays whom**, i.e. the same theme as ours. **(inference)** A concrete, named agent problem beat a generic integration. We state our problem in the first sentence of the README. |
| 2nd | **Codebusters**: Shopware 6 PayPal integration upgraded from JS SDK v5 to the v6 beta | Credits better performance and maintainability. | **(inference)** Judges reward using the *newest* PayPal surface correctly. We use Orders v2 with `payment_source.paypal` + `PAY_NOW`, `PayPal-Request-Id` idempotency, and show it in a table. |
| 3rd | **Rosen**: social trading app on PYUSD (README: https://github.com/GoROSEN/rosen-pyusd) | Wallet app; PayPal Developer demoed it on X as a finalist (https://twitter.com/paypaldev/status/1917997655214702623). | **(inference)** A working, demo-able product with a clear user beats architecture. We ship a dashboard a non-developer can use on a phone. |

Honest limit: this is one prior event and the post gives almost no judge commentary, so these rows are weak evidence for
*why* they won. The strongest transferable signal is the 2026 criteria text in section 1, which explicitly weights
"works end to end" and "complete product".

## 3. Same-event 2026 entries studied as README / product models (not winners; results are not out until ~2026-12-21)
* **Holdfast** https://github.com/faizanmohiuddin482/holdfast : strongest README structure I found. Tagline, hosted demo link,
  a numbered 4-step *judge click path with sample inputs*, a table mapping every money step to a PayPal endpoint, a
  guardrails list for the MCP tools, an honest "Limits" section. Its gap (by its own README): no screenshots/GIF/diagram,
  test coverage not described. **Copied**: judge click path, endpoint table, limits section. **Fixed on ours**: screenshot,
  a test table, a diagram.
* **spendwall** https://github.com/AndrewDongminYoo/spendwall : the closest competitor. A deterministic payment-policy firewall
  for agent refunds with spending reservations, bound human approval, audit trail, a four-scenario demo, and a README that
  says "no LLM in the policy decision path" and includes a table of where AI *is* used. **Copied**: the explicit
  "where is AI used" table, the four-scenario demo framing, audit trail as a first-class feature. **How we differ**: buyer-side
  agent spend (not support refunds), zero-dependency stdlib build anyone can run in 10 seconds, MCP-first, a hash-chained
  audit log, and a human-approval page that shows the exact text the agent read with the injected phrase highlighted.
  **Where they are ahead** (see RISKS.md): Postgres persistence, spend reservations, webhooks.

## 4. Pattern across the three sources (what we optimised)
1. Name the problem and the user in sentence one. 2. A judge can reproduce the demo in under a minute with no keys.
3. Every PayPal call is named and mapped. 4. The AI is used for something a judge can point at. 5. Limits are stated by us
before a judge finds them.
