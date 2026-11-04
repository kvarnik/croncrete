"""Compiled support-triage workflow. Generated; do not hand-edit."""

from pydantic import BaseModel, ConfigDict

from croncrete import Priority, llm


class CrmLookupGuard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: str
    name: str
    open_tickets: int
    tier: str


class TicketsCreateGuard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket_id: str


class OutboxSendGuard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str


class CrmLookupOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: str
    name: str
    open_tickets: int
    tier: str


class TicketsCreateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket_id: str


class OutboxSendOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pass


def map_crm_lookup(response, input, prev):
    return {
        "customer_id": response["customer_id"],
        "name": response["name"],
        "open_tickets": response["open_tickets"],
        "tier": response["tier"],
    }


def map_tickets_create(response, input, prev):
    return {
        "ticket_id": response["ticket_id"],
    }


def map_outbox_send(response, input, prev):
    # nothing downstream reads this response
    return {}


STEPS = [
    {
        "index": 0,
        "tool": "crm_lookup",
        "endpoint": "GET /crm/customers",
        "args": {"email": "input.sender"},
        "guard": "CrmLookupGuard",
        "map_fn": "map_crm_lookup",
        "output_model": "CrmLookupOut",
    },
    {
        "index": 1,
        "tool": "tickets_create",
        "endpoint": "POST /tickets",
        "args": {
            "customer_id": "crm_lookup.customer_id",
            "priority": "judge",
            "summary": "input.subject",
        },
        "guard": "TicketsCreateGuard",
        "map_fn": "map_tickets_create",
        "output_model": "TicketsCreateOut",
    },
    {
        "index": 2,
        "tool": "outbox_send",
        "endpoint": "POST /outbox",
        "args": {"body": "template", "subject": "template", "to": "input.sender"},
        "guard": "OutboxSendGuard",
        "map_fn": "map_outbox_send",
        "output_model": "OutboxSendOut",
    },
]

SCOPES = ["GET /crm/customers", "POST /tickets", "POST /outbox"]


JUDGE_PROMPT = (
    "You are a priority judge for support triage.\nInput: support email "
    "(sender, subject, body) + customer context (tier, open_tickets).\n"
    "Task: return exactly one priority label.\n\nP1 - Outage / Production "
    "Blocked: Core service completely down, no workaround, blocking all "
    "users.\n  e.g., production API all requests failing (500s), webhooks "
    "not delivering for hours, checkout/payments fully down\nP2 - "
    "Degraded or Time-Bound: Partial failure/degraded performance or "
    "hard deadline within 1-2 days.\n  e.g., export/job stuck for hours "
    "with deadline tomorrow, single user locked out with reset failing, "
    "compliance doc needed by Friday for renewal\nP3 - Routine: General "
    "question or request with no immediate service impact.\n  e.g., "
    "billing/invoice clarification, how-to question, feature request "
    "(SSO), update payment method\n\nRules: Judge by impact/urgency in "
    "email; tier/open_tickets is context only, not determinative.\nOutput "
    "requirement: Respond with exactly one of P1, P2, P3\nNo other text, "
    "explanation, JSON, or punctuation."
)


TEMPLATE = (
    "Re: {subject}\n\nHi {name}, we've logged your request as ticket "
    "{ticket_id} with priority {priority}. Our team will reply within "
    "{sla}.\n"
)


SLA_TABLE = {"enterprise": "4 hours", "free": "3 business days", "pro": "1 business day"}


def _dig(obj, dotted):
    for part in dotted.split("."):
        obj = obj[part]
    return obj


def _literal(marker):
    return {"null": None, "true": True, "false": False, '""': ""}[marker[8:]]


def judge_messages(email, customer, summary):
    return [
        {"role": "system", "content": JUDGE_PROMPT},
        {
            "role": "user",
            "content": (
                f"sender: {email['sender']}\n"
                f"subject: {email['subject']}\n"
                f"body: {email['body']}\n"
                f"tier: {customer['tier']}\n"
                f"open_tickets: {customer['open_tickets']}\n"
                f"summary: {summary}"
            ),
        },
    ]


def _priority_from_text(text):
    t = text.strip()
    if t.startswith("{"):
        i = t.index('"priority"') + len('"priority"')
        i = t.index(":", i) + 1
        j = t.index('"', i) + 1
        return Priority(t[j : t.index('"', j)])
    return Priority(t.strip('"').strip("'"))


def judge(email, customer, summary):
    result = llm.chat(
        role="judge",
        messages=judge_messages(email, customer, summary),
        response_schema=Priority,
    )
    return _priority_from_text(result.text)


def render_reply(input, prev, priority):
    body = TEMPLATE.format(
        subject=input["subject"],
        name=prev["crm_lookup"]["name"],
        ticket_id=prev["tickets_create"]["ticket_id"],
        priority=priority,
        sla=SLA_TABLE[prev["crm_lookup"]["tier"]],
    ).rstrip("\n")
    return {"subject": f"Re: {input['subject']}", "body": body}


def validate_run(priority, to, sender):
    Priority(priority)
    if to != sender:
        raise ValueError(f"recipient {to!r} != sender {sender!r}")


def build_args(index, input, prev, run_id, priority=None):
    step = STEPS[index]
    args = {}
    for arg, prov in step["args"].items():
        if prov in ("judge", "template"):
            continue
        if prov.startswith("literal:"):
            args[arg] = _literal(prov)
        elif prov.startswith("input."):
            args[arg] = _dig(input, prov[6:])
        else:
            tool, _, path = prov.partition(".")
            args[arg] = _dig(prev[tool], path)
    if step["tool"] == "tickets_create":
        args["idempotency_key"] = f"{run_id}:{index}"
        if priority is not None:
            args["priority"] = priority
        else:
            customer = prev["crm_lookup"]
            args["priority"] = judge(input, customer, args["summary"])
    if step["tool"] == "outbox_send":
        if priority is None:
            raise ValueError("outbox_send needs priority")
        rendered = render_reply(input, prev, priority)
        args["subject"] = rendered["subject"]
        args["body"] = rendered["body"]
    return args
