"""Audit CLI.  python -m agentpurse.audit --db agentpurse.db [--payment pay_x] [--tail 20]
Exit code 0 = hash chain intact, 2 = tampering detected."""
import argparse, json, sys, time
from .ledger import Ledger


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="agentpurse.db")
    ap.add_argument("--payment")
    ap.add_argument("--tail", type=int, default=20)
    a = ap.parse_args(argv)
    led = Ledger(a.db)
    for r in led.audit_rows(a.payment, a.tail):
        print(f"{r['seq']:>4} {time.strftime('%H:%M:%S', time.localtime(r['ts']))} {r['event']:<24} {r['payment_id'] or '-':<14} {r['actor']:<16} {r['detail'][:90]}")
    ok, bad, n = led.verify_audit()
    print(f"\nhash chain: {'OK' if ok else 'BROKEN at seq %s' % bad} ({n} rows)")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
