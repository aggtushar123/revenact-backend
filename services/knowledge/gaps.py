"""What the company cannot answer, noticed rather than guessed.

A gap is raised from something that really happened: somebody asked the
Copilot about a company and retrieval had nothing to go on, or a routed
question went stale with nobody answering. Both mean the same thing, and
both are cheap to notice at the moment they occur.

Closing one is writing it down. A Contribution on that customer from the
function that owed the answer fills every open gap for that function, so
the loop ends where the knowledge layer already was.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone

from core import audit

from .aging import STALE_DAYS
from .models import FunctionOwner, KnowledgeGap, Question


def _fingerprint(subject: str) -> str:
    """The same question asked twice, however it was typed."""
    return " ".join(subject.lower().split())[:500]


def _who_should_answer(customer, function):
    if not function:
        return None
    owner = FunctionOwner.objects.filter(customer=customer, function=function).first()
    return owner.user if owner else None


def record_unanswered(*, organisation, customer, question, asked_by=None, function="", source=None):
    """Somebody asked and nobody here knew. Counts a repeat rather than
    raising a second gap, so the list ranks by how often it comes up."""
    if customer is None or not (question or "").strip():
        return None
    fingerprint = _fingerprint(question)
    existing = KnowledgeGap.objects.filter(customer=customer, fingerprint=fingerprint).first()
    if existing is not None:
        if existing.status == KnowledgeGap.Status.OPEN:
            existing.times_asked += 1
            existing.last_asked_at = timezone.now()
            existing.save(update_fields=["times_asked", "last_asked_at"])
        return existing
    try:
        with transaction.atomic():
            return KnowledgeGap.objects.create(
                organisation=organisation,
                customer=customer,
                subject=question.strip()[:500],
                fingerprint=fingerprint,
                function=function or "",
                assignee=_who_should_answer(customer, function),
                source=source or KnowledgeGap.Source.COPILOT,
            )
    except IntegrityError:
        # Another request raised the same gap a moment ago, which is the
        # same outcome as raising it here.
        return KnowledgeGap.objects.filter(customer=customer, fingerprint=fingerprint).first()


def raise_from_stale_questions(organisation=None, days=STALE_DAYS, now=None) -> int:
    """Routed questions still open after `days` become gaps: the person
    asked has been reminded and it is now the company's problem, not a
    reminder problem. One gap per question, ever."""
    now = now or timezone.now()
    questions = (
        Question.objects.filter(status=Question.Status.OPEN, gap__isnull=True)
        .filter(created_at__lte=now - timezone.timedelta(days=days))
        .select_related("customer", "assignee")
    )
    if organisation is not None:
        questions = questions.filter(organisation=organisation)
    raised = 0
    for question in questions:
        if question.customer is None:
            continue
        function = question.assignee.function if question.assignee_id else ""
        gap = record_unanswered(
            organisation=question.organisation,
            customer=question.customer,
            question=question.text,
            asked_by=question.asked_by,
            function=function,
            source=KnowledgeGap.Source.QUESTION,
        )
        if gap is None:
            continue
        if gap.question_id is None:
            gap.question = question
            gap.assignee = gap.assignee or question.assignee
            gap.save(update_fields=["question", "assignee"])
            raised += 1
    return raised


def fill_from_contribution(contribution, request=None) -> int:
    """A person wrote down what they know: every open gap on that customer
    owed by their function is answered. Their own words are the answer, so
    nothing here reads or copies them."""
    gaps = KnowledgeGap.objects.filter(
        customer=contribution.customer,
        status=KnowledgeGap.Status.OPEN,
    ).filter(function__in=["", contribution.function])
    filled = 0
    for gap in gaps:
        gap.status = KnowledgeGap.Status.FILLED
        gap.filled_by = contribution
        gap.save(update_fields=["status", "filled_by"])
        audit.record(
            "knowledge.gap_filled",
            request=request,
            actor=contribution.author,
            organisation=contribution.organisation,
            target=gap,
            metadata={"subject": gap.subject, "customer": gap.customer.name},
        )
        filled += 1
    return filled
