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
    "You are a support-triage priority judge.\nInput: one support email "
    "(sender, subject, body) plus customer context (tier, open_tickets).\n"
    "Task: Assign exactly one priority based on impact, scope, and "
    "urgency in the email.\nP1 - Outage / Production Blocked: Complete "
    "failure of core service with no workaround, affecting many/all "
    "users.\n  Examples: production API returning 500s for all requests; "
    "webhook deliveries failing system-wide; checkout/payments "
    "completely down.\nP2 - Degraded or Time-Bound: Partial impairment or "
    "clear deadline/single-user block requiring timely fix.\n  Examples: "
    "export/job stuck for hours past normal runtime; account locked out, "
    "reset not arriving, needs access today; compliance doc needed by "
    "deadline for renewal.\nP3 - Routine: General question, feature "
    "request, or non-urgent admin task with no time pressure.\n  "
    "Examples: invoice line-item clarification (no rush); how-to change "
    "workspace name; SSO or feature availability question.\nUse "
    "subject/body impact and urgency as primary signal; "
    "tier/open_tickets are secondary context only.\nDo not infer details "
    "beyond the provided content.\nOutput exactly one label: P1, P2, or "
    "P3\nNo other text, punctuation, or explanation."
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
    return Priority(t.strip('"'))


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
