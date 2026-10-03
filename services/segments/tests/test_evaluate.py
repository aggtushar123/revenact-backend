"""Members are computed for the viewer: the rules over their own book, pins
added, exclusions removed, visibility last. A shared viewer learns how many
of the owner's members they cannot open, and nothing else about them."""

from datetime import timedelta
from decimal import Decimal

from services.accounts.models import User
from services.customers.models import Account, Contact, Customer
from services.customers.tests.test_views import blind_to_one_account
from services.segments.evaluate import (
    Draft,
    hidden_count,
    member_ids,
    members_queryset,
    openable_ids,
    visible_records,
)
from services.segments.tests.fixtures import SegmentFixture, person, rule

HEALTHY = rule("health_score", "gt", 0)
NOBODY = rule("health_score", "gt", 100)


class MembershipTests(SegmentFixture):
    def ids(self, draft, user=None):
        return member_ids(draft, user or self.admin, today=self.today)

    def test_the_rules_run_over_the_viewers_own_book(self):
        draft = Draft("customer", HEALTHY)
        self.assertEqual(self.ids(draft), sorted([self.pizza.pk, self.taco.pk]))
        self.assertEqual(self.ids(draft, self.csm), [self.pizza.pk])
        self.assertEqual(self.ids(draft, self.stranger), [self.globex.pk])

    def test_a_managers_segment_covers_their_reports_records(self):
        manager = person("mia@acme.io", "Mia Manager", self.org)
        draft = Draft("customer", HEALTHY)
        self.assertEqual(self.ids(draft, manager), [])
        User.objects.filter(pk=self.other.pk).update(reports_to=manager)
        # A fresh instance: the org chart is memoised per user object.
        self.assertEqual(self.ids(draft, User.objects.get(pk=manager.pk)), [self.taco.pk])

    def test_pins_are_added_whatever_the_rules_say(self):
        self.assertEqual(self.ids(Draft("customer", NOBODY, [self.taco.pk])), [self.taco.pk])

    def test_exclusions_win_over_rules_and_pins(self):
        draft = Draft("customer", HEALTHY, [self.taco.pk], [self.taco.pk])
        self.assertEqual(self.ids(draft), [self.pizza.pk])

    def test_a_pinned_churned_organisation_stays_in(self):
        gone = Customer.objects.create(
            organisation=self.org, name="Gone", churn_date=self.today - timedelta(days=1),
            health_score=Decimal("5.0"), owner=self.csm,
        )  # fmt: skip
        self.assertNotIn(gone.pk, self.ids(Draft("customer", HEALTHY)))
        self.assertIn(gone.pk, self.ids(Draft("customer", HEALTHY, [gone.pk])))

    def test_visibility_is_applied_last_so_a_hidden_pin_is_not_the_viewers(self):
        draft = Draft("customer", NOBODY, [self.taco.pk, self.globex.pk])
        self.assertEqual(self.ids(draft, self.csm), [])
        self.assertEqual(self.ids(draft, self.other), [self.taco.pk])

    def test_blind_to_one_account(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        draft = Draft("account", HEALTHY, [hidden.pk])
        self.assertEqual(self.ids(draft, viewer), [seen.pk])

    def test_another_tenant_never_appears(self):
        draft = Draft("customer", HEALTHY, [self.globex.pk])
        self.assertNotIn(self.globex.pk, self.ids(draft))

    def test_contacts_follow_their_parents_visibility(self):
        sam = Contact.objects.create(customer=self.pizza, name="Sam", email="sam@pizza.io")
        tom = Contact.objects.create(account=self.west, name="Tom", email="tom@taco.io")
        draft = Draft("contact", rule("status", "is", "active"))
        self.assertEqual(self.ids(draft, self.csm), [sam.pk])
        self.assertEqual(self.ids(draft, self.other), [tom.pk])

    def test_members_queryset_is_the_kinds_own_model(self):
        members = members_queryset(Draft("account", HEALTHY), self.admin, today=self.today)
        self.assertIs(members.model, Account)


class HiddenCountTests(SegmentFixture):
    def test_a_shared_viewer_counts_the_owners_members_they_cannot_open(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY, sharing="workspace")
        self.assertEqual(member_ids(segment, self.csm, today=self.today), [self.pizza.pk])
        self.assertEqual(hidden_count(segment, self.csm, today=self.today), 1)
        self.assertEqual(hidden_count(segment, self.other, today=self.today), 1)

    def test_hidden_pins_are_counted_not_named(self):
        segment = self.segment(owner=self.csm, rules=NOBODY, pinned_ids=[self.pizza.pk])
        self.assertEqual(member_ids(segment, self.other, today=self.today), [])
        self.assertEqual(hidden_count(segment, self.other, today=self.today), 1)

    def test_the_owner_hides_nothing_from_themselves(self):
        segment = self.segment(owner=self.csm, rules=HEALTHY)
        self.assertEqual(hidden_count(segment, self.csm, today=self.today), 0)

    def test_a_record_the_viewer_sees_but_the_owner_does_not_is_the_viewers(self):
        segment = self.segment(owner=self.csm, rules=HEALTHY, sharing="workspace")
        self.assertEqual(
            member_ids(segment, self.admin, today=self.today), sorted([self.pizza.pk, self.taco.pk])
        )
        self.assertEqual(hidden_count(segment, self.admin, today=self.today), 0)

    def test_it_is_one_query(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY, sharing="workspace")
        viewer = User.objects.get(pk=self.csm.pk)
        # A request has already loaded both people's capabilities and org chart
        # (memoised on each instance); what is left is the count itself.
        visible_records("customer", viewer)
        visible_records("customer", segment.owner)
        with self.assertNumQueries(1):
            self.assertEqual(hidden_count(segment, viewer, today=self.today), 1)


class OpenableIdsTests(SegmentFixture):
    def test_only_what_the_reader_may_open_sorted(self):
        ids = [self.taco.pk, self.pizza.pk, self.globex.pk, 999999]
        self.assertEqual(openable_ids("customer", self.csm, ids), [self.pizza.pk])
        self.assertEqual(
            openable_ids("customer", self.admin, ids), sorted([self.pizza.pk, self.taco.pk])
        )
        with self.assertNumQueries(0):
            self.assertEqual(openable_ids("customer", self.admin, []), [])

    def test_visible_records_per_kind(self):
        self.assertEqual(
            set(visible_records("account", self.csm).values_list("pk", flat=True)), {self.emea.pk}
        )
