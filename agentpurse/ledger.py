"""SQLite ledger + tamper-evident audit log.

Payment statuses: DENIED, PENDING_HUMAN, REJECTED, EXPIRED, CANCELLED, AUTO_APPROVED, ORDER_CREATED, CAPTURED, FAILED.
The audit table is append-only and hash-chained: each row stores sha256(prev_hash + its own fields), so editing
or deleting any past row breaks verification from that row on (`verify_audit`).
"""
from __future__ import annotations
import hashlib, json, sqlite3, time, uuid
from decimal import Decimal

SCHEMA = """
create table if not exists payments(
  id text primary key, created real, agent_id text, payee_id text, category text,
  amount text, currency text, description text, context text,
  status text, decision text, order_id text, approve_url text, capture_id text, updated real,
  idempotency_key text);
create index if not exists ix_created on payments(created);
create table if not exists audit(
  seq integer primary key autoincrement, ts real, event text, payment_id text, actor text,
  detail text, prev_hash text, hash text);
"""
SPENDING = ("ORDER_CREATED", "CAPTURED")                      # statuses that reserve budget
LIVE = ("PENDING_HUMAN", "AUTO_APPROVED", "ORDER_CREATED", "CAPTURED")  # statuses a duplicate can collide with
GENESIS = "0" * 64


def _norm_text(s) -> str:
    return " ".join(str(s or "").lower().split())


def _hash(prev, ts, event, pid, actor, detail) -> str:
    body = json.dumps([prev, repr(ts), event, pid, actor, detail], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


class Ledger:
    def __init__(self, path=":memory:", clock=time.time):
        self.clock = clock
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("pragma table_info(payments)")}
        if "idempotency_key" not in cols:  # database created by an older version
            self.db.execute("alter table payments add column idempotency_key text")
        self.db.execute("create unique index if not exists ux_idem on payments(idempotency_key) "
                        "where idempotency_key is not null")
        self.db.commit()

    # --- payments -----------------------------------------------------
    def add(self, req, status, decision, actor="agent") -> str:
        pid = "pay_" + uuid.uuid4().hex[:10]
        now = self.clock()
        self.db.execute(
            "insert into payments(id,created,agent_id,payee_id,category,amount,currency,description,context,"
            "status,decision,updated,idempotency_key) values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, now, req.get("agent_id", "agent"), str(req.get("payee_id")), str(req.get("category")),
             str(req.get("amount")), req.get("currency") or "USD", req.get("description"),
             (req.get("context") or "")[:2000], status, json.dumps(decision), now,
             req.get("idempotency_key") or None))
        self.db.commit()
        return pid

    def update(self, pid, only_if_status=None, **kw):
        """Returns True if a row changed. only_if_status makes it a compare-and-swap, so two processes
        (MCP server and dashboard) cannot both move the same payment (e.g. cancel vs approve)."""
        kw["updated"] = self.clock()
        cols = ",".join(f"{k}=?" for k in kw)
        sql, args = f"update payments set {cols} where id=?", [*kw.values(), pid]
        if only_if_status:
            sql, args = sql + " and status=?", args + [only_if_status]
        n = self.db.execute(sql, args).rowcount
        self.db.commit()
        return n > 0

    def get(self, pid):
        r = self.db.execute("select * from payments where id=?", (pid,)).fetchone()
        return dict(r) if r else None

    def by_idempotency_key(self, key):
        r = self.db.execute("select * from payments where idempotency_key=?", (key,)).fetchone()
        return dict(r) if r else None

    def list(self, limit=50):
        return [dict(r) for r in self.db.execute(
            "select * from payments order by created desc limit ?", (limit,))]

    def _q(self, sql, args):
        return self.db.execute(sql, args)

    def spent_since(self, t, payee=None) -> Decimal:
        q = "select amount from payments where created>=? and status in (%s)" % ",".join("?" * len(SPENDING))
        a = [t, *SPENDING]
        if payee:
            q += " and payee_id=?"
            a.append(payee)
        return sum((Decimal(r[0]) for r in self._q(q, a)), Decimal("0"))

    def count_since(self, t, payee=None) -> int:
        """Orders opened (velocity). A cancelled order still counts: otherwise propose->cancel loops are free."""
        q = ("select count(*) from payments where created>=? and (status in (%s) or (status='CANCELLED' and order_id is not null))"
             % ",".join("?" * len(SPENDING)))
        a = [t, *SPENDING]
        if payee:
            q += " and payee_id=?"
            a.append(payee)
        return self._q(q, a).fetchone()[0]

    def pending_count(self) -> int:
        return self._q("select count(*) from payments where status='PENDING_HUMAN'", ()).fetchone()[0]

    def find_duplicate(self, req, since, exclude="") -> str:
        """id of a live payment with the same agent, payee, amount, category and description, or ''."""
        q = ("select id, description from payments where created>=? and payee_id=? and amount=? and category=? "
             "and agent_id=? and id!=? and status in (%s)" % ",".join("?" * len(LIVE)))
        for r in self._q(q, (since, str(req.get("payee_id")), str(req.get("amount")), str(req.get("category")),
                             req.get("agent_id", "agent"), exclude, *LIVE)):
            if _norm_text(r["description"]) == _norm_text(req.get("description")):
                return r["id"]
        return ""

    def expire_pending(self, older_than) -> list:
        ids = [r[0] for r in self._q("select id from payments where status='PENDING_HUMAN' and created<?", (older_than,))]
        for i in ids:
            self.update(i, status="EXPIRED")
        return ids

    # --- audit --------------------------------------------------------
    def log(self, event, payment_id=None, actor="system", detail=None):
        """Append one chained row. BEGIN IMMEDIATE so the MCP server and the dashboard (two processes) cannot fork the chain."""
        detail_s = json.dumps(detail or {}, sort_keys=True, separators=(",", ":"), default=str)
        ts = self.clock()
        if self.db.in_transaction:
            self.db.commit()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            r = self.db.execute("select hash from audit order by seq desc limit 1").fetchone()
            prev = r[0] if r else GENESIS
            h = _hash(prev, ts, event, payment_id, actor, detail_s)
            self.db.execute("insert into audit(ts,event,payment_id,actor,detail,prev_hash,hash) values(?,?,?,?,?,?,?)",
                            (ts, event, payment_id, actor, detail_s, prev, h))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def audit_rows(self, payment_id=None, limit=200):
        if payment_id:
            rs = self.db.execute("select * from audit where payment_id=? order by seq", (payment_id,))
        else:
            rs = self.db.execute("select * from (select * from audit order by seq desc limit ?) order by seq", (limit,))
        return [dict(r) for r in rs]

    def verify_audit(self):
        """(ok, first_bad_seq, rows_checked)"""
        prev, n = GENESIS, 0
        for r in self.db.execute("select * from audit order by seq"):
            n += 1
            if r["prev_hash"] != prev or r["hash"] != _hash(prev, r["ts"], r["event"], r["payment_id"], r["actor"], r["detail"]):
                return False, r["seq"], n
            prev = r["hash"]
        return True, None, n
