"""Spending policy: pure functions, no I/O. Every decision carries human-readable reasons.

Order of the rules (all hard rules are evaluated, so a DENY lists *every* reason):
  1. amount is a clean positive 2-decimal number          (else DENY, nothing else is checked)
  2. currency, payee allow-list / block-list, category    (DENY)
  3. per-payment cap, per-payee cap, daily cap, per-payee daily cap   (DENY)
  4. velocity: payments per hour / per payee per day, pending-queue size  (DENY)
  5. duplicate payment inside the duplicate window         (DENY)
  6. soft rules -> a human decides: injection tripwire, hidden characters,
     human-review categories, amount above the auto-approve limit        (NEEDS_HUMAN)
  7. otherwise                                             (APPROVE)
Fail-closed: any unexpected exception while evaluating is a DENY.
"""
from __future__ import annotations
import json, re, unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

APPROVE, NEEDS_HUMAN, DENY = "approve", "needs_human", "deny"

# Phrases that show up when a web page / email / tool result tries to steer the agent into paying.
INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions?|rules?|directions?|guidance)",
    r"(do not|don't|never) (tell|inform|notify|alert|mention) (this to )?(the )?(user|owner|human|operator)",
    r"(send|transfer|pay|wire|remit) .{0,40}(immediately|urgent|urgently|right now|asap|at once)",
    r"system\s*(prompt|override|message|notice)?\s*:\s*(override|ignore|pay|new|you)",
    r"system (prompt|override|message|notice)\s*:",
    r"(forget|ignore|disregard) (everything|all) (above|before|prior)",
    r"(forget|disregard) (all |any |the )?(earlier|previous|prior) \w+",
    r"you (must|are required to|have to|need to) (now )?(pay|send|transfer|wire)",
    r"disregard (the |your )?(budget|policy|limit|limits|rules)",
    r"(new|updated) (payment|payee|bank) (instructions|details)",
    r"(bypass|skip|override) (the )?(approval|human|review|policy)",
]
# Zero-width / bidi-control characters are used to hide instructions from people reading the same text.
def _is_hidden(c):
    o = ord(c)
    # format chars (zero-width, bidi, soft hyphen, Unicode tag chars U+E00xx ...), private use, variation selectors, CGJ
    return unicodedata.category(c) in ("Cf", "Co") or 0xFE00 <= o <= 0xFE0F or 0xE0100 <= o <= 0xE01EF or o == 0x034F


class _Hidden:
    """Drop-in for the old regex (.sub/.search) but covers every invisible class, not a short list."""
    def sub(self, repl, text):
        return "".join(c for c in str(text) if not _is_hidden(c))

    def search(self, text):
        return any(_is_hidden(c) for c in str(text))


HIDDEN_CHARS = _Hidden()

KNOWN_KEYS = {"currency", "per_payment_cap", "auto_approve_up_to", "daily_cap", "max_payments_per_hour",
              "max_payments_per_day", "max_pending_human", "duplicate_window_seconds", "pending_ttl_seconds",
              "max_requests_per_hour", "order_ttl_seconds", "allowed_payees", "blocked_payees", "blocked_categories", "human_review_categories"}
PAYEE_KEYS = {"name", "categories", "max_amount", "daily_cap", "max_payments_per_day", "paypal_email"}

DEFAULT_POLICY = {
    "currency": "USD",
    "per_payment_cap": "50.00",       # above this: always denied
    "auto_approve_up_to": "20.00",    # at or below this (and allow-listed payee): no human needed
    "daily_cap": "100.00",
    "max_payments_per_hour": 5,
    "max_payments_per_day": 20,
    "max_pending_human": 10,          # approval-fatigue guard: a flood of requests cannot bury the human
    "duplicate_window_seconds": 600,
    "pending_ttl_seconds": 86400,     # a human decision older than this is void (EXPIRED)
    "max_requests_per_hour": 30,      # every request counts, denied ones too: caps probing and loops
    "order_ttl_seconds": 10800,       # an uncaptured PayPal order older than this is not captured (PayPal orders lapse too)
    "allowed_payees": {               # payee id -> {name, categories, optional max_amount/daily_cap/max_payments_per_day}
        "acme-api": {"name": "Acme Data API", "categories": ["api", "data"]},
        "docs-pub": {"name": "Docs Publisher", "categories": ["subscription", "content"]},
    },
    "blocked_payees": [],
    "blocked_categories": ["gift_card", "crypto", "gambling", "adult"],
    "human_review_categories": ["subscription"],
}


class PolicyError(ValueError):
    pass


@dataclass
class Decision:
    verdict: str
    reasons: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    evidence: list = field(default_factory=list)   # the exact text that tripped a flag

    def to_dict(self):
        d = {"verdict": self.verdict, "reasons": self.reasons, "flags": self.flags}
        if self.evidence:
            d["evidence"] = self.evidence
        return d


@dataclass
class Usage:
    """What the ledger already knows. Computed by the service, passed in so this module stays pure."""
    spent_24h: Decimal = Decimal("0")
    count_1h: int = 0
    count_24h: int = 0
    payee_spent_24h: Decimal = Decimal("0")
    payee_count_24h: int = 0
    pending_human: int = 0
    requests_1h: int = 0
    duplicate_of: str = ""


def money(v) -> Decimal:
    if isinstance(v, bool) or v is None:
        raise ValueError(f"invalid amount: {v!r}")
    try:
        d = Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"invalid amount: {v!r}")
    if not d.is_finite():
        raise ValueError(f"invalid amount: {v!r}")
    if d <= 0 or d != d.quantize(Decimal("0.01")):
        raise ValueError("amount must be positive with at most 2 decimals")
    return d.quantize(Decimal("0.01"))


def _dec(v, name):
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise PolicyError(f"{name}: not a number: {v!r}")
    if not d.is_finite() or d <= 0:
        raise PolicyError(f"{name}: must be a positive number")
    return d


def validate_policy(p: dict) -> dict:
    """Reject typos and nonsense up front: a misspelled key must not silently weaken a limit."""
    unknown = set(p) - KNOWN_KEYS
    if unknown:
        raise PolicyError(f"unknown policy keys: {sorted(unknown)}")
    cap, auto, day = (_dec(p[k], k) for k in ("per_payment_cap", "auto_approve_up_to", "daily_cap"))
    if auto > cap:
        raise PolicyError("auto_approve_up_to must be <= per_payment_cap")
    if cap > day:
        raise PolicyError("per_payment_cap must be <= daily_cap")
    for k in ("max_payments_per_hour", "max_payments_per_day", "max_pending_human",
              "duplicate_window_seconds", "pending_ttl_seconds", "max_requests_per_hour", "order_ttl_seconds"):
        if not isinstance(p[k], int) or isinstance(p[k], bool) or p[k] < 0:
            raise PolicyError(f"{k}: must be a non-negative integer")
    if not isinstance(p["allowed_payees"], dict):
        raise PolicyError("allowed_payees must be an object")
    for pid, info in p["allowed_payees"].items():
        if not isinstance(info, dict) or set(info) - PAYEE_KEYS:
            raise PolicyError(f"payee {pid!r}: unknown keys {sorted(set(info) - PAYEE_KEYS) if isinstance(info, dict) else info}")
        if "paypal_email" in info and not (isinstance(info["paypal_email"], str) and "@" in info["paypal_email"]):
            raise PolicyError(f"payee {pid!r}: paypal_email must look like an email address")
        if not isinstance(info.get("categories"), list) or not info["categories"] or not all(isinstance(c, str) for c in info["categories"]):
            raise PolicyError(f"payee {pid!r}: needs a non-empty categories list")
        for k in ("max_amount", "daily_cap"):
            if k in info:
                _dec(info[k], f"payee {pid!r} {k}")
        if pid in p["blocked_payees"]:
            raise PolicyError(f"payee {pid!r} is both allowed and blocked")
    for k in ("blocked_payees", "blocked_categories", "human_review_categories"):
        if not isinstance(p[k], list):
            raise PolicyError(f"{k} must be a list")
    return p


def load_policy(path=None) -> dict:
    p = json.loads(json.dumps(DEFAULT_POLICY))
    if path:
        with open(path, encoding="utf-8") as f:
            p.update(json.load(f))
    return validate_policy(p)


# Latin look-alikes from Cyrillic/Greek: NFKC does not fold them, so "ignоre" (Cyrillic o) would slip past the patterns.
_CONFUSABLE = str.maketrans({"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ј": "j",
                             "ѕ": "s", "ԁ": "d", "ɡ": "g", "ο": "o", "α": "a", "ε": "e", "ι": "i", "ν": "v", "υ": "u",
                             "τ": "t", "κ": "k", "ρ": "p", "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "Х": "X",
                             "Т": "T", "М": "M", "Н": "H", "К": "K", "В": "B"})


def _norm(t) -> str:
    t = "".join(chr(ord(c) - 0xE0000) if 0xE0020 <= ord(c) <= 0xE007E else c for c in str(t))  # reveal tag-char text
    return unicodedata.normalize("NFKC", HIDDEN_CHARS.sub("", t)).translate(_CONFUSABLE)


def find_injection(*texts) -> list:
    """Return the matched snippets (so a human can see exactly what the agent was told)."""
    hits = []
    for t in texts:
        if not t:
            continue
        s = _norm(t)
        for pat in INJECTION_PATTERNS:
            m = re.search(pat, s, re.I | re.S)
            if m:
                hits.append(m.group(0)[:120])
    return hits


def scan_injection(*texts) -> list:
    return find_injection(*texts)


def has_hidden_chars(*texts) -> bool:
    return any(t and HIDDEN_CHARS.search(str(t)) for t in texts)


def evaluate(policy: dict, req: dict, spent_today=Decimal("0"), payments_last_hour: int = 0,
             usage: "Usage | None" = None) -> Decision:
    """req: amount, currency, payee_id, category, description, context (text the agent read).
    The legacy (spent_today, payments_last_hour) arguments are used when `usage` is not given."""
    try:
        return _evaluate(policy, req, usage or Usage(Decimal(spent_today), payments_last_hour))
    except Exception as e:  # fail closed
        return Decision(DENY, [f"policy could not be evaluated ({type(e).__name__}); denied for safety"])


def _evaluate(policy, req, u: Usage) -> Decision:
    try:
        amount = money(req.get("amount"))
    except ValueError as e:
        return Decision(DENY, [str(e)])

    deny, soft, flags, evidence = [], [], [], []
    cur = req.get("currency") or policy["currency"]
    if cur != policy["currency"]:
        deny.append(f"currency must be {policy['currency']}")

    pid, cat = req.get("payee_id"), req.get("category") or ""
    payee = policy["allowed_payees"].get(pid) if isinstance(pid, str) else None
    if isinstance(pid, str) and pid in policy.get("blocked_payees", []):
        deny.append(f"payee '{pid}' is blocked")
    elif payee is None:
        deny.append(f"payee '{pid}' is not on the allow-list")
    if cat in policy["blocked_categories"]:
        deny.append(f"category '{cat}' is blocked")
    elif payee is not None and cat not in payee["categories"]:
        deny.append(f"{payee['name']} is not approved for category '{cat}'")

    if amount > Decimal(policy["per_payment_cap"]):
        deny.append(f"{amount} exceeds per-payment cap {policy['per_payment_cap']}")
    if u.spent_24h + amount > Decimal(policy["daily_cap"]):
        deny.append(f"daily cap {policy['daily_cap']} would be exceeded (spent {u.spent_24h})")
    if payee is not None:
        if "max_amount" in payee and amount > Decimal(payee["max_amount"]):
            deny.append(f"{amount} exceeds the {payee['name']} limit {payee['max_amount']}")
        if "daily_cap" in payee and u.payee_spent_24h + amount > Decimal(payee["daily_cap"]):
            deny.append(f"{payee['name']} daily cap {payee['daily_cap']} would be exceeded (spent {u.payee_spent_24h})")
        if "max_payments_per_day" in payee and u.payee_count_24h >= int(payee["max_payments_per_day"]):
            deny.append(f"{payee['name']}: too many payments in 24h (limit {payee['max_payments_per_day']})")
    if u.count_1h >= int(policy["max_payments_per_hour"]):
        deny.append("velocity limit: too many payments in the last hour")
    if u.count_24h >= int(policy["max_payments_per_day"]):
        deny.append("velocity limit: too many payments in the last 24 hours")
    if u.requests_1h >= int(policy["max_requests_per_hour"]):
        deny.append(f"too many requests from this agent in the last hour (limit {policy['max_requests_per_hour']})")
    if u.pending_human >= int(policy["max_pending_human"]):
        deny.append("too many requests are already waiting for a human; wait for them to be decided")
    if u.duplicate_of:
        deny.append(f"duplicate of {u.duplicate_of} (same payee, amount, category, description within "
                    f"{policy['duplicate_window_seconds']}s); use payment_status instead of paying twice")
    if deny:
        return Decision(DENY, deny)

    hits = find_injection(req.get("description"), req.get("context"))
    if hits:
        flags.append("possible_prompt_injection")
        evidence += hits
        soft.append("text the agent read contains instruction-like payment pressure")
    if has_hidden_chars(req.get("description"), req.get("context")):
        flags.append("hidden_characters")
        soft.append("text the agent read contains invisible characters (a way to hide instructions)")
    if cat in policy["human_review_categories"]:
        soft.append(f"category '{cat}' always needs a human")
    if amount > Decimal(policy["auto_approve_up_to"]):
        soft.append(f"{amount} is above the auto-approve limit {policy['auto_approve_up_to']}")
    if soft:
        return Decision(NEEDS_HUMAN, soft, flags, evidence)
    return Decision(APPROVE, [f"allow-listed payee, {amount} within auto-approve limit"])
