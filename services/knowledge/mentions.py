"""@mentions: a name in a message becomes a question for that person.

`@Mei` or `@Mei Tanaka` resolves against the organisation's active members
by first name or full name, case-insensitively; a first name two people
share resolves to nobody rather than to the wrong one. The asker cannot
route a question to themselves.
"""

import re

from django.utils import timezone

from services.accounts.models import User
from services.notifications.models import Notification
from services.notifications.realtime import notify

from .models import Contribution, Question

MENTION = re.compile(r"@([A-Za-z][A-Za-z'\-]*)(?:\s+([A-Z][A-Za-z'\-]*))?")


def resolve_mentions(text, organisation, *, exclude=None):
    """The members `text` @mentions, in order, without duplicates."""
    members = list(User.objects.filter(organisation=organisation, is_active=True))
    found, seen = [], set()
    for match in MENTION.finditer(text or ""):
        first, second = match.group(1), match.group(2)
        full = f"{first} {second}".lower() if second else None
        by_full = [m for m in members if full and m.name.lower() == full]
        by_first = [m for m in members if m.name.lower().split(" ")[0] == first.lower()]
        candidates = by_full or by_first
        if len(candidates) != 1:
            continue
        user = candidates[0]
        if exclude is not None and user.id == exclude.id:
            continue
        if user.id not in seen:
            seen.add(user.id)
            found.append(user)
    return found


def route_questions(*, organisation, asked_by, text, customer=None, message=None, assignees=None):
    """One Question per person the text mentions (or per explicit assignee),
    each told about it. Returns the questions created."""
    people = (
        assignees
        if assignees is not None
        else resolve_mentions(text, organisation, exclude=asked_by)
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
            link=f"/organizations/{customer.id}" if customer else "/copilot",
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
        link=f"/organizations/{question.customer_id}" if question.customer_id else "/copilot",
    )
    return question
