"""Spending policy: pure functions, no I/O. Every decision carries human-readable reasons."""
from __future__ import annotations
import json, re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

APPROVE, NEEDS_HUMAN, DENY = "approve", "needs_human", "deny"

# Phrases that show up when a web page / email / tool result tries to steer the agent into paying.
INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) (instructions|rules)",
    r"(do not|don't) (tell|inform|notify) (the )?(user|owner|human)",
    r"(send|transfer|pay|wire) .{0,40}(immediately|urgent|right now|asap)",
    r"system (prompt|override|message)\s*:",
    r"you (must|are required to) (now )?(pay|send|transfer)",
    r"disregard (the )?(budget|policy|limit)",
]

DEFAULT_POLICY = {
    "currency": "USD",
    "per_payment_cap": "50.00",       # above this: always denied
    "auto_approve_up_to": "20.00",    # at or below this (and allow-listed payee): no human needed
    "daily_cap": "100.00",
    "max_payments_per_hour": 5,
    "allowed_payees": {               # payee id -> {name, categories}
        "acme-api": {"name": "Acme Data API", "categories": ["api", "data"]},
        "docs-pub": {"name": "Docs Publisher", "categories": ["subscription", "content"]},
    },
    "blocked_categories": ["gift_card", "crypto", "gambling", "adult"],
    "human_review_categories": ["subscription"],
}


@dataclass
class Decision:
    verdict: str
    reasons: list = field(default_factory=list)
    flags: list = field(default_factory=list)

    def to_dict(self):
        return {"verdict": self.verdict, "reasons": self.reasons, "flags": self.flags}


def money(v) -> Decimal:
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise ValueError(f"invalid amount: {v!r}")
    if d <= 0 or d != d.quantize(Decimal("0.01")):
        raise ValueError("amount must be positive with at most 2 decimals")
    return d


def load_policy(path=None) -> dict:
    p = dict(DEFAULT_POLICY)
    if path:
        with open(path, encoding="utf-8") as f:
            p.update(json.load(f))
    return p


def scan_injection(*texts) -> list:
    hits = []
    for t in texts:
        if not t:
            continue
        for pat in INJECTION_PATTERNS:
            if re.search(pat, str(t), re.I):
                hits.append(pat)
    return hits


def evaluate(policy: dict, req: dict, spent_today: Decimal, payments_last_hour: int) -> Decision:
    """req: amount, currency, payee_id, category, description, context (text the agent read)."""
    reasons, flags = [], []
    try:
        amount = money(req.get("amount"))
    except ValueError as e:
        return Decision(DENY, [str(e)])

    if req.get("currency", policy["currency"]) != policy["currency"]:
        return Decision(DENY, [f"currency must be {policy['currency']}"])
    payee = policy["allowed_payees"].get(req.get("payee_id"))
    if payee is None:
        return Decision(DENY, [f"payee '{req.get('payee_id')}' is not on the allow-list"])
    cat = req.get("category", "")
    if cat in policy["blocked_categories"]:
        return Decision(DENY, [f"category '{cat}' is blocked"])
    if cat not in payee["categories"]:
        return Decision(DENY, [f"{payee['name']} is not approved for category '{cat}'"])
    if amount > Decimal(policy["per_payment_cap"]):
        return Decision(DENY, [f"{amount} exceeds per-payment cap {policy['per_payment_cap']}"])
    if spent_today + amount > Decimal(policy["daily_cap"]):
        return Decision(DENY, [f"daily cap {policy['daily_cap']} would be exceeded (spent {spent_today})"])
    if payments_last_hour >= int(policy["max_payments_per_hour"]):
        return Decision(DENY, ["velocity limit: too many payments in the last hour"])

    hits = scan_injection(req.get("description"), req.get("context"))
    if hits:
        flags.append("possible_prompt_injection")
        reasons.append("text the agent read contains instruction-like payment pressure")
    if cat in policy["human_review_categories"]:
        reasons.append(f"category '{cat}' always needs a human")
    if amount > Decimal(policy["auto_approve_up_to"]):
        reasons.append(f"{amount} is above the auto-approve limit {policy['auto_approve_up_to']}")
    if reasons:
        return Decision(NEEDS_HUMAN, reasons, flags)
    return Decision(APPROVE, [f"allow-listed payee, {amount} within auto-approve limit"], flags)
