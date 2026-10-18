"""SQLite ledger + tamper-evident audit log.

Payment statuses: DENIED, PENDING_HUMAN, REJECTED, EXPIRED, CANCELLED, AUTO_APPROVED, ORDER_CREATED, CAPTURED, FAILED, plus
PAYOUT_PENDING / PAID (Payouts API: the agent paid an allow-listed PayPal account directly; PAID = batch SUCCESS),
AUTO_APPROVED counts as spent too (a reservation while the PayPal call is in flight),
ORDER_UNKNOWN (PayPal did not answer the create call; retried with the same PayPal-Request-Id), CAPTURE_UNKNOWN (no answer to
the capture call; reconciled through get_order, never expired) and CAPTURE_PENDING
(PayPal accepted the capture but it is not COMPLETED yet; both count as spent until resolved).
Every audit row that names a payment also carries `row_hash`, a hash of that payment's row at that moment, so the chain
covers the payments table too (`verify_ledger`).
The audit table is append-only and hash-chained: each row stores sha256(prev_hash + its own fields), so editing
or deleting any past row breaks verification from that row on (`verify_audit`).
"""
from __future__ import annotations
import contextlib, hashlib, json, sqlite3, threading, time, uuid
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
SPENDING = ("AUTO_APPROVED", "PAYOUT_PENDING", "PAID", "ORDER_CREATED", "ORDER_UNKNOWN", "CAPTURE_PENDING", "CAPTURE_UNKNOWN", "CAPTURED")                      # statuses that reserve budget
LIVE = ("PENDING_HUMAN", "AUTO_APPROVED", "PAYOUT_PENDING", "PAID", "ORDER_CREATED", "ORDER_UNKNOWN", "CAPTURE_PENDING", "CAPTURE_UNKNOWN", "CAPTURED")  # statuses a duplicate can collide with
GENESIS = "0" * 64


def _norm_text(s) -> str:
    return " ".join(str(s or "").lower().split())


def row_hash(p) -> str:
    """Hash of the WHOLE payment row (every column except the volatile `updated`), so editing the context, the decision,
    the approve_url, the agent, the creation time ... is caught, not just money fields."""
    body = {k: (None if v is None else str(v)) for k, v in sorted(p.items()) if k != "updated"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


class _Result:
    def __init__(self, rows, rowcount):
        self.rows, self.rowcount = rows, rowcount

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class _LockedConn:
    """One sqlite3 connection shared by dashboard threads: every call is serialised (a bare shared connection
    segfaults CPython 3.9 under concurrent use) and results are materialised so no cursor outlives the lock."""

    def __init__(self, conn):
        self.conn, self.lock = conn, threading.RLock()

    def execute(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            return _Result(cur.fetchall() if cur.description else [], cur.rowcount)

    def executescript(self, sql):
        with self.lock:
            self.conn.executescript(sql)

    def commit(self):
        with self.lock:
            self.conn.commit()

    def rollback(self):
        with self.lock:
            self.conn.rollback()

    @property
    def in_transaction(self):
        return self.conn.in_transaction


def _hash(prev, ts, event, pid, actor, detail) -> str:
    body = json.dumps([prev, repr(ts), event, pid, actor, detail], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


class Ledger:
    def __init__(self, path=":memory:", clock=time.time):
        self.clock = clock
        self.path = None if path == ":memory:" else path
        conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        self.db, self._depth = _LockedConn(conn), 0
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("pragma table_info(payments)")}
        if "idempotency_key" not in cols:  # database created by an older version
            self.db.execute("alter table payments add column idempotency_key text")
        if "committed_at" not in cols:  # when money became committed (the budget window anchors here, not on `created`)
            self.db.execute("alter table payments add column committed_at real")
        self.db.execute("create unique index if not exists ux_idem on payments(idempotency_key) "
                        "where idempotency_key is not null")
        self.db.commit()

    @contextlib.contextmanager
    def atomic(self):
        """One transaction for a state change AND its audit row: a crash cannot leave one without the other."""
        with self.db.lock:
            if self._depth == 0:
                if self.db.in_transaction:
                    self.db.commit()
                self.db.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if self._depth == 0:
                    self.db.rollback()
                raise
            self._depth -= 1
            if self._depth == 0:
                self.db.commit()

    # --- payments -----------------------------------------------------
    @staticmethod
    def new_id() -> str:
        return "pay_" + uuid.uuid4().hex[:10]

    def add(self, req, status, decision, actor="agent", pid=None) -> str:
        pid = pid or self.new_id()
        now = self.clock()
        with self.atomic():
            self.db.execute(
                "insert into payments(id,created,agent_id,payee_id,category,amount,currency,description,context,"
                "status,decision,updated,idempotency_key) values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, now, req.get("agent_id", "agent"), str(req.get("payee_id")), str(req.get("category")),
                 str(req.get("amount")), req.get("currency") or "USD", req.get("description"),
                 (req.get("context") or "")[:2000], status, json.dumps(decision), now,
                 req.get("idempotency_key") or None))
        return pid

    def update(self, pid, only_if_status=None, **kw):
        """Returns True if a row changed. only_if_status makes it a compare-and-swap, so two processes
        (MCP server and dashboard) cannot both move the same payment (e.g. cancel vs approve)."""
        now = kw["updated"] = self.clock()
        cols = ",".join(f"{k}=?" for k in kw)
        sql, args = f"update payments set {cols}", [*kw.values()]
        if kw.get("status") in ("ORDER_CREATED", "ORDER_UNKNOWN", "PAYOUT_PENDING", "PAID"):  # money is committed from here: anchor the budget window
            sql, args = sql + ",committed_at=coalesce(committed_at,?)", args + [now]
        sql, args = sql + " where id=?", args + [pid]
        if only_if_status:
            sql, args = sql + " and status=?", args + [only_if_status]
        with self.atomic():
            n = self.db.execute(sql, args).rowcount
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
        q = "select amount from payments where coalesce(committed_at,created)>=? and status in (%s)" % ",".join("?" * len(SPENDING))
        a = [t, *SPENDING]
        if payee:
            q += " and payee_id=?"
            a.append(payee)
        return sum((Decimal(r[0]) for r in self._q(q, a)), Decimal("0"))

    def count_since(self, t, payee=None) -> int:
        """Orders opened (velocity). A cancelled order still counts: otherwise propose->cancel loops are free."""
        q = ("select count(*) from payments where coalesce(committed_at,created)>=? and (status in (%s) or (status in ('CANCELLED','EXPIRED') and order_id is not null))"
             % ",".join("?" * len(SPENDING)))
        a = [t, *SPENDING]
        if payee:
            q += " and payee_id=?"
            a.append(payee)
        return self._q(q, a).fetchone()[0]

    def requests_since(self, agent, t, exclude="") -> int:
        """Every request the agent made (denied ones too): the attempt-rate limit and the reviewer-oracle guard."""
        return self._q("select count(*) from payments where created>=? and agent_id=? and id!=?", (t, agent, exclude)).fetchone()[0]

    def stale(self, status, older_than):
        return [dict(r) for r in self._q("select * from payments where status=? and updated<?", (status, older_than))]

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
        detail = dict(detail or {})
        if payment_id:
            row = self.get(payment_id)
            if row:
                detail["row_hash"] = row_hash(row)
        detail_s = json.dumps(detail, sort_keys=True, separators=(",", ":"), default=str)
        ts = self.clock()
        with self.atomic():
            r = self.db.execute("select hash from audit order by seq desc limit 1").fetchone()
            prev = r[0] if r else GENESIS
            h = _hash(prev, ts, event, payment_id, actor, detail_s)
            self.db.execute("insert into audit(ts,event,payment_id,actor,detail,prev_hash,hash) values(?,?,?,?,?,?,?)",
                            (ts, event, payment_id, actor, detail_s, prev, h))

    def audit_rows(self, payment_id=None, limit=200):
        if payment_id:
            rs = self.db.execute("select * from audit where payment_id=? order by seq", (payment_id,))
        else:
            rs = self.db.execute("select * from (select * from audit order by seq desc limit ?) order by seq", (limit,))
        return [dict(r) for r in rs]

    def verify_ledger(self):
        """Payments that no longer match the last state the audit chain recorded for them (edited, deleted, or
        inserted without a trace). Complements verify_audit(), which only protects the log itself. -> list of ids"""
        last = {}
        for r in self.db.execute("select payment_id, detail from audit where payment_id is not null order by seq"):
            h = json.loads(r["detail"]).get("row_hash")
            if h:
                last[r["payment_id"]] = h
        rows = {r["id"]: dict(r) for r in self.db.execute("select * from payments")}
        bad = [pid for pid, h in last.items() if pid not in rows or row_hash(rows[pid]) != h]
        bad += [pid for pid in rows if pid not in last]
        return sorted(set(bad))

    def head_hash(self):
        r = self.db.execute("select seq, hash from audit order by seq desc limit 1").fetchone()
        return (r["seq"], r["hash"]) if r else (0, GENESIS)

    def verify_audit(self):
        """(ok, first_bad_seq, rows_checked)"""
        prev, n = GENESIS, 0
        for r in self.db.execute("select * from audit order by seq"):
            n += 1
            if r["prev_hash"] != prev or r["hash"] != _hash(prev, r["ts"], r["event"], r["payment_id"], r["actor"], r["detail"]):
                return False, r["seq"], n
            prev = r["hash"]
        return True, None, n
