"""SQLite ledger. Statuses: DENIED, PENDING_HUMAN, REJECTED, ORDER_CREATED, CAPTURED, FAILED."""
from __future__ import annotations
import json, sqlite3, time, uuid
from decimal import Decimal

SCHEMA = """
create table if not exists payments(
  id text primary key, created real, agent_id text, payee_id text, category text,
  amount text, currency text, description text, context text,
  status text, decision text, order_id text, approve_url text, capture_id text, updated real);
create index if not exists ix_created on payments(created);
"""
SPENDING = ("ORDER_CREATED", "CAPTURED")  # statuses that reserve budget


class Ledger:
    def __init__(self, path=":memory:", clock=time.time):
        self.clock = clock
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def add(self, req, status, decision) -> str:
        pid = "pay_" + uuid.uuid4().hex[:10]
        now = self.clock()
        self.db.execute(
            "insert into payments values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, now, req.get("agent_id", "agent"), req.get("payee_id"), req.get("category"),
             str(req.get("amount")), req.get("currency", "USD"), req.get("description"),
             (req.get("context") or "")[:2000], status, json.dumps(decision), None, None, None, now))
        self.db.commit()
        return pid

    def update(self, pid, **kw):
        kw["updated"] = self.clock()
        cols = ",".join(f"{k}=?" for k in kw)
        self.db.execute(f"update payments set {cols} where id=?", (*kw.values(), pid))
        self.db.commit()

    def get(self, pid):
        r = self.db.execute("select * from payments where id=?", (pid,)).fetchone()
        return dict(r) if r else None

    def list(self, limit=50):
        return [dict(r) for r in self.db.execute(
            "select * from payments order by created desc limit ?", (limit,))]

    def spent_since(self, t) -> Decimal:
        q = "select amount from payments where created>=? and status in (%s)" % ",".join("?" * len(SPENDING))
        return sum((Decimal(r[0]) for r in self.db.execute(q, (t, *SPENDING))), Decimal("0"))

    def count_since(self, t) -> int:
        q = "select count(*) from payments where created>=? and status in (%s)" % ",".join("?" * len(SPENDING))
        return self.db.execute(q, (t, *SPENDING)).fetchone()[0]
