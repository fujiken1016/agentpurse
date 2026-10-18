# Review round 3 (2026-10-18, harsh re-score, nothing implemented)

Method: read README, SUBMISSION, LIVE_RUN, RISKS, R1/R2, ROLE_MODELS; ran `python3 -m unittest discover -s tests` (167 tests, OK, 11 s, 6 ResourceWarnings); ran `demo.py` on a fresh ledger (works, 9 scenarios);
probed the policy by hand (currency, case/space look-alike payees, fullwidth and spaced-out injection phrases); extracted frames of `video/agentpurse_demo.mp4` (2:01.8, 1080p30, AAC mono, mean -17 dB, peak -1.5 dB) at 6 s intervals plus full-size frames; checked git refs and remote.
Devpost page itself was not re-opened (403 earlier, see ROLE_MODELS): criteria below are the five equal-weight ones already recorded.

## Scores (0-10, harsh)
| Viewpoint | Score | One-line reason |
|---|---|---|
| PayPal DevRel judge | 5.5 | Zero PayPal pixels in the video, sandbox never called, and the "who gets paid" semantics of Orders v2 do not fit a buyer-side agent |
| Payment-risk judge | 7.5 | Policy, caps, velocity, duplicate guard, capture-unknown handling are real and unusually thorough; heuristic scan and category self-declared by the agent |
| Agent-design judge | 7.5 | Dry-run tool, model-readable denials, escalate-only reviewer, two real-Claude runs; but the agent never pays without a human on PayPal, so "agentic" is thin |
| Security engineer | 7.5 | Fail-closed, flock race tested, row-hash audit, dashboard CSRF/rebinding; PII in `context`, no approver identity, lock held over HTTP |
| English technical writer | 7.0 | README is strong and honest, but too long before the first run, repeats caveats, and three docs contradict each other (see Important 3) |
| Video director | 6.5 | Clear structure and captions, correct honesty badges; no human voice, no PayPal UI, one long mock-mode middle, TTS voice, no hook beyond a diagram-heavy first 20 s |
| **Overall** | **6.9** | Top-5 caliber if the fatal items are fixed, 1st place only with a live sandbox run and a payee story that holds |

Round-2 deltas verified: F1-F3 fixed and tested; reviewer and native `claude -p` runs now exist (R2's "never executed" is stale). F4 (Gmail in history) is **resolved on the remote**: `origin/main` and `refs/pull/1/head` carry only noreply authors; `fa3112b` survives only in the local `refs/original/` backup. RISKS.md still says it is open (stale, see Important 3).

## FATAL (fix or lose 1st place)
| # | Finding | Fix (specific) |
|---|---|---|
| F1 | **The PayPal half has never run.** README, SUBMISSION, video end card and every video label say mock. Criterion 1 says "must work, mockups do not count". A DevRel judge discounts everything above the PayPal line. | Create a free sandbox REST app (developer.paypal.com, owner action: login only, no card) and run `PAYPAL_CLIENT_ID/SECRET python3 demo.py` once. Fix the "small shape fixes" the README predicts. Re-record scenes 3, 7 with the real sandbox checkout page and the sandbox order id on screen. Replace "Not yet exercised" in README/SUBMISSION with the real order id and capture id. Budget: one evening. |
| F2 | **Payee semantics are wrong for the pitch.** With plain Orders v2 the order pays the merchant that owns the credentials (README Limits, first bullet). So "agent buys from acme-api" opens a checkout that pays *you*. The allow-list is then enforced by AgentPurse only, and a judge who knows Orders will see the product as policy theatre around the wrong API. | Make the money path honest, pick one: (a) add the **Payouts API** (`POST /v1/payments/payouts`, sandbox) as the way to pay allow-listed payees by `paypal_email`, with the same policy/human/audit gates, and demo it; or (b) reframe the story as "agent-initiated checkout the payer approves" and rename allow-list to "approved checkout destinations" without implying a third party gets paid. (a) is stronger and matches "Agentic Commerce". Say it in the first screen of the README, not in Limits. |
| F3 | **Submission is not actually submittable yet.** `SUBMISSION.md` still has `Video: <YouTube URL>`; the mp4 is untracked in git; `video/` holds 362 MB of frames, 6 MB wav and `node_modules` with no `.gitignore` entry (one `git add .` poisons the repo); the video was never checked from a logged-out window. | Upload (Public, not Unlisted), paste URL in SUBMISSION and README, add `video/frames/ video/work/ video/probe/ video/aud/ video/node_modules/ video/*.wav` to `.gitignore`, commit only `video/` sources plus a 1 MB poster. Verify the Devpost criteria text on the real page before 2026-11-12 (deadline 2026-11-13 05:00 JST = 11-12 12:00 PT; keep a 24 h buffer). |

## IMPORTANT
1. **"Agentic" is limited by per-order payer approval.** Auto-approve only opens the checkout. Add one flow where no human click is needed and still safe: e.g. Payouts to an allow-listed payee under 20.00 (see F2a), or a billing-agreement/vault reference. Otherwise judges read it as an approval inbox.
2. **Video**: (a) no PayPal screen appears at all; show the sandbox checkout page in scene 7 (3 s is enough). (b) Add a human voice or at least a credited, natural TTS; muted-safe captions already work. (c) First 20 s are hook + diagram; cut the architecture slide to 6 s and get to a real-Claude call by 0:20. (d) Scene `a1` terminal is dense (about 18 lines); highlight the one line that matters (`status ORDER_CREATED`) with a box. (e) The attacker scene is clearly labelled SCRIPTED, keep that. (f) Add the tagline sentence from README on the first frame so a muted judge gets it. Duration 2:02 is fine (limit 3:00); you have 50 s spare for the real sandbox run.
3. **Docs contradict each other**: RISKS R28 and the "Hygiene" paragraph say the Gmail commit is open (remote is clean); REVIEW_R2 "third review should look at" lists things now done; SUBMISSION says "(not yet run against sandbox)" in two places and the README table says 167 tests in three layouts (counts do add up to 167: checked). Do one pass so each fact lives in one file. Also say in RISKS that GitHub may still resolve `fa3112b` by SHA via the old PR and that a support request to purge is optional.
4. **README length and order.** The first screen is good. The threat table is the best artefact; move "Where AI is used" next to it and push the 12-row test table and PayPal-APIs table below the fold or into docs. A judge decides in 90 s; the 30-second command block should be the first thing after the tagline (it is) and a one-line GIF of the dashboard approval should sit beside the 375 px screenshot.
5. **Heuristic injection scan** (probed): catches "disregard prior directions", fullwidth, "admin mode"; misses spaced-out "i g n o r e", Spanish "ignora las instrucciones anteriores", and business-email-compromise phrasing ("vendor changed bank details, send to new payee"). You say it is a tripwire; add a second, cheap, language-neutral signal that does not rely on phrases: flag any `context` that names a payee id or amount different from the request (mismatch check), and flag non-ASCII-script mixing. Add these to tests as "known misses" so the claim stays measured.
6. **Category is agent-supplied.** An agent can declare `api` for anything sold by acme-api. State it, and add per-payee `max_amount` examples to the README table (they exist in `policy.example.json`).
7. **Compliance gaps already admitted but cheap to close**: record `approver` from the dashboard login/OS user instead of constant `human:dashboard`; add `context` redaction/retention days to the policy; publish the audit head (`--expect-head`) by printing it to the demo's last line so the video can show it.
8. **Lock held across PayPal HTTP** (60 s worst case): at minimum reserve spend (insert row in `RESERVED`) under the lock, release, call PayPal, then finalize. One sentence in README is fine for the hackathon, but one commit with a test would turn the biggest "single node" criticism into a design note.
9. **Competition**: spendwall has Postgres, reservations and webhooks. The differentiators to push in the first 100 words: buyer-side, MCP-first, evidence on the approval page, real-Claude logs, zero-dependency 30-second run. Add a one-row comparison table (AgentPurse / PayPal Agent Toolkit / spendwall) in the README; the PayPal toolkit sentence already says "check docs"; do that check and cite the date.
10. **Test hygiene**: a passing test prints `FAILED: no such database ...` to stdout (confusing to a judge who runs the suite); silence it with `contextlib.redirect_stdout`. Fix the 6 ResourceWarnings (unclosed connections in live-driver tests). Add a tiny CI workflow (`.github/workflows/test.yml`, Python 3.9 and 3.12) so the repo shows a green badge.

## POLISH
* Tagline "cannot ... be tricked past" overclaims next to the heuristic in Limits; use "cannot exceed, and is hard to trick past" or keep and link to the threat table.
* Say "Sonnet" vs a model id consistently (video says Sonnet; docs say one model).
* `python3 -m unittest -s tests` note in README for `tests.x` style invocations.
* Put the 375 px screenshot's alt text next to a one-sentence caption of the injected phrase.
* Devpost "Built with" add: `python`, `mcp`, `paypal-orders-v2` (+ `paypal-payouts` if F2a).
* Windows: thread-lock only, mention once in Limits (already) and drop "Python 3.9+" claim for Windows if untested.
* Video thumbnail: the red DENIED stamp frame (0:07) is the strongest still; use it.

## Gap to 1st place, ranked by points per hour
1. F1 sandbox run + real PayPal page in the video (+1.0 overall, biggest swing in DevRel and Technological Implementation).
2. F2 Payouts path or honest reframing (+0.7, removes the one question a DevRel judge will ask in 30 s).
3. F3 + Important 3 (+0.3, zero-risk hygiene; avoids disqualification-type mistakes).
4. Important 1/2 (agentic without human click; video re-cut) (+0.4).
5. Important 5, 7, 8 (+0.3 for security/compliance reviewers).
Projected after 1-4: about 8.2. Past PayPal winners (CrawlerPal, ROLE_MODELS) won on a concrete agent-money problem plus a working PayPal integration; the second half is exactly what is missing.

## Status after implementation (2026-10-18)
F2 Payouts path: done (offline; sandbox pending). F3: `video/` bulky files ignored, no video committed, Gmail finding resolved by history rewrite (RISKS updated). F1: `live/sandbox_run.py` + setup steps ready, waiting for keys in `~/.config/fujiken/paypal_sandbox.env`.
Important: 1 (Payouts), 3 (docs pass), 4 (README order, tests moved to docs/TESTS.md), 5 (mismatch/mixed-script signals + measured known misses), 6 (stated), 7 (approver, head hash printed by demo), 8 (propose no longer holds the lock during the PayPal call), 9 (comparison table, toolkit read 2026-10-18), 10 (quiet tests, CI workflow) done. Important 2 (video re-cut) is the video session's job and depends on the sandbox clip.
