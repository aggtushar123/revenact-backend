"""Tasks are personal: the creator's, the assignee's, and their chains'."""

from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.capabilities import Capability
from services.accounts.models import Organisation, Role, User
from services.customers.models import Customer, Task


class TaskVisibilityTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        mk = lambda email, name, **kw: User.objects.create_user(  # noqa: E731
            email=email, password="x", name=name, organisation=self.org, **kw
        )
        self.alice = mk("alice@acme.io", "Alice", role=User.Role.ADMIN)
        self.carl = mk("carl@acme.io", "Carl", reports_to=self.alice)
        self.dana = mk("dana@acme.io", "Dana", reports_to=self.carl)
        self.priya = mk("priya@acme.io", "Priya", reports_to=self.alice)
        lead = Role.objects.create(
            organisation=self.org,
            name="Lead",
            slug="lead",
            permissions=[Capability.VIEW_ALL_ACCOUNTS],
        )
        User.objects.filter(pk__in=[self.carl.pk, self.dana.pk, self.priya.pk]).update(role=lead)
        for person in (self.carl, self.dana, self.priya):
            person.refresh_from_db()
        self.pizza = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", owner=self.carl
        )
        self.url = f"/api/v1/customers/{self.pizza.id}/tasks/"

    def task(self, title, created_by=None, assignee=None):
        return Task.objects.create(
            customer=self.pizza,
            title=title,
            assignee_name=assignee.name if assignee else "Seed",
            created_by=created_by,
            assignee=assignee,
            due_date="2026-09-30",
            priority="medium",
        )

    def seen_by(self, user, url=None):
        self.client.force_authenticate(user)
        return {row["title"] for row in self.client.get(url or self.url).data}

    def test_creator_assignee_and_their_chains_see_a_task_nobody_else(self):
        self.task("seeded")
        self.task("carl for dana", created_by=self.carl, assignee=self.dana)  # handed down
        self.task("dana for herself", created_by=self.dana, assignee=self.dana)
        self.task("alice for carl", created_by=self.alice, assignee=self.carl)
        self.task("priya's own", created_by=self.priya, assignee=self.priya)

        # Dana sees what was handed to her and her own, never Carl's other task.
        self.assertEqual(self.seen_by(self.dana), {"seeded", "carl for dana", "dana for herself"})
        # Carl: his, his report's, and what Alice handed him.
        self.assertEqual(
            self.seen_by(self.carl),
            {"seeded", "carl for dana", "dana for herself", "alice for carl"},
        )
        self.assertEqual(self.seen_by(self.priya), {"seeded", "priya's own"})
        self.assertEqual(len(self.seen_by(self.alice)), 5)
        # The cross-company list follows the same rule.
        self.assertEqual(
            self.seen_by(self.dana, "/api/v1/tasks/"),
            {"seeded", "carl for dana", "dana for herself"},
        )

    def test_creating_a_task_stamps_the_creator_and_defaults_the_assignee_to_them(self):
        self.client.force_authenticate(self.carl)
        handed = self.client.post(
            self.url,
            {
                "title": "Ship the SSO fix",
                "due_date": "2026-09-30",
                "priority": "high",
                "assignee_id": self.dana.id,
            },
            format="json",
        )
        self.assertEqual(handed.status_code, status.HTTP_201_CREATED, handed.data)
        self.assertEqual(handed.data["created_by"]["name"], "Carl")
        self.assertEqual(handed.data["assignee"], {"id": self.dana.id, "name": "Dana"})
        self.assertEqual(handed.data["assignee_name"], "Dana")
        own = self.client.post(
            self.url,
            {"title": "Prep QBR", "due_date": "2026-10-01", "priority": "low"},
            format="json",
        )
        self.assertEqual(own.data["assignee"]["name"], "Carl")
        # Dana sees the one handed to her, not Carl's own.
        self.assertEqual(self.seen_by(self.dana), {"Ship the SSO fix"})
        # An assignee outside the organisation is refused.
        other = Organisation.objects.create(name="Globex")
        stranger = User.objects.create_user(
            email="z@globex.io", password="x", name="Zed", organisation=other
        )
        self.client.force_authenticate(self.carl)
        refused = self.client.post(
            self.url,
            {"title": "x", "due_date": "2026-10-01", "priority": "low", "assignee_id": stranger.id},
            format="json",
        )
        self.assertEqual(refused.status_code, status.HTTP_400_BAD_REQUEST)
