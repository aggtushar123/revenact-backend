"""Is the company writing things down? Per function, over a window.

The brain only knows what people tell it. This is the honest measure of
that: for each function, how many people contributed, how much they
wrote, how many questions they were asked, how many they answered, how
many are still waiting on them, and how long they take. All from rows
that already exist — no model call.
"""

from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from services.accounts.models import User

from .models import Contribution, Question


def by_function(organisation, days=30, now=None):
    now = now or timezone.now()
    since = now - timedelta(days=days)
    contributions = (
        Contribution.objects.filter(organisation=organisation, created_at__gte=since)
        .values("function")
        .annotate(count=Count("id"), people=Count("author", distinct=True))
    )
    written = {row["function"]: row for row in contributions}

    asked = {}
    answered = {}
    waiting = {}
    answer_days = {}
    questions = Question.objects.filter(organisation=organisation).select_related("assignee")
    for q in questions:
        function = q.assignee.function
        if q.created_at >= since:
            asked[function] = asked.get(function, 0) + 1
        if q.status == Question.Status.ANSWERED and q.answered_at and q.answered_at >= since:
            answered[function] = answered.get(function, 0) + 1
            answer_days.setdefault(function, []).append((q.answered_at - q.created_at).days)
        if q.status == Question.Status.OPEN:
            waiting[function] = waiting.get(function, 0) + 1

    members = dict(
        User.objects.filter(organisation=organisation, is_active=True)
        .values_list("function")
        .annotate(n=Count("id"))
    )

    rows = []
    for function, label in User.Function.choices:
        days_list = answer_days.get(function, [])
        rows.append(
            {
                "function": function,
                "label": label,
                "members": members.get(function, 0),
                "contributors": written.get(function, {}).get("people", 0),
                "contributions": written.get(function, {}).get("count", 0),
                "questions_asked": asked.get(function, 0),
                "questions_answered": answered.get(function, 0),
                "questions_waiting": waiting.get(function, 0),
                "avg_days_to_answer": (
                    round(sum(days_list) / len(days_list), 1) if days_list else None
                ),
            }
        )
    return {"days": days, "since": since.date().isoformat(), "functions": rows}
