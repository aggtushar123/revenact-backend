from datetime import UTC, datetime, time, timedelta

from django.http import Http404
from django.utils import timezone

from services.customers.models import Activity, Customer, Survey, Task
from services.organizations.story.params import NO_ACCOUNT
from services.organizations.story.scope import resolve_scope
from services.organizations.story.sources import SOURCES

from .story_fixtures import StoryFixture

RECORD_KINDS = tuple(SOURCES)


def ids(queryset):
    return set(queryset.values_list("id", flat=True))


class ScopeTests(StoryFixture):
    def test_an_organisation_the_viewer_cannot_open_is_a_404(self):
        danas = self.customer("Dana's", owner=self.other)
        globex = self.customer("Globex's", owner=None, organisation=self.other_org)
        for customer_id in (danas.pk, globex.pk, 999_999):
            with self.assertRaises(Http404):
                resolve_scope(self.csm, customer_id)

    def test_archived_and_churned_organisations_still_open(self):
        gone = self.customer(
            "Gone", is_archived=True, lifecycle_stage=Customer.LifecycleStage.CHURN
        )
        self.assertEqual(resolve_scope(self.csm, gone.pk).customer, gone)

    def test_accounts_are_this_organisation_s_that_the_viewer_may_open(self):
        taco = self.customer("Taco Co")
        self.account("Taco only", customers=[taco])
        accounts = list(self.scope().accounts.items())
        self.assertEqual(accounts, [(self.apac.pk, "APAC"), (self.emea.pk, "EMEA")])
        open_co = self.customer("Open Co", owner=None)
        free = self.account("Free div", customers=[open_co])
        self.account("Dana's div", customers=[open_co], owner=self.other)
        self.assertEqual(self.scope(customer=open_co).accounts, {free.pk: "Free div"})

    def test_parent_q_narrows_to_one_account_or_the_organisation(self):
        on_org, on_emea, on_apac = self.note(self.pizza), self.note(self.emea), self.note(self.apac)
        scope = self.scope()
        rows = self.base("note")
        self.assertEqual(ids(rows), {on_org.pk, on_emea.pk, on_apac.pk})
        self.assertEqual(ids(rows.filter(scope.parent_q(self.emea.pk))), {on_emea.pk})
        self.assertEqual(ids(rows.filter(scope.parent_q(NO_ACCOUNT))), {on_org.pk})
        stranger = self.account("Not ours", customers=[self.customer("Taco Co")])
        self.assertEqual(ids(rows.filter(scope.parent_q(stranger.pk))), set())


class SourceScopeTests(StoryFixture):
    def test_every_source_reads_only_this_organisation_and_its_accounts(self):
        taco = self.customer("Taco Co")
        taco_div = self.account("Taco div", customers=[taco])
        for kind in RECORD_KINDS:
            with self.subTest(kind=kind):
                mine = {self.make(kind, self.pizza).pk, self.make(kind, self.emea).pk}
                self.make(kind, taco)
                self.make(kind, taco_div)
                self.assertEqual(ids(self.base(kind)), mine)

    def test_records_on_an_account_the_viewer_may_not_open_stay_out(self):
        open_co = self.customer("Open Co", owner=None)
        free = self.account("Free div", customers=[open_co])
        danas = self.account("Dana's div", customers=[open_co], owner=self.other)
        for kind in RECORD_KINDS:
            with self.subTest(kind=kind):
                seen = {self.make(kind, open_co).pk, self.make(kind, free).pk}
                self.make(kind, danas)
                self.assertEqual(ids(self.base(kind, customer=open_co)), seen)

    def test_a_shared_account_s_records_belong_to_each_of_its_organisations(self):
        taco = self.customer("Taco Co")
        shared = self.account("Shared div", customers=[self.pizza, taco])
        note = self.note(shared)
        self.assertIn(note.pk, ids(self.base("note")))
        self.assertIn(note.pk, ids(self.base("note", customer=taco)))

    def test_the_other_organisation_s_own_records_stay_out_despite_a_shared_account(self):
        taco = self.customer("Taco Co")
        shared = self.account("Shared div", customers=[self.pizza, taco])
        for kind in RECORD_KINDS:
            with self.subTest(kind=kind):
                on_shared = self.make(kind, shared).pk
                on_taco = self.make(kind, taco).pk
                self.assertEqual(ids(self.base(kind)), {on_shared})
                self.assertEqual(ids(self.base(kind, customer=taco)), {on_shared, on_taco})


class RecordRuleTests(StoryFixture):
    def test_mail_is_its_mailbox_owner_s_and_their_chain_s(self):
        mine = self.email(self.pizza, mailbox_owner=self.csm)
        danas = self.email(self.emea, mailbox_owner=self.other)
        logged = self.email(self.pizza)
        self.assertEqual(ids(self.base("email")), {mine.pk, logged.pk})
        self.other.reports_to = self.csm
        self.other.save(update_fields=["reports_to"])
        self.assertEqual(ids(self.base("email")), {mine.pk, danas.pk, logged.pk})

    def test_a_note_is_its_author_s_and_their_chain_s(self):
        mine = self.note(self.pizza, author=self.csm)
        self.note(self.pizza, author=self.other)
        seeded = self.note(self.emea)
        self.assertEqual(ids(self.base("note")), {mine.pk, seeded.pk})

    def test_a_task_is_its_creator_s_and_its_assignee_s(self):
        handed = self.task(self.pizza, created_by=self.other, assignee=self.csm)
        self.task(self.pizza, created_by=self.other, assignee=self.other)
        seeded = self.task(self.apac)
        self.assertEqual(ids(self.base("task")), {handed.pk, seeded.pk})

    def test_a_ticket_is_its_department_s_and_leadership_reads_all(self):
        everyone = self.ticket(self.pizza)
        ours = self.ticket(self.pizza, department="cs")
        engineering = self.ticket(self.emea, department="engineering")
        self.assertEqual(ids(self.base("ticket")), {everyone.pk, ours.pk})
        self.assertEqual(
            ids(self.base("ticket", user=self.admin)), {everyone.pk, ours.pk, engineering.pk}
        )


class OccurredAtTests(StoryFixture):
    def test_dates_read_as_midnight_utc_and_timestamps_as_themselves(self):
        day = self.days_ago(3)
        midnight = datetime.combine(day, time.min, tzinfo=UTC)
        activity = self.activity(self.pizza, day=day)
        self.assertEqual(self.base("activity").get(pk=activity.pk)._at, midnight)
        sent = timezone.now() - timedelta(hours=5)
        email = self.email(self.pizza, at=sent)
        self.assertEqual(self.base("email").get(pk=email.pk)._at, sent)
        answered = self.survey(
            self.pizza,
            day=self.days_ago(9),
            responded_at=day,
            status=Survey.Status.RESPONDED,
            score=40,
        )
        self.assertEqual(self.base("survey").get(pk=answered.pk)._at, midnight)

    def test_a_task_reads_as_when_it_was_created_not_when_it_is_due(self):
        task = self.task(self.pizza, due=self.today + timedelta(days=30))
        created = Task.objects.get(pk=task.pk).created_at
        self.assertEqual(self.base("task").get(pk=task.pk)._at, created)

    def test_nothing_dated_after_today_is_in_the_story(self):
        today = self.meeting(self.pizza)
        self.meeting(self.pizza, day=self.today + timedelta(days=1))
        self.assertEqual(ids(self.base("calendar_event")), {today.pk})


class SearchTests(StoryFixture):
    def search(self, kind, q):
        return ids(self.base(kind).filter(SOURCES[kind].search_q(q)))

    def test_text_fields_match_case_insensitively(self):
        email = self.email(self.pizza, body="They asked about SSO pricing")
        self.email(self.pizza)
        self.assertEqual(self.search("email", "sso"), {email.pk})
        ticket = self.ticket(self.pizza, number="TKT-1042")
        self.assertEqual(self.search("ticket", "tkt-1042"), {ticket.pk})
        call = self.call(self.pizza, summary="Renewal is at risk")
        self.call(self.pizza)
        self.assertEqual(self.search("call", "AT RISK"), {call.pk})

    def test_activity_and_survey_match_on_their_type_label(self):
        check = self.activity(self.pizza)
        self.activity(self.pizza, activity_type=Activity.ActivityType.ONBOARDING_MILESTONE)
        self.assertEqual(self.search("activity", "health check"), {check.pk})
        nps = self.survey(self.pizza)
        self.survey(self.pizza, survey_type=Survey.SurveyType.CSAT)
        self.assertEqual(self.search("survey", "nps"), {nps.pk})

    def test_no_match_is_nothing_and_no_query_is_everything(self):
        note = self.note(self.pizza)
        self.activity(self.pizza)
        self.assertEqual(self.search("note", "zebra"), set())
        self.assertEqual(self.search("activity", "zebra"), set())
        self.assertEqual(self.search("note", ""), {note.pk})
