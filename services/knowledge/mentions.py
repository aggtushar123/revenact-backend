"""@mentions: a name — or a function — in a message becomes a question for
the right person.

`@Mei` or `@Mei Tanaka` resolves against the organisation's active members
by first name or full name, case-insensitively; a first name two people
share resolves to nobody rather than to the wrong one. `@engineering`
(`@sales`, `@analytics`, `@cs`, `@leadership`) resolves to whoever is
responsible for the customer in that function and, when nobody is, to
everyone in the function — the asker need not know who owns what. `@team`
is everyone responsible for the customer. The asker never routes a
question to themselves.
"""

import re
from dataclasses import dataclass

from django.utils import timezone

from services.accounts.models import User
from services.notifications.models import Notification
from services.notifications.realtime import notify

from .models import Contribution, Question

MENTION = re.compile(r"@([A-Za-z][A-Za-z'\-]*)(?:\s+([A-Z][A-Za-z'\-]*))?")

# What a function can be called in a mention: the choice values themselves
# plus the words people actually type.
FUNCTION_TOKENS = {
    "cs": User.Function.CS,
    "customer-success": User.Function.CS,
    "customersuccess": User.Function.CS,
    "success": User.Function.CS,
    "engineering": User.Function.ENGINEERING,
    "eng": User.Function.ENGINEERING,
    "engineers": User.Function.ENGINEERING,
    "sales": User.Function.SALES,
    "analytics": User.Function.ANALYTICS,
    "analyst": User.Function.ANALYTICS,
    "analysts": User.Function.ANALYTICS,
    "data": User.Function.ANALYTICS,
    "leadership": User.Function.LEADERSHIP,
    "leaders": User.Function.LEADERSHIP,
}
TEAM_TOKENS = {"team", "responsible", "everyone"}


@dataclass(frozen=True)
class Route:
    """One person a mention reached, and why: `via` is "name" (they were
    named), "owner" (responsible for the customer in the mentioned
    function), "members" (nobody is, so the whole function), or "team"
    (everyone responsible for the customer)."""

    user: User
    token: str
    via: str
    function: str | None = None


def resolve_routes(text, organisation, *, exclude=None, customer=None):
    """Whom `text` @mentions, in order, without duplicates, with the reason
    each was reached. `customer` is what the question is about, when
    known — it decides who "the responsible engineer" is."""
    members = list(User.objects.filter(organisation=organisation, is_active=True))
    routes, seen = [], set()

    def add(user, token, via, function=None):
        if exclude is not None and user.id == exclude.id:
            return
        if user.id in seen:
            return
        seen.add(user.id)
        routes.append(Route(user=user, token=token, via=via, function=function))

    for match in MENTION.finditer(text or ""):
        first, second = match.group(1), match.group(2)
        token = first.lower()
        if token in TEAM_TOKENS:
            for user, function in _responsible(customer):
                add(user, first, "team", function)
            continue
        function = FUNCTION_TOKENS.get(token)
        if function is not None:
            people, via = _function_people(function, members, customer, exclude)
            for user in people:
                add(user, first, via, function)
            continue
        full = f"{first} {second}".lower() if second else None
        by_full = [m for m in members if full and m.name.lower() == full]
        by_first = [m for m in members if m.name.lower().split(" ")[0] == first.lower()]
        candidates = by_full or by_first
        if len(candidates) != 1:
            continue
        add(candidates[0], match.group(0)[1:], "name")
    return routes


def resolve_mentions(text, organisation, *, exclude=None, customer=None):
    """The members `text` @mentions, in order, without duplicates."""
    return [r.user for r in resolve_routes(text, organisation, exclude=exclude, customer=customer)]


def _responsible(customer):
    """(user, function) for everyone responsible for the customer: the
    account owner and each function owner. Nobody without a customer."""
    if customer is None:
        return []
    people = []
    if customer.owner_id and customer.owner.is_active:
        people.append((customer.owner, customer.owner.function))
    for fo in customer.function_owners.select_related("user").order_by("function"):
        if fo.user.is_active:
            people.append((fo.user, fo.function))
    return people


def _function_people(function, members, customer, exclude):
    """The responsible person for `customer` in `function` when there is
    one (for CS, the account owner stands in); otherwise everyone in the
    function. The asker asking their own function reaches their peers."""
    if customer is not None:
        fo = customer.function_owners.filter(function=function).select_related("user").first()
        owner = fo.user if fo is not None else None
        if owner is None and function == User.Function.CS and customer.owner_id:
            owner = customer.owner
        if owner is not None and owner.is_active and (exclude is None or owner.id != exclude.id):
            return [owner], "owner"
    return sorted((m for m in members if m.function == function), key=lambda m: m.name), "members"


def routing_summary(routes, customer=None):
    """Whom the mentions reached and why, for the Copilot's system prompt:
    "Priya Nair (Engineering) — responsible for Acme in Engineering;
    Raj Mehta (Sales), Sam Lee (Sales) — everyone in Sales, since nobody
    is responsible for Acme there yet"."""
    groups = {}
    for route in routes:
        groups.setdefault((route.token.lower(), route.via), []).append(route)
    parts = []
    for (_, via), group in groups.items():
        names = ", ".join(f"{r.user.name} ({r.user.get_function_display()})" for r in group)
        function = User.Function(group[0].function).label if group[0].function else ""
        about = customer.name if customer is not None else "this customer"
        if via == "owner":
            parts.append(f"{names} — responsible for {about} in {function}")
        elif via == "members":
            parts.append(
                f"{names} — everyone in {function}, since nobody is responsible for "
                f"{about} there yet"
            )
        elif via == "team":
            parts.append(f"{names} — everyone responsible for {about}")
        else:
            parts.append(names)
    return "; ".join(parts)


def route_questions(*, organisation, asked_by, text, customer=None, message=None, assignees=None):
    """One Question per person the text mentions (or per explicit assignee),
    each told about it. Returns the questions created."""
    people = (
        assignees
        if assignees is not None
        else resolve_mentions(text, organisation, exclude=asked_by, customer=customer)
    )
    created = []
    for person in people:
        question = Question.objects.create(
            organisation=organisation,
            customer=customer,
            asked_by=asked_by,
            assignee=person,
            text=text.strip(),
            message=message,
        )
        about = f" about {customer.name}" if customer else ""
        notify(
            recipient=person,
            actor=asked_by,
            kind=Notification.Kind.QUESTION_ASKED,
            message=f"{asked_by.name} asked you{about}: {question.text[:120]}",
            link=_link_for(question),
        )
        created.append(question)
    return created


def answer_question(question, answerer, body):
    """Store the answer as a contribution from the answerer's function and
    close the question; the asker is told."""
    contribution = Contribution.objects.create(
        organisation=question.organisation,
        customer=question.customer,
        author=answerer,
        function=answerer.function,
        body=f'In answer to {question.asked_by.name}\'s question "{question.text}": {body.strip()}',
    )
    question.answer = contribution
    question.status = Question.Status.ANSWERED
    question.answered_at = timezone.now()
    question.save(update_fields=["answer", "status", "answered_at"])
    about = f" about {question.customer.name}" if question.customer_id else ""
    notify(
        recipient=question.asked_by,
        actor=answerer,
        kind=Notification.Kind.QUESTION_ANSWERED,
        message=f"{answerer.name} answered your question{about}: {body.strip()[:120]}",
        link=_link_for(question),
    )
    return question


def _link_for(question):
    """Where a notification about the question should land: the chat it was
    asked in, when it was asked in one (the page already opens any
    conversation the viewer may read via `?session=`); otherwise the
    customer's page, where the Questions panel lives."""
    if question.message_id:
        return f"/copilot?session={question.message.conversation_id}"
    if question.customer_id:
        return f"/organizations/{question.customer_id}"
    return "/copilot"


def ask_suggestions_for(company, *, exclude=None):
    """Who could be asked about `company`, for a screen to offer in one
    click: the people responsible for it in each function, the asker
    left out. Empty for an account (responsibility hangs off the
    customer) and for a question about no company in particular."""
    if company is None or company.__class__.__name__ != "Customer":
        return []
    people = {}
    if company.owner_id and (exclude is None or company.owner_id != exclude.id):
        people[company.owner_id] = (company.owner, User.Function.CS)
    for fo in company.function_owners.select_related("user"):
        if exclude is not None and fo.user_id == exclude.id:
            continue
        people.setdefault(fo.user_id, (fo.user, fo.function))
    return [
        {
            "user_id": user.id,
            "name": user.name,
            "function": function,
            "function_display": User.Function(function).label,
            "customer_id": company.id,
            "customer_name": company.name,
        }
        for user, function in people.values()
    ]
