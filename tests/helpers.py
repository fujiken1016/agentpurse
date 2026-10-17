from decimal import Decimal
from agentpurse import policy as P
from agentpurse.ledger import Ledger
from agentpurse.paypal import MockPayPal
from agentpurse.service import AgentPurse
import copy


def req(**kw):
    r = dict(amount="10.00", payee_id="acme-api", category="api", description="API credits", currency="USD")
    r.update(kw)
    return r


def policy(**over):
    p = copy.deepcopy(P.DEFAULT_POLICY)
    p.update(over)
    return p


def make(**over):
    t = [1_000_000.0]
    pp = MockPayPal()
    svc = AgentPurse(pp, Ledger(clock=lambda: t[0]), policy(**over), clock=lambda: t[0])
    return svc, pp, t
