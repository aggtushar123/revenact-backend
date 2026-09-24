"""Who may see whose words — the org chart rule on knowledge and chat."""

from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts import hierarchy
from services.accounts.models import Organisation, User
from services.copilot.models import Conversation, Message
from services.customers.models import Customer
from services.knowledge.models import Contribution, Question


class ChartFixture(APITestCase):
    """Alice (leadership) ← Carl (cs) ← Dana (cs); Alice ← Priya (eng), Raj (sales)."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        mk = lambda email, name, function, role=User.Role.CSM, boss=None: User.objects.create_user(  # noqa: E731
            email=email,
            password="x",
            name=name,
            organisation=self.org,
            role=role,
            function=function,
            reports_to=boss,
        )
        self.alice = mk("alice@acme.io", "Alice Admin", "leadership", User.Role.ADMIN)
        self.carl = mk("carl@acme.io", "Carl CSM", "cs", boss=self.alice)
        self.dana = mk("dana@acme.io", "Dana CSM", "cs", boss=self.carl)
        self.priya = mk("priya@acme.io", "Priya Nair", "engineering", boss=self.alice)
        self.raj = mk("raj@acme.io", "Raj Mehta", "sales", boss=self.alice)
        self.pizza = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", owner=self.carl
        )

    def note(self, author, body):
        return Contribution.objects.create(
            organisation=self.org,
            customer=self.pizza,
            author=author,
            function=author.function,
            body=body,
        )


class ScopeTests(ChartFixture):
    def test_scope_is_self_reports_team_and_leadership_above(self):
        self.assertEqual(
            hierarchy.scope_ids(self.alice),
            {self.alice.id, self.carl.id, self.dana.id, self.priya.id, self.raj.id},
        )
        self.assertEqual(
            hierarchy.scope_ids(self.dana), {self.dana.id, self.carl.id, self.alice.id}
        )
        self.assertEqual(hierarchy.scope_ids(self.priya), {self.priya.id, self.alice.id})
        self.assertEqual(
            [a.name for a in hierarchy.ancestors(self.dana)], ["Carl CSM", "Alice Admin"]
        )
        self.assertTrue(hierarchy.would_cycle(self.alice, self.dana))
        self.assertFalse(hierarchy.would_cycle(self.dana, self.priya))

    def test_the_chart_refuses_loops_and_outsiders(self):
        self.client.force_authenticate(self.alice)
        looped = self.client.patch(
            f"/api/v1/auth/users/{self.alice.id}/", {"reports_to_id": self.dana.id}, format="json"
        )
        self.assertEqual(looped.status_code, status.HTTP_400_BAD_REQUEST)
        moved = self.client.patch(
            f"/api/v1/auth/users/{self.dana.id}/", {"reports_to_id": self.priya.id}, format="json"
        )
        self.assertEqual(moved.status_code, status.HTTP_200_OK)
        self.assertEqual(moved.data["reports_to"], {"id": self.priya.id, "name": "Priya Nair"})


class KnowledgeScopeTests(ChartFixture):
    def test_contributions_follow_the_chart(self):
        self.note(self.priya, "eng note")
        self.note(self.raj, "sales note")
        self.note(self.alice, "leadership note")
        self.note(self.dana, "dana note")
        url = f"/api/v1/customers/{self.pizza.id}/contributions/"

        def seen_by(user):
            self.client.force_authenticate(user)
            return sorted(c["body"] for c in self.client.get(url).data)

        self.assertEqual(
            seen_by(self.alice), ["dana note", "eng note", "leadership note", "sales note"]
        )
        self.assertEqual(seen_by(self.priya), ["eng note", "leadership note"])
        # Carl owns Pizza Hut: responsibility grants reading on it, so he sees all.
        self.assertEqual(
            seen_by(self.carl), ["dana note", "eng note", "leadership note", "sales note"]
        )
        self.assertEqual(seen_by(self.dana), ["dana note", "leadership note"])

    def test_an_answer_reaches_the_asker_across_branches(self):
        from services.knowledge import mentions

        q = Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.raj,
            assignee=self.priya,
            text="ETA?",
        )
        mentions.answer_question(q, self.priya, "25 Sep.")
        self.client.force_authenticate(self.raj)
        bodies = [
            c["body"]
            for c in self.client.get(f"/api/v1/customers/{self.pizza.id}/contributions/").data
        ]
        self.assertTrue(any("25 Sep." in b for b in bodies))
        # Dana is on another branch and another team: not hers to read.
        self.client.force_authenticate(self.dana)
        self.assertEqual(
            self.client.get(f"/api/v1/customers/{self.pizza.id}/contributions/").data, []
        )
        self.assertEqual([r["id"] for r in self.client.get("/api/v1/questions/").data], [])
        # Priya was asked: the question is hers even though Raj is outside her scope.
        self.client.force_authenticate(self.priya)
        self.assertEqual([r["id"] for r in self.client.get("/api/v1/questions/").data], [q.id])

    def test_responsibility_grants_reading_on_that_customer_only(self):
        from services.knowledge.models import FunctionOwner

        other = Customer.objects.create(organisation=self.org, name="Uber", owner=self.carl)
        self.note(self.raj, "sales note on pizza hut")
        Contribution.objects.create(
            organisation=self.org,
            customer=other,
            author=self.raj,
            function="sales",
            body="sales note on uber",
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.raj,
            assignee=self.carl,
            text="pizza q",
        )
        FunctionOwner.objects.create(customer=self.pizza, function="engineering", user=self.priya)

        self.client.force_authenticate(self.priya)
        pizza = [
            c["body"]
            for c in self.client.get(f"/api/v1/customers/{self.pizza.id}/contributions/").data
        ]
        self.assertEqual(pizza, ["sales note on pizza hut"])
        uber = self.client.get(f"/api/v1/customers/{other.id}/contributions/").data
        self.assertEqual(uber, [])
        self.assertEqual(
            [q["text"] for q in self.client.get("/api/v1/questions/").data], ["pizza q"]
        )
        # Dana is responsible for nothing here and stays outside.
        self.client.force_authenticate(self.dana)
        self.assertEqual(
            self.client.get(f"/api/v1/customers/{self.pizza.id}/contributions/").data, []
        )

    def test_the_copilot_grounds_only_in_what_the_asker_may_see(self):
        from services.copilot.context import build_grounding

        self.note(self.raj, "SALES-ONLY procurement stalled")
        self.note(self.alice, "LEADERSHIP board wants this kept")
        summary = build_grounding(self.org, self.priya, query="What about Pizza Hut?").summary
        self.assertIn("LEADERSHIP board", summary)
        self.assertNotIn("SALES-ONLY", summary)


class ChatSliceTests(ChartFixture):
    def test_a_mentioned_person_sees_only_their_slice_of_the_conversation(self):
        from services.copilot.views import visible_messages

        conversation = Conversation.objects.create(
            organisation=self.org, user=self.alice, title="Pizza Hut"
        )

        def turn(author, text):
            m = Message.objects.create(
                conversation=conversation, role="user", content=text, author=author
            )
            Message.objects.create(
                conversation=conversation, role="assistant", content=f"re: {text}"
            )
            return m

        turn(self.alice, "Alice opens")
        turn(self.raj, "Raj adds sales detail")
        asked = turn(self.alice, "@Priya Nair can you confirm the fix date?")
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.priya,
            text=asked.content,
            message=asked,
        )
        turn(self.raj, "Raj follows up")

        kept = [m.content for m in visible_messages(conversation, self.priya)]
        self.assertEqual(
            kept,
            [
                "Alice opens",
                "re: Alice opens",
                "@Priya Nair can you confirm the fix date?",
                "re: @Priya Nair can you confirm the fix date?",
            ],
        )
        self.assertEqual(
            [m.content for m in visible_messages(conversation, self.alice)][::2][:4],
            [
                "Alice opens",
                "Raj adds sales detail",
                "@Priya Nair can you confirm the fix date?",
                "Raj follows up",
            ],
        )

        self.client.force_authenticate(self.priya)
        listed = self.client.get("/api/v1/copilot/conversations/").data
        self.assertEqual([c["id"] for c in listed], [conversation.id])
        detail = self.client.get(f"/api/v1/copilot/conversations/{conversation.id}/").data
        self.assertEqual(detail["visibility"], "partial")
        self.assertEqual(len(detail["messages"]), 4)
        self.assertEqual(detail["messages"][0]["author"]["name"], "Alice Admin")

        # Dana was never mentioned: nothing.
        self.client.force_authenticate(self.dana)
        self.assertEqual(
            self.client.get(f"/api/v1/copilot/conversations/{conversation.id}/").status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_a_turn_addressed_to_someone_else_is_not_shown_however_senior_its_author(self):
        from services.copilot.views import visible_messages

        conversation = Conversation.objects.create(
            organisation=self.org, user=self.alice, title="Pizza Hut"
        )
        general = Message.objects.create(
            conversation=conversation, role="user", content="Alice: general", author=self.alice
        )
        to_priya = Message.objects.create(
            conversation=conversation,
            role="user",
            content="@Priya Nair fix date?",
            author=self.alice,
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.priya,
            text=to_priya.content,
            message=to_priya,
        )
        to_raj = Message.objects.create(
            conversation=conversation,
            role="user",
            content="@Raj Mehta procurement?",
            author=self.alice,
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.raj,
            text=to_raj.content,
            message=to_raj,
        )
        Message.objects.create(conversation=conversation, role="assistant", content="re: raj")
        both = Message.objects.create(
            conversation=conversation,
            role="user",
            content="@Priya Nair @Raj Mehta agree?",
            author=self.alice,
        )
        for who in (self.priya, self.raj):
            Question.objects.create(
                organisation=self.org,
                customer=self.pizza,
                asked_by=self.alice,
                assignee=who,
                text=both.content,
                message=both,
            )

        self.assertEqual(
            [m.content for m in visible_messages(conversation, self.priya)],
            ["Alice: general", "@Priya Nair fix date?", "@Priya Nair @Raj Mehta agree?"],
        )
        self.assertEqual(
            [m.content for m in visible_messages(conversation, self.raj)],
            [
                "Alice: general",
                "@Raj Mehta procurement?",
                "re: raj",
                "@Priya Nair @Raj Mehta agree?",
            ],
        )
        self.assertEqual(len(visible_messages(conversation, self.alice)), 5)
        self.assertEqual(general.author, self.alice)

    def test_a_follow_up_from_the_mentioned_person_uses_only_their_slice_as_history(self):
        conversation = Conversation.objects.create(
            organisation=self.org, user=self.alice, title="Pizza Hut"
        )
        Message.objects.create(
            conversation=conversation, role="user", content="SECRET sales turn", author=self.raj
        )
        Message.objects.create(conversation=conversation, role="assistant", content="re: secret")
        asked = Message.objects.create(
            conversation=conversation,
            role="user",
            content="@Priya Nair fix date?",
            author=self.alice,
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.priya,
            text=asked.content,
            message=asked,
        )

        self.client.force_authenticate(self.priya)
        with patch("services.copilot.views.get_completion", return_value="25 Sep.") as call:
            response = self.client.post(
                "/api/v1/copilot/messages/",
                {"conversation_id": conversation.id, "content": "It ships 25 Sep."},
                format="json",
            )

        history = [m["content"] for m in call.call_args.kwargs["messages"]]
        self.assertNotIn("SECRET sales turn", history)
        self.assertIn("@Priya Nair fix date?", history)
        self.assertEqual(response.data["visibility"], "partial")
        self.assertEqual(Message.objects.get(content="It ships 25 Sep.").author, self.priya)


class CustomerPageTests(ChartFixture):
    def test_a_responsible_or_asked_person_can_open_the_customer_page(self):
        from services.knowledge.models import FunctionOwner

        url = f"/api/v1/customers/{self.pizza.id}/"
        # Priya owns nothing and is not a CSM: without a reason, no page.
        self.client.force_authenticate(self.priya)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)
        # Asked about it: the notification's link must open.
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.priya,
            text="?",
        )
        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)
        # Responsible for it, likewise.
        self.client.force_authenticate(self.raj)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)
        FunctionOwner.objects.create(customer=self.pizza, function="sales", user=self.raj)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)


class ReplyRedactionTests(ChartFixture):
    def test_a_reply_that_quotes_records_outside_the_viewers_scope_is_withheld(self):
        from services.copilot.views import REDACTED_REPLY, visible_messages

        sales_note = self.note(self.raj, "SALES-ONLY procurement stalled")
        conversation = Conversation.objects.create(
            organisation=self.org, user=self.alice, title="Pizza Hut"
        )
        asked = Message.objects.create(
            conversation=conversation,
            role="user",
            content="@Priya Nair what is going on?",
            author=self.alice,
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.priya,
            text=asked.content,
            message=asked,
        )
        Message.objects.create(
            conversation=conversation,
            role="assistant",
            content="Raj says procurement stalled.",
            sources=[
                {
                    "type": "contribution",
                    "id": sales_note.id,
                    "label": "Sales · Raj Mehta",
                    "date": "2026-09-13",
                    "company": "Pizza Hut",
                    "company_type": "customer",
                    "company_id": self.pizza.id,
                }
            ],
        )

        # Priya is mentioned but not responsible: the reply quotes a note she may not read.
        kept = visible_messages(conversation, self.priya)
        self.assertEqual([m.content for m in kept], [asked.content, REDACTED_REPLY])
        self.assertEqual(
            Message.objects.get(role="assistant").content, "Raj says procurement stalled."
        )

        # Once responsible for Pizza Hut she may read the note, so the reply is hers too.
        from services.knowledge.models import FunctionOwner

        FunctionOwner.objects.create(customer=self.pizza, function="engineering", user=self.priya)
        kept = visible_messages(conversation, self.priya)
        self.assertEqual(kept[1].content, "Raj says procurement stalled.")

        # The owner always sees her own conversation whole.
        self.assertEqual(
            visible_messages(conversation, self.alice)[1].content, "Raj says procurement stalled."
        )


class DashboardReplyRedactionTests(ChartFixture):
    """A dashboard-sourced reply carries aggregates and company names from
    the asker's whole filtered book, not just the records it cites.

    Fix round 1 on the dashboard Ask Revenact backend (task-8-report.md):
    `_reply_readable_by` requires the asker's whole filtered book to be
    inside the viewer's own visible customers.

    Fix round 2: pairing a reply with "whichever user turn sorts
    immediately before it" fails open under concurrent sends — two
    participants posting at once can interleave a second user turn between
    a reply and the one it actually answers. `Message.reply_to` now names
    the answered turn explicitly, and the asker short-circuit only applies
    when that turn carries dashboard `context` — a context-less reply keeps
    exactly the per-source checks, the asker included.

    Final fix wave (whole-branch review): the book-subset check alone isn't
    enough — a stored, model-written anomaly title/summary is org-wide and
    can name a company outside the reader's book even when the asker's own
    filtered book passes the subset check, so a reader who doesn't see
    everything never reads a reply that could carry one from an asker who
    does; and a dashboard turn with no author (`SET_NULL`, a deleted user)
    fails closed for everyone but the owner, since there's no book at all
    to check."""

    def setUp(self):
        super().setUp()
        # Owned by Carl, same as Pizza Hut, but Priya has no reason (no
        # question, no contribution, no FunctionOwner) to see this one.
        self.secret = Customer.objects.create(
            organisation=self.org, name="Secret Corp", owner=self.carl
        )
        self.conversation = Conversation.objects.create(
            organisation=self.org, user=self.carl, title="Ask Revenact"
        )
        dashboard_context = {
            "surface": "dashboard",
            "area": "overview",
            "view": None,
            "filters": {"owner": "", "lifecycle": "", "customer": ""},
            "focus": None,
        }
        self.asked = Message.objects.create(
            conversation=self.conversation,
            role="user",
            content="@Priya Nair why is at-risk ARR up?",
            author=self.carl,
            context=dashboard_context,
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.carl,
            assignee=self.priya,
            text=self.asked.content,
            message=self.asked,
        )
        self.reply = Message.objects.create(
            conversation=self.conversation,
            role="assistant",
            content="At-risk ARR is up because of renewals across the book.",
            sources=[],
            reply_to=self.asked,
        )

    def test_a_partial_visibility_viewer_cannot_see_the_askers_whole_book(self):
        from services.copilot.views import REDACTED_REPLY, visible_messages

        # Priya is mentioned (so she reads the question) and is even an
        # assignee on Pizza Hut (so she could see that one customer), but
        # Secret Corp — also in Carl's whole-book digest — is not hers.
        kept = visible_messages(self.conversation, self.priya)
        self.assertEqual([m.content for m in kept], [self.asked.content, REDACTED_REPLY])
        self.assertEqual(Message.objects.get(role="assistant").content, self.reply.content)

    def test_a_full_visibility_session_participant_sees_it(self):
        from services.copilot.models import CopilotSession, SessionInvite, SessionParticipant
        from services.copilot.views import visible_messages

        session = CopilotSession.objects.create(
            conversation=self.conversation, status=CopilotSession.Status.LIVE
        )
        SessionInvite.objects.create(
            session=session,
            invited_user=self.dana,
            invited_by=self.carl,
            status=SessionInvite.Status.ACCEPTED,
        )
        SessionParticipant.objects.create(session=session, user=self.dana)

        kept = [m.content for m in visible_messages(self.conversation, self.dana)]
        self.assertEqual(kept, [self.asked.content, self.reply.content])

    def test_the_asker_always_sees_their_own_reply(self):
        from services.copilot.views import visible_messages

        # Carl owns the conversation, so this also exercises the plain
        # owner path — sees_whole_conversation is True either way.
        kept = [m.content for m in visible_messages(self.conversation, self.carl)]
        self.assertEqual(kept, [self.asked.content, self.reply.content])

    def test_a_communications_reply_with_no_context_is_unaffected(self):
        from services.copilot.views import visible_messages

        # A plain follow-up from Alice, who is in Priya's own scope (her
        # manager) but is *not* Priya herself: proves the reply is kept
        # through the ordinary per-source check (empty sources, so trivially
        # readable), not because the viewer happens to be its author.
        Message.objects.create(
            conversation=self.conversation,
            role="user",
            content="Thanks, got it.",
            author=self.alice,
        )
        plain_reply = Message.objects.create(
            conversation=self.conversation,
            role="assistant",
            content="You're welcome.",
            sources=[],
        )
        kept = [m.content for m in visible_messages(self.conversation, self.priya)]
        self.assertIn(plain_reply.content, kept)

    def test_reply_to_pins_the_answered_turn_despite_a_later_turn_from_the_viewer(self):
        """The concurrent-send trap: U_A (Carl, dashboard context), then
        U_B (Priya, no context — as if she posted while Carl's answer was
        still in flight), then R_A, which really answers U_A via
        `reply_to`. Ordering alone would pair R_A with U_B instead — no
        context, so the book check would be skipped, and even if it did
        carry context, U_B's own author is the viewer, so the (dashboard-
        only) asker short-circuit would also wrongly fire. `reply_to`
        must override that mispairing."""
        from services.copilot.views import REDACTED_REPLY, visible_messages

        Message.objects.create(
            conversation=self.conversation, role="user", content="Thanks!", author=self.priya
        )
        concurrent_reply = Message.objects.create(
            conversation=self.conversation,
            role="assistant",
            content="At-risk ARR is up because of renewals across the book, again.",
            sources=[],
            reply_to=self.asked,
        )

        kept = [m.content for m in visible_messages(self.conversation, self.priya)]
        self.assertNotIn(concurrent_reply.content, kept)
        self.assertEqual(kept.count(REDACTED_REPLY), 2)

    def test_a_partial_viewer_who_can_see_the_whole_filtered_book_sees_the_reply(self):
        """Positive case: the same asker, but the screen was filtered down
        to just the one company the viewer can already see — the book
        check is a genuine subset check, not a blanket redaction."""
        from services.copilot.views import visible_messages

        narrow_context = {
            "surface": "dashboard",
            "area": "overview",
            "view": None,
            "filters": {"owner": "", "lifecycle": "", "customer": str(self.pizza.pk)},
            "focus": None,
        }
        asked_narrow = Message.objects.create(
            conversation=self.conversation,
            role="user",
            content="@Priya Nair what about Pizza Hut specifically?",
            author=self.carl,
            context=narrow_context,
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.carl,
            assignee=self.priya,
            text=asked_narrow.content,
            message=asked_narrow,
        )
        narrow_reply = Message.objects.create(
            conversation=self.conversation,
            role="assistant",
            content="Pizza Hut is renewing on schedule.",
            sources=[],
            reply_to=asked_narrow,
        )

        kept = [m.content for m in visible_messages(self.conversation, self.priya)]
        self.assertIn(narrow_reply.content, kept)

    def test_a_mentioned_reader_cannot_see_a_stored_anomaly_title_within_their_own_book(self):
        """I1 (final review): the stored anomaly title/summary
        (services.attention.rules) are org-wide and can name a company
        outside this viewer's book even when the asker's own *filtered*
        book — all the ordinary book-subset check looks at — is entirely
        inside what the reader can see. Alice (leadership, sees everything)
        asks an Overview question filtered down to just Pizza Hut, which
        Priya can already see (book check would pass); the reply is still
        withheld because Alice sees everything and Priya doesn't, and an
        Overview turn can carry a stored anomaly title/summary."""
        from services.copilot.views import REDACTED_REPLY, visible_messages

        overview_context = {
            "surface": "dashboard",
            "area": "overview",
            "view": None,
            "filters": {"owner": "", "lifecycle": "", "customer": str(self.pizza.pk)},
            "focus": None,
        }
        asked = Message.objects.create(
            conversation=self.conversation,
            role="user",
            content="@Priya Nair why is at-risk ARR up?",
            author=self.alice,
            context=overview_context,
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.priya,
            text=asked.content,
            message=asked,
        )
        reply = Message.objects.create(
            conversation=self.conversation,
            role="assistant",
            content="Theirs and Mine both report SSO failures.",
            sources=[],
            reply_to=asked,
        )

        kept = [m.content for m in visible_messages(self.conversation, self.priya)]
        self.assertNotIn(reply.content, kept)
        self.assertIn(REDACTED_REPLY, kept)

    def test_a_sees_everything_reader_is_shown_the_same_reply(self):
        """The same shape of reply as above, but the mentioned reader also
        sees everything (an executive on another branch) — the stored
        title/summary rule protects readers who don't see the whole org,
        not every partial-visibility (mentioned-only) reader indiscriminately."""
        from services.copilot.views import visible_messages

        grace = User.objects.create_user(
            email="grace@acme.io",
            password="x",
            name="Grace Exec",
            organisation=self.org,
            role=User.Role.ADMIN,
            function=User.Function.LEADERSHIP,
            reports_to=self.alice,
        )
        overview_context = {
            "surface": "dashboard",
            "area": "overview",
            "view": None,
            "filters": {"owner": "", "lifecycle": "", "customer": ""},
            "focus": None,
        }
        asked = Message.objects.create(
            conversation=self.conversation,
            role="user",
            content="@Grace Exec why is at-risk ARR up?",
            author=self.alice,
            context=overview_context,
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=grace,
            text=asked.content,
            message=asked,
        )
        reply = Message.objects.create(
            conversation=self.conversation,
            role="assistant",
            content="Theirs and Mine both report SSO failures.",
            sources=[],
            reply_to=asked,
        )

        kept = [m.content for m in visible_messages(self.conversation, grace)]
        self.assertIn(reply.content, kept)

    def test_a_deleted_askers_dashboard_reply_is_unreadable_by_anyone_but_the_owner(self):
        """Null author (final review): `Message.author` is `SET_NULL`, so a
        dashboard turn can end up with no author at all. There is then no
        asker's book to check, so the reply fails closed for everyone —
        except the owner, who never reaches `_reply_readable_by` at all
        (`sees_whole_conversation` short-circuits first)."""
        from services.copilot.views import REDACTED_REPLY, visible_messages

        orphan_context = {
            "surface": "dashboard",
            "area": "overview",
            "view": None,
            "filters": {"owner": "", "lifecycle": "", "customer": ""},
            "focus": None,
        }
        asked = Message.objects.create(
            conversation=self.conversation,
            role="user",
            content="Why is at-risk ARR up?",
            author=None,
            context=orphan_context,
        )
        reply = Message.objects.create(
            conversation=self.conversation,
            role="assistant",
            content="Because renewals cluster next month.",
            sources=[],
            reply_to=asked,
        )

        kept_by_priya = [m.content for m in visible_messages(self.conversation, self.priya)]
        self.assertIn(REDACTED_REPLY, kept_by_priya)
        self.assertNotIn(reply.content, kept_by_priya)

        # The owner still reads it whole regardless of any of the above —
        # sees_whole_conversation short-circuits before _reply_readable_by
        # is ever consulted.
        kept_by_owner = [m.content for m in visible_messages(self.conversation, self.carl)]
        self.assertIn(reply.content, kept_by_owner)


class ManagerSeesTeamTests(ChartFixture):
    """Carl manages Dana. The CEO asks Dana something in a chat; Carl sees
    the ask and the reply. Raj, on another branch, sees nothing."""

    def test_a_manager_sees_what_was_asked_of_a_report_and_what_they_replied(self):
        from services.copilot.views import visible_messages

        conversation = Conversation.objects.create(
            organisation=self.org, user=self.alice, title="Pizza Hut"
        )
        Message.objects.create(
            conversation=conversation, role="user", content="Alice: general", author=self.alice
        )
        to_raj = Message.objects.create(
            conversation=conversation,
            role="user",
            content="@Raj Mehta procurement?",
            author=self.alice,
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.raj,
            text=to_raj.content,
            message=to_raj,
        )
        to_dana = Message.objects.create(
            conversation=conversation,
            role="user",
            content="@Dana CSM when is the QBR?",
            author=self.alice,
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.dana,
            text=to_dana.content,
            message=to_dana,
        )
        Message.objects.create(conversation=conversation, role="assistant", content="re: dana")
        Message.objects.create(
            conversation=conversation,
            role="user",
            content="Dana: QBR is on the 20th.",
            author=self.dana,
        )
        Message.objects.create(
            conversation=conversation, role="assistant", content="re: dana reply"
        )

        self.client.force_authenticate(self.carl)
        self.assertEqual(
            [c["id"] for c in self.client.get("/api/v1/copilot/conversations/").data],
            [conversation.id],
        )
        self.assertEqual(
            [m.content for m in visible_messages(conversation, self.carl)],
            [
                "Alice: general",
                "@Dana CSM when is the QBR?",
                "re: dana",
                "Dana: QBR is on the 20th.",
                "re: dana reply",
            ],
        )
        self.assertEqual(
            [q["text"] for q in self.client.get("/api/v1/questions/").data if "Dana" in q["text"]],
            ["@Dana CSM when is the QBR?"],
        )
        # Raj was asked too, but Dana's part is not his.
        self.assertEqual(
            [m.content for m in visible_messages(conversation, self.raj)],
            ["Alice: general", "@Raj Mehta procurement?"],
        )
        # Priya reports to Alice, not Carl: not a manager of Dana, never mentioned.
        self.client.force_authenticate(self.priya)
        self.assertEqual(self.client.get("/api/v1/copilot/conversations/").data, [])

    def test_a_manager_opens_the_customers_their_reports_own(self):
        # Dana owns nothing here; give her one and Carl, her manager, can open it.
        mine = Customer.objects.create(organisation=self.org, name="Dana's", owner=self.dana)
        self.client.force_authenticate(self.carl)
        self.assertEqual(
            self.client.get(f"/api/v1/customers/{mine.id}/").status_code, status.HTTP_200_OK
        )
        self.client.force_authenticate(self.raj)
        self.assertEqual(
            self.client.get(f"/api/v1/customers/{mine.id}/").status_code, status.HTTP_404_NOT_FOUND
        )
