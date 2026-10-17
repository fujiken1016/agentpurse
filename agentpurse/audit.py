"""Audit CLI.  python -m agentpurse.audit --db agentpurse.db [--payment pay_x] [--tail 20]
Also checks that every payment still matches the last state the chain recorded for it, prints the head hash (publish it
somewhere append-only; pass it back with --expect-head to detect deleted newest rows).
Exit code 0 = intact, 2 = tampering detected."""
import argparse, json, sys, time
from .ledger import Ledger


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="agentpurse.db")
    ap.add_argument("--payment")
    ap.add_argument("--tail", type=int, default=20)
    ap.add_argument("--expect-head", help="'<seq>:<hash>' printed by an earlier run; fails if that row is gone or changed")
    a = ap.parse_args(argv)
    led = Ledger(a.db)
    for r in led.audit_rows(a.payment, a.tail):
        print(f"{r['seq']:>4} {time.strftime('%H:%M:%S', time.localtime(r['ts']))} {r['event']:<24} {r['payment_id'] or '-':<14} {r['actor']:<16} {r['detail'][:90]}")
    ok, bad, n = led.verify_audit()
    print(f"\nhash chain: {'OK' if ok else 'BROKEN at seq %s' % bad} ({n} rows)")
    mism = led.verify_ledger()
    print(f"payments vs chain: {'OK' if not mism else 'MISMATCH ' + ', '.join(mism)}")
    seq, h = led.head_hash()
    print(f"head: {seq}:{h}")
    head_ok = True
    if a.expect_head:
        s, _, eh = a.expect_head.partition(":")
        row = led.db.execute("select hash from audit where seq=?", (int(s),)).fetchone()
        head_ok = bool(row) and row[0] == eh
        print(f"expected head: {'OK' if head_ok else 'NOT FOUND / CHANGED (rows deleted or edited)'}")
    return 0 if (ok and not mism and head_ok) else 2


if __name__ == "__main__":
    sys.exit(main())
