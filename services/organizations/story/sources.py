"""Which rows each story source reads: the second half of the twice-filter.

A row is read when it is filed on this organisation or one of its accounts in
scope (`Scope.parent_q`) **and** its own record rule admits it, the rule its
per-type endpoint applies: mail is its mailbox owner's and their chain's,
notes their author's and their chain's, tasks their creator's and assignee's
and the chains above them, tickets their department's. Activities, calls,
meetings and surveys carry no author, mailbox or department, so the
organisation's rule is their whole rule, as on their endpoints (#64 gated
activities exactly so).

Items, counts, the attention block and search all start from `Source.base`,
so none of them can read a row the others would not.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from django.db.models import DateTimeField, F, Func, Q
from django.db.models.functions import Coalesce

from services.customers.models import (
    Activity,
    CalendarEvent,
    Call,
    Email,
    Note,
    Survey,
    Task,
    Ticket,
)
from services.customers.personal import visible_notes, visible_tasks, visible_tickets
from services.mail.visibility import visible_emails


def _open_to_the_organisation(user, queryset):
    """No record rule beyond the organisation's: see the module docstring."""
    return queryset


class UTCMidnight(Func):
    """A date as the story's timestamp: that day's midnight in UTC.

    Spelled out in SQL rather than `Cast(date, DateTimeField())`: Postgres
    casts a date to `timestamptz` at midnight in the *session's* time zone.
    Django sets that to UTC on connect, but a pooler or a `DATABASES`
    `TIME_ZONE` could leave it elsewhere, and then the horizon and the cursor
    cut would move by the offset. `date::timestamp AT TIME ZONE 'UTC'` does not
    read the session's zone at all, so every comparison made in SQL holds.
    (Reading a `timestamptz` back into Python still relies on Django's own
    invariant that the session is UTC, for every model in the project.)"""

    template = "((%(expressions)s)::timestamp AT TIME ZONE 'UTC')"
    output_field = DateTimeField()


def _day(expression):
    return UTCMidnight(expression)


def _labels(choices, q):
    needle = q.casefold()
    return [value for value, label in choices if needle in str(label).casefold()]


def horizon_for(today: date) -> datetime:
    """The first moment after `today`. Nothing dated from then on has
    happened yet, so it is not story (an upcoming meeting, a future-dated
    ticket)."""
    return datetime.combine(today + timedelta(days=1), time.min, tzinfo=UTC)


@dataclass(frozen=True)
class Source:
    kind: str
    model: type
    #: The sort key, annotated as `_at`: always a timestamp.
    at: object
    #: True when the model stores only a date.
    all_day: bool
    rule: Callable
    text_fields: tuple[str, ...] = ()
    choice_field: str = ""
    choices: tuple = ()
    related: tuple[str, ...] = ()

    def base(self, user, scope, *, horizon):
        rows = self.model.objects.filter(scope.parent_q())
        # SOC2:AUTH-02 each record's own rule, after the organisation's
        return self.rule(user, rows).annotate(_at=self.at).filter(_at__lt=horizon)

    def search_q(self, q) -> Q:
        if not q:
            return Q()
        match = Q(pk__in=[])
        for field in self.text_fields:
            match |= Q(**{f"{field}__icontains": q})
        if self.choice_field:
            match |= Q(**{f"{self.choice_field}__in": _labels(self.choices, q)})
        return match


SOURCES = {
    "activity": Source(
        kind="activity",
        model=Activity,
        at=_day("occurred_at"),
        all_day=True,
        rule=_open_to_the_organisation,
        choice_field="type",
        choices=tuple(Activity.ActivityType.choices),
    ),
    "calendar_event": Source(
        kind="calendar_event",
        model=CalendarEvent,
        at=_day("event_date"),
        all_day=True,
        rule=_open_to_the_organisation,
        text_fields=("title", "description"),
    ),
    "call": Source(
        kind="call",
        model=Call,
        at=F("occurred_at"),
        all_day=False,
        rule=_open_to_the_organisation,
        text_fields=("title", "summary", "host_name"),
        related=("connector",),
    ),
    "email": Source(
        kind="email",
        model=Email,
        at=F("sent_at"),
        all_day=False,
        rule=visible_emails,
        text_fields=("subject", "body", "sender_name", "recipient_name"),
        related=("mailbox", "mailbox_owner"),
    ),
    "note": Source(
        kind="note",
        model=Note,
        at=_day("logged_at"),
        all_day=True,
        rule=visible_notes,
        text_fields=("title", "body", "author_name"),
        related=("author",),
    ),
    "survey": Source(
        kind="survey",
        model=Survey,
        at=_day(Coalesce("responded_at", "sent_at")),
        all_day=True,
        rule=_open_to_the_organisation,
        choice_field="survey_type",
        choices=tuple(Survey.SurveyType.choices),
    ),
    "task": Source(
        kind="task",
        model=Task,
        at=F("created_at"),
        all_day=False,
        rule=visible_tasks,
        text_fields=("title", "assignee_name"),
        related=("assignee",),
    ),
    "ticket": Source(
        kind="ticket",
        model=Ticket,
        at=_day("opened_at"),
        all_day=True,
        rule=visible_tickets,
        text_fields=("title", "ticket_number", "description", "requester_name"),
        related=("connector",),
    ),
}
