from contextlib import contextmanager
from datetime import time, timedelta
from decimal import Decimal

from django.db import connection
from django.utils import timezone

from services.accounts.models import User
from services.customers.models import (
    Account,
    Activity,
    CalendarEvent,
    Call,
    Email,
    HealthSnapshot,
    Note,
    Survey,
    Task,
    Ticket,
)
from services.organizations.story.build import build_story
from services.organizations.story.params import parse_story_params
from services.organizations.story.scope import resolve_scope
from services.organizations.story.sources import SOURCES, horizon_for

from .fixtures import PortfolioFixture


@contextmanager
def session_time_zone(name):
    """Run with the Postgres session in another time zone. Django sets it to
    UTC on connect; a pooler or a `DATABASES` `TIME_ZONE` could leave it
    elsewhere, and the story's day-to-timestamp reads must not care."""
    with connection.cursor() as cursor:
        cursor.execute("SET TIME ZONE %s", [name])
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SET TIME ZONE 'UTC'")


class StoryFixture(PortfolioFixture):
    """Carl's Pizza Hut with two unowned accounts, EMEA and APAC, and one maker
    per record kind. Every maker dates its row today unless told otherwise
    and files it on `parent`: the organisation, or one of its accounts."""

    def setUp(self):
        super().setUp()
        self.pizza = self.customer("Pizza Hut")
        self.emea = self.account("EMEA")
        self.apac = self.account("APAC")
        self.engineer = User.objects.create_user(
            email="erin@acme.io",
            password="supersecret1",
            name="Erin Engineer",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.ENGINEERING,
        )

    def account(self, name, *, customers=None, owner=None):
        account = Account.objects.create(name=name, owner=owner)
        account.customers.add(*(customers or [self.pizza]))
        return account

    @staticmethod
    def on(parent):
        return {"account": parent} if isinstance(parent, Account) else {"customer": parent}

    def days_ago(self, days):
        return self.today - timedelta(days=days)

    def activity(
        self, parent, *, day=None, activity_type=Activity.ActivityType.HEALTH_CHECK_REVIEW
    ):
        return Activity.objects.create(
            type=activity_type, occurred_at=day or self.today, **self.on(parent)
        )

    def email(
        self,
        parent,
        *,
        at=None,
        subject="Renewal terms",
        body="Can we talk about the renewal?",
        **fields,
    ):
        return Email.objects.create(
            subject=subject,
            sender_name="Pat Buyer",
            recipient_name="Carl CSM",
            body=body,
            sent_at=at or timezone.now(),
            **self.on(parent),
            **fields,
        )

    def call(
        self, parent, *, at=None, title="Quarterly review", summary="They want SSO.", **fields
    ):
        return Call.objects.create(
            title=title,
            host_name="Carl CSM",
            occurred_at=at or timezone.now(),
            summary=summary,
            **self.on(parent),
            **fields,
        )

    def meeting(self, parent, *, day=None, title="Kickoff", **fields):
        return CalendarEvent.objects.create(
            title=title,
            description="Plan the rollout",
            type=CalendarEvent.EventType.MEETING,
            event_date=day or self.today,
            start_time=time(10),
            end_time=time(11),
            attendee_count=3,
            **self.on(parent),
            **fields,
        )

    def ticket(
        self,
        parent,
        *,
        day=None,
        number="T-1",
        title="Login broken",
        priority=Ticket.Priority.MEDIUM,
        **fields,
    ):
        return Ticket.objects.create(
            ticket_number=number,
            title=title,
            assignee_name="Support",
            priority=priority,
            opened_at=day or self.today,
            **self.on(parent),
            **fields,
        )

    def task(self, parent, *, due=None, title="Send the QBR deck", **fields):
        return Task.objects.create(
            title=title,
            assignee_name="Carl CSM",
            due_date=due or self.today,
            priority=Task.Priority.HIGH,
            **self.on(parent),
            **fields,
        )

    def note(self, parent, *, day=None, title="Champion left", body="Sam moved on.", **fields):
        return Note.objects.create(
            title=title,
            author_name="Carl CSM",
            body=body,
            logged_at=day or self.today,
            **self.on(parent),
            **fields,
        )

    def survey(self, parent, *, day=None, survey_type=Survey.SurveyType.NPS, **fields):
        return Survey.objects.create(
            survey_type=survey_type, sent_at=day or self.today, **self.on(parent), **fields
        )

    def snapshot(self, parent, day, score, *, ai=None, csm=None):
        return HealthSnapshot.objects.create(
            captured_on=day,
            health_score=Decimal(score),
            ai_pulse_value=ai,
            csm_pulse_score=csm,
            **self.on(parent),
        )

    def make(self, kind, parent, **fields):
        makers = {
            "activity": self.activity,
            "calendar_event": self.meeting,
            "call": self.call,
            "email": self.email,
            "note": self.note,
            "survey": self.survey,
            "task": self.task,
            "ticket": self.ticket,
        }
        return makers[kind](parent, **fields)

    def scope(self, user=None, customer=None):
        return resolve_scope(user or self.csm, (customer or self.pizza).pk)

    def base(self, kind, user=None, customer=None):
        user = user or self.csm
        return SOURCES[kind].base(user, self.scope(user, customer), horizon=horizon_for(self.today))

    def story(self, user=None, customer=None, **query):
        user = user or self.csm
        scope = self.scope(user, customer)
        return build_story(user, scope, parse_story_params(query), today=self.today)

    @staticmethod
    def keys(body):
        return [(item["kind"], item["id"]) for item in body["items"]]

    def walk(self, user=None, **query):
        seen, cursor = [], None
        while True:
            body = self.story(user, **query, **({"cursor": cursor} if cursor else {}))
            seen += self.keys(body)
            cursor = body["next_cursor"]
            if cursor is None:
                return seen
