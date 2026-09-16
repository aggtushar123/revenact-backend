"""Notes are personal: the author's and their management chain's."""

from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.capabilities import Capability
from services.accounts.models import Organisation, Role, User
from services.customers.models import Customer, Note


class Fixture(APITestCase):
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
        self.url = f"/api/v1/customers/{self.pizza.id}/notes/"

    def note(self, author, title):
        return Note.objects.create(
            customer=self.pizza,
            title=title,
            author_name=author.name if author else "Seed",
            author=author,
            body="…",
            logged_at="2026-09-16",
        )


class NoteVisibilityTests(Fixture):
    def test_you_read_yours_and_your_reports_never_your_seniors_or_peers(self):
        self.note(None, "seeded")
        self.note(self.dana, "dana")
        self.note(self.carl, "carl")
        self.note(self.alice, "alice")
        self.note(self.priya, "priya")

        def seen_by(user):
            self.client.force_authenticate(user)
            return {row["title"] for row in self.client.get(self.url).data}

        self.assertEqual(seen_by(self.dana), {"seeded", "dana"})
        self.assertEqual(seen_by(self.carl), {"seeded", "dana", "carl"})
        self.assertEqual(seen_by(self.alice), {"seeded", "dana", "carl", "alice", "priya"})
        self.assertEqual(seen_by(self.priya), {"seeded", "priya"})

    def test_writing_a_note_stamps_the_author_and_dates_it_today(self):
        self.client.force_authenticate(self.dana)
        response = self.client.post(
            self.url, {"title": "Call with Sam", "body": "Wants a discount."}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["author"], {"id": self.dana.id, "name": "Dana"})
        self.assertEqual(response.data["author_name"], "Dana")
        note = Note.objects.get(title="Call with Sam")
        self.assertEqual((note.author, note.customer), (self.dana, self.pizza))
        self.assertIsNotNone(note.logged_at)
        # Her manager reads it; her peer does not.
        self.client.force_authenticate(self.carl)
        self.assertEqual([r["title"] for r in self.client.get(self.url).data], ["Call with Sam"])
        self.client.force_authenticate(self.priya)
        self.assertEqual(self.client.get(self.url).data, [])

    def test_the_copilot_and_headlines_follow_the_rule(self):
        from services.copilot.models import Message as Turn
        from services.copilot.retrieval import _gather_candidates
        from services.copilot.views import _reply_readable_by
        from services.customers.headline_generation import collect_records

        carl_note = self.note(self.carl, "Carl's private note")
        self.note(self.dana, "Dana's note")
        self.note(None, "Shared seed note")
        for_dana = [i.line for i in _gather_candidates(self.pizza, viewer=self.dana)]
        self.assertTrue(any("Dana's note" in line for line in for_dana))
        self.assertTrue(any("Shared seed note" in line for line in for_dana))
        self.assertFalse(any("Carl's private note" in line for line in for_dana))

        turn = Turn(
            sources=[
                {
                    "type": "note",
                    "id": carl_note.id,
                    "company_type": "customer",
                    "company_id": self.pizza.id,
                }
            ]
        )
        self.assertFalse(_reply_readable_by(turn, self.dana))
        self.assertTrue(_reply_readable_by(turn, self.alice))

        # Headlines are read by everyone: only shared notes feed them.
        collected = collect_records(self.pizza, window_days=30)
        notes_text = "\n".join(str(v) for k, v in collected.items() if "note" in str(k).lower())
        self.assertIn("Shared seed note", notes_text)
        self.assertNotIn("Carl's private note", notes_text)
