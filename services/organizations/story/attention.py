"""Needs attention: the block pinned above the stream (spec §1.6).

Each entry reuses a rule another screen owns, read under the same record
rules as the stream: tickets and tasks come from the story's own base
querysets (`visible_tickets`, `visible_tasks`); questions from the knowledge
layer's `visible_questions`; the anomaly from evidence the viewer may read
(`personal.readable_evidence_q`, the rule `anomalies.views.visible_evidence`
applies), titled by `title_for`, so only a viewer who sees everything reads
the model-written title. The summary is never returned.

It follows `account` (tickets, tasks, anomaly evidence) and nothing else: the
renewal is the organisation's, a question names no account, and a search or
a group must never hide the block.
"""

from django.db.models import Count, Min

from services.attention.rules import SUPPORT_PRIORITIES
from services.customers.models import Customer, Task, Ticket

#: The spec's window (§1.6), shorter than the Dashboard's 90 days.
RENEWAL_WINDOW_DAYS = 30


def renewal(customer, today):
    churned = (
        customer.churn_date is not None or customer.lifecycle_stage == Customer.LifecycleStage.CHURN
    )
    if churned or customer.renewal_date is None:
        return None
    days = (customer.renewal_date - today).days
    if days > RENEWAL_WINDOW_DAYS:
        return None
    return {"date": customer.renewal_date.isoformat(), "days": days, "overdue": days < 0}


def _aged(row, today):
    if not row["count"]:
        return None
    return {"count": row["count"], "oldest_days": max(0, (today - row["oldest"]).days)}


def urgent_tickets(tickets, today):
    row = (
        tickets.filter(priority__in=SUPPORT_PRIORITIES)
        .exclude(status__in=Ticket.RESOLVED_STATUSES)
        .aggregate(count=Count("id"), oldest=Min("opened_at"))
    )
    return _aged(row, today)


def overdue_tasks(tasks, today):
    row = (
        tasks.exclude(status=Task.Status.COMPLETED)
        .filter(due_date__lt=today)
        .aggregate(count=Count("id"), oldest=Min("due_date"))
    )
    return _aged(row, today)


def open_questions(user, customer):
    # Imported here: knowledge.views is a views module with a wide import tree.
    from services.knowledge.models import Question
    from services.knowledge.views import visible_questions

    # SOC2:AUTH-02 the knowledge layer's own rule for questions
    count = visible_questions(
        user,
        Question.objects.filter(
            organisation_id=customer.organisation_id,
            customer=customer,
            status=Question.Status.OPEN,
        ),
    ).count()
    return {"count": count} if count else None


def latest_anomaly(user, scope, account):
    from services.anomalies.models import Anomaly, AnomalyEvidence
    from services.anomalies.views import title_for
    from services.customers.personal import readable_evidence_q
    from services.customers.scoping import sees_everything

    row = (
        AnomalyEvidence.objects.filter(
            organisation_id=scope.customer.organisation_id,
            anomaly__status=Anomaly.Status.LIVE,
        )
        .filter(scope.parent_q(account))
        # SOC2:AUTH-02 evidence is read under the rule of the record it copies
        .filter(
            readable_evidence_q(
                user,
                email=AnomalyEvidence.Kind.EMAIL,
                ticket=AnomalyEvidence.Kind.TICKET,
                call=AnomalyEvidence.Kind.CALL,
            )
        )
        .select_related("anomaly")
        .order_by("-anomaly__last_seen_at", "-anomaly_id")
        .first()
    )
    if row is None:
        return None
    anomaly = row.anomaly
    return {
        "id": anomaly.pk,
        "title": title_for(anomaly.title, 1, sees_all=sees_everything(user)),
        "first_seen_at": anomaly.first_seen_at.isoformat(),
        "last_seen_at": anomaly.last_seen_at.isoformat(),
    }


def build_attention(user, scope, bases, account, *, today):
    return {
        "renewal": renewal(scope.customer, today),
        "tickets": urgent_tickets(bases["ticket"].filter(scope.parent_q(account)), today),
        "overdue_tasks": overdue_tasks(bases["task"].filter(scope.parent_q(account)), today),
        "questions": open_questions(user, scope.customer),
        "anomaly": latest_anomaly(user, scope, account),
    }
