#!/usr/bin/env python3
"""Build video-ready 'transaction record' screens from REAL PayPal sandbox API responses (no screenshots of anyone's account).
Reads the ledger saved by live/sandbox_run.py, asks the sandbox for the order, capture and payout batch, and renders
docs/sandbox_evidence.html (+ .png when Chrome is available). Names, emails, addresses are never rendered: only ids,
amounts, fees, statuses and timestamps. Usage: python3 live/sandbox_evidence.py"""
import html, json, os, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from live.sandbox_run import load_env, DEFAULT_ENV  # noqa: E402
from agentpurse import paypal  # noqa: E402
from agentpurse.ledger import Ledger  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def main():
    claude_payout = sys.argv[sys.argv.index("--claude-payout") + 1] if "--claude-payout" in sys.argv else None
    env = load_env(DEFAULT_ENV)
    os.environ.update({k: v for k, v in env.items() if k.startswith("PAYPAL_")})
    pp = paypal.from_env()
    if pp.mode != "sandbox":
        sys.exit("FAILED: no sandbox keys")
    led = Ledger(os.path.expanduser("~/.cache/agentpurse/sandbox.db"))
    rows = led.list(100)
    cap = next((r for r in rows if r["status"] == "CAPTURED"), None)
    pay = next((r for r in rows if r["status"] in ("PAID", "PAYOUT_PENDING")), None)
    if not cap or not pay:
        sys.exit("FAILED: the saved ledger has no captured checkout and a payout yet; run live/sandbox_run.py first")
    c = pp._call("GET", f"/v2/payments/captures/{cap['capture_id']}")
    o = pp._call("GET", f"/v2/checkout/orders/{cap['order_id']}")
    b = pp._call("GET", f"/v1/payments/payouts/{pay['order_id']}")
    bh, it = b.get("batch_header", {}), (b.get("items") or [{}])[0]
    br = c.get("seller_receivable_breakdown", {})
    cb = ci = {}
    if claude_payout:
        cr = pp._call("GET", f"/v1/payments/payouts/{claude_payout}")
        cb, ci = cr.get("batch_header", {}), (cr.get("items") or [{}])[0]
    esc = html.escape

    def money(m):
        return f"{m['value']} {m.get('currency_code') or m.get('currency')}" if m else "-"
    seq, h = led.head_hash()
    cards = [
        ("Checkout order (payer approved on PayPal)", [("Order id", o["id"]), ("Status", o["status"]), ("Intent", o.get("intent")),
            ("Amount", money(o["purchase_units"][0]["amount"])), ("Our payment id (custom_id)", c.get("custom_id"))]),
        ("Capture (money moved, once)", [("Capture id", c["id"]), ("Status", c["status"]), ("Gross", money(br.get("gross_amount"))),
            ("PayPal fee", money(br.get("paypal_fee"))), ("Net", money(br.get("net_amount"))), ("Created", c.get("create_time"))]),
        ("Payout (agent paid an allow-listed account, no click)", [("Batch id", bh.get("payout_batch_id")), ("Batch status", bh.get("batch_status")),
            ("Item status", it.get("transaction_status")), ("Amount", money(it.get("payout_item", {}).get("amount"))),
            ("Sender batch id (= our payment id)", bh.get("sender_batch_header", {}).get("sender_batch_id"))]),
        *([("Payout made by Claude itself (claude -p over MCP, 2.00 USD)", [("Batch id", cb.get("payout_batch_id")), ("Batch status", cb.get("batch_status")),
            ("Item status", ci.get("transaction_status")), ("Amount", money(ci.get("payout_item", {}).get("amount")))])] if claude_payout else []),
        ("AgentPurse audit", [("Hash chain", "OK" if led.verify_audit()[0] else "BROKEN"), ("Payments vs chain", "OK" if not led.verify_ledger() else "MISMATCH"),
            ("Head", f"{seq}:{h[:24]}...")]),
    ]
    body = "".join(f"<section><h2>{esc(t)}</h2><dl>" + "".join(f"<dt>{esc(k)}</dt><dd>{esc(str(v))}</dd>" for k, v in rs) + "</dl></section>" for t, rs in cards)
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PayPal sandbox record</title>
<style>:root{{--bg:#f4f6f9;--fg:#14171c;--mut:#5a6270;--card:#fff;--line:#d9dde3;--acc:#003087}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1319;--fg:#eef0f3;--mut:#a3abb8;--card:#19202a;--line:#2c323c;--acc:#6ea8ff}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:22px/1.5 system-ui,sans-serif;padding:36px}}h1{{margin:0 0 4px;font-size:30px;color:var(--acc)}}.s{{color:var(--mut);margin:0 0 22px;font-size:18px}}
.g{{display:grid;grid-template-columns:repeat(auto-fit,minmax(520px,1fr));gap:18px}}section{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 22px}}
h2{{font-size:21px;margin:0 0 10px}}dl{{display:grid;grid-template-columns:auto 1fr;gap:6px 18px;margin:0}}dt{{color:var(--mut);font-size:18px}}dd{{margin:0;font-family:ui-monospace,monospace;font-size:19px;word-break:break-all}}</style></head>
<body><h1>PayPal sandbox: transaction record</h1><p class="s">Live API responses (api-m.sandbox.paypal.com), fetched by live/sandbox_evidence.py. Sandbox money only.</p><div class="g">{body}</div></body></html>"""
    out = os.path.join(ROOT, "docs", "sandbox_evidence.html")
    open(out, "w").write(page)
    print("wrote", out)
    if os.path.exists(CHROME):
        png = out.replace(".html", ".png")
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=1920,1080", f"--screenshot={png}", "file://" + out],
                       capture_output=True, timeout=60)
        print("wrote", png, os.path.exists(png))


if __name__ == "__main__":
    main()
