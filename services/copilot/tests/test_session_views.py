"""Integration tier, through the real URLconf + real test DB — Phase 2a's
own permission matrix (owner / accepted+active participant / pending-only
invitee / unrelated user) is the part of this feature actually worth being
paranoid about, since it's a real access-control change to who can read and
post into someone else's Conversation. The Anthropic API is never called
here — these endpoints don't touch it."""

from unittest.mock import patch

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.copilot.models import (
    Conversation,
    CopilotSession,
    Message,
    SessionEvent,
    SessionInvite,
    SessionParticipant,
)
from services.customers.models import Customer
from services.notifications.models import Notification


class SessionViewTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.owner = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.teammate = User.objects.create_user(
            email="bob@acme.io", password="supersecret1", name="Bob", organisation=self.org
        )
        self.stranger = User.objects.create_user(
            email="carl@acme.io", password="supersecret1", name="Carl", organisation=self.org
        )
        other_org = Organisation.objects.create(name="Globex")
        self.outsider = User.objects.create_user(
            email="dana@globex.io", password="supersecret1", name="Dana", organisation=other_org
        )
        self.conversation = Conversation.objects.create(
            organisation=self.org, user=self.owner, title="Renewal risk"
        )

    def _url(self, suffix=""):
        return f"/api/v1/copilot/conversations/{self.conversation.id}/session/{suffix}"

    def test_an_accepted_participant_sees_the_conversation_in_their_own_list(self):
        session = CopilotSession.objects.create(
            conversation=self.conversation, status=CopilotSession.Status.LIVE
        )
        SessionInvite.objects.create(
            session=session,
            invited_user=self.teammate,
            invited_by=self.owner,
            status=SessionInvite.Status.ACCEPTED,
        )
        SessionParticipant.objects.create(session=session, user=self.teammate)

        self.client.force_authenticate(self.teammate)
        listed = self.client.get("/api/v1/copilot/conversations/")
        self.assertEqual([c["id"] for c in listed.data], [self.conversation.id])

        # Not for someone merely invited, and not for a stranger.
        self.client.force_authenticate(self.stranger)
        self.assertEqual(self.client.get("/api/v1/copilot/conversations/").data, [])

    def test_get_404s_when_no_session_exists_yet(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_only_the_owner_can_make_a_session_live(self):
        self.client.force_authenticate(self.teammate)
        response = self.client.post(self._url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(CopilotSession.objects.filter(conversation=self.conversation).exists())

    def test_owner_making_it_live_creates_the_session_and_joins_as_participant(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(self._url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "live")
        session = CopilotSession.objects.get(conversation=self.conversation)
        self.assertTrue(
            SessionParticipant.objects.filter(
                session=session, user=self.owner, left_at__isnull=True
            ).exists()
        )
        self.assertTrue(
            session.events.filter(kind=SessionEvent.Kind.MADE_LIVE, actor=self.owner).exists()
        )

    def test_make_live_captures_real_customer_context_when_given(self):
        customer = Customer.objects.create(organisation=self.org, name="Globex")
        self.client.force_authenticate(self.owner)

        response = self.client.post(self._url(), {"customer_id": customer.id}, format="json")

        self.assertEqual(response.data["customer_id"], customer.id)
        self.assertEqual(response.data["customer_name"], "Globex")

    def test_a_closed_session_refuses_to_be_reopened_via_post(self):
        self.client.force_authenticate(self.owner)
        self.client.post(self._url())
        self.client.post(self._url("close/"))

        response = self.client.post(self._url())
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_stranger_cant_get_a_session_they_have_no_invite_to(self):
        self.client.force_authenticate(self.owner)
        self.client.post(self._url())

        self.client.force_authenticate(self.stranger)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_an_accepted_active_participant_can_get_the_session(self):
        self.client.force_authenticate(self.owner)
        self.client.post(self._url())
        self.client.post(self._url("invite/"), {"user_id": self.teammate.id}, format="json")

        session = CopilotSession.objects.get(conversation=self.conversation)
        invite = session.invites.get(invited_user=self.teammate)
        self.client.force_authenticate(self.teammate)
        self.client.post(
            f"/api/v1/copilot/sessions/invites/{invite.id}/respond/",
            {"status": "accepted"},
            format="json",
        )

        response = self.client.get(self._url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_an_invited_but_not_yet_accepted_user_still_cant_get_the_session(self):
        self.client.force_authenticate(self.owner)
        self.client.post(self._url())
        self.client.post(self._url("invite/"), {"user_id": self.teammate.id}, format="json")

        self.client.force_authenticate(self.teammate)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_participant_who_left_loses_access_again(self):
        self.client.force_authenticate(self.owner)
        self.client.post(self._url())
        self.client.post(self._url("invite/"), {"user_id": self.teammate.id}, format="json")
        session = CopilotSession.objects.get(conversation=self.conversation)
        invite = session.invites.get(invited_user=self.teammate)
        self.client.force_authenticate(self.teammate)
        self.client.post(
            f"/api/v1/copilot/sessions/invites/{invite.id}/respond/",
            {"status": "accepted"},
            format="json",
        )
        SessionParticipant.objects.filter(session=session, user=self.teammate).update(
            left_at=timezone.now()
        )

        response = self.client.get(self._url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_events_since_id_only_returns_newer_events(self):
        self.client.force_authenticate(self.owner)
        self.client.post(self._url())
        session = CopilotSession.objects.get(conversation=self.conversation)
        first_event_id = session.events.first().id
        SessionEvent.objects.create(
            session=session, kind=SessionEvent.Kind.JOINED, actor=self.owner
        )

        response = self.client.get(self._url() + f"?since_id={first_event_id}")

        self.assertEqual(len(response.data["events"]), 1)
        self.assertEqual(response.data["events"][0]["kind"], "joined")

    def test_since_id_must_be_an_integer(self):
        self.client.force_authenticate(self.owner)
        self.client.post(self._url())
        response = self.client.get(self._url() + "?since_id=not-a-number")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cant_invite_someone_from_another_organisation(self):
        self.client.force_authenticate(self.owner)
        self.client.post(self._url())
        response = self.client.post(
            self._url("invite/"), {"user_id": self.outsider.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_only_the_owner_can_invite(self):
        self.client.force_authenticate(self.owner)
        self.client.post(self._url())
        self.client.force_authenticate(self.teammate)
        response = self.client.post(
            self._url("invite/"), {"user_id": self.stranger.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_inviting_someone_sends_them_a_real_notification(self):
        customer = Customer.objects.create(organisation=self.org, name="Pizza Hut")
        self.client.force_authenticate(self.owner)
        self.client.post(self._url(), {"customer_id": customer.id}, format="json")

        self.client.post(self._url("invite/"), {"user_id": self.teammate.id}, format="json")

        notification = Notification.objects.get(recipient=self.teammate)
        self.assertEqual(notification.kind, Notification.Kind.COPILOT_INVITE)
        self.assertEqual(notification.actor, self.owner)
        self.assertIn("Pizza Hut", notification.message)
        self.assertEqual(notification.link, f"/copilot?session={self.conversation.id}")

    def test_reinviting_a_declined_user_resets_them_to_pending(self):
        self.client.force_authenticate(self.owner)
        self.client.post(self._url())
        self.client.post(self._url("invite/"), {"user_id": self.teammate.id}, format="json")
        session = CopilotSession.objects.get(conversation=self.conversation)
        invite = session.invites.get(invited_user=self.teammate)
        self.client.force_authenticate(self.teammate)
        self.client.post(
            f"/api/v1/copilot/sessions/invites/{invite.id}/respond/",
            {"status": "declined"},
            format="json",
        )

        self.client.force_authenticate(self.owner)
        self.client.post(self._url("invite/"), {"user_id": self.teammate.id}, format="json")

        invite.refresh_from_db()
        self.assertEqual(invite.status, SessionInvite.Status.PENDING)

    def test_a_participant_not_just_the_owner_can_hand_off(self):
        self.client.force_authenticate(self.owner)
        self.client.post(self._url())
        self.client.post(self._url("invite/"), {"user_id": self.teammate.id}, format="json")
        session = CopilotSession.objects.get(conversation=self.conversation)
        invite = session.invites.get(invited_user=self.teammate)
        self.client.force_authenticate(self.teammate)
        self.client.post(
            f"/api/v1/copilot/sessions/invites/{invite.id}/respond/",
            {"status": "accepted"},
            format="json",
        )

        response = self.client.post(
            self._url("handoff/"),
            {"to_user_id": self.stranger.id, "note": "Own the recovery call."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "awaiting_handoff")
        session.refresh_from_db()
        handoff_event = session.events.get(kind=SessionEvent.Kind.HANDED_OFF)
        self.assertEqual(handoff_event.payload["to_user_id"], self.stranger.id)
        self.assertEqual(handoff_event.payload["note"], "Own the recovery call.")
        self.assertTrue(
            SessionInvite.objects.filter(
                session=session, invited_user=self.stranger, status=SessionInvite.Status.PENDING
            ).exists()
        )
        notification = Notification.objects.get(recipient=self.stranger)
        self.assertEqual(notification.kind, Notification.Kind.COPILOT_HANDOFF)
        self.assertEqual(notification.actor, self.teammate)
        # The note stays on the session (payload above), read once the
        # target accepts; the notice carries it only to someone who already
        # sees the whole conversation, which a pending target does not.
        self.assertNotIn("Own the recovery call.", notification.message)

    def test_handoff_creates_the_session_directly_without_make_live_first(self):
        # Real UX fix: hand-off is its own independent way to start a
        # session — not gated behind clicking "Make this a live
        # session" first (see _get_or_create_session's own docstring).
        self.assertFalse(CopilotSession.objects.filter(conversation=self.conversation).exists())
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            self._url("handoff/"),
            {"to_user_id": self.teammate.id, "note": "Own it."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        session = CopilotSession.objects.get(conversation=self.conversation)
        self.assertEqual(session.status, CopilotSession.Status.AWAITING_HANDOFF)
        # The owner becomes an active participant even though they never
        # called POST .../session/ first.
        self.assertTrue(
            SessionParticipant.objects.filter(
                session=session, user=self.owner, left_at__isnull=True
            ).exists()
        )

    def test_stranger_cant_hand_off_a_session_they_have_no_access_to(self):
        # 404, not 400 — same "don't even confirm it exists" convention
        # as everywhere else conversations_visible_to gates access.
        self.client.force_authenticate(self.owner)
        self.client.post(self._url())
        self.client.force_authenticate(self.stranger)
        response = self.client.post(
            self._url("handoff/"), {"to_user_id": self.teammate.id, "note": ""}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_only_the_owner_can_close(self):
        self.client.force_authenticate(self.owner)
        self.client.post(self._url())
        self.client.force_authenticate(self.teammate)
        response = self.client.post(self._url("close/"))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_close_can_capture_the_sessions_decisions_in_the_same_request(self):
        Message.objects.create(conversation=self.conversation, role="user", content="Let's do it")
        session = CopilotSession.objects.create(
            conversation=self.conversation, status=CopilotSession.Status.LIVE
        )
        self.client.force_authenticate(self.owner)
        with patch("services.copilot.views.SessionCloseView._capture") as capture:
            capture.return_value = {"decisions": [{"id": 1}], "decisions_error": None}
            response = self.client.post(
                self._url("close/"), {"capture_decisions": True}, format="json"
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "closed")
        self.assertEqual(response.data["decisions"], [{"id": 1}])
        capture.assert_called_once_with(session, self.owner)

    def test_a_failed_capture_leaves_the_session_closed_and_says_why(self):
        CopilotSession.objects.create(
            conversation=self.conversation, status=CopilotSession.Status.LIVE
        )
        self.client.force_authenticate(self.owner)
        with patch("services.metrics.facilitator.get_completion") as call:
            # No customers and no human turn: the facilitator refuses before
            # any call, and the refusal travels back beside the closed session.
            response = self.client.post(
                self._url("close/"), {"capture_decisions": True}, format="json"
            )

        call.assert_not_called()
        self.assertEqual(response.data["status"], "closed")
        self.assertEqual(response.data["decisions"], [])
        self.assertTrue(response.data["decisions_error"])
        self.assertEqual(
            CopilotSession.objects.get(conversation=self.conversation).status, "closed"
        )

    def test_close_without_the_flag_makes_no_model_call(self):
        CopilotSession.objects.create(
            conversation=self.conversation, status=CopilotSession.Status.LIVE
        )
        self.client.force_authenticate(self.owner)
        with patch("services.copilot.views.SessionCloseView._capture") as capture:
            response = self.client.post(self._url("close/"))
        capture.assert_not_called()
        self.assertNotIn("decisions", response.data)

    def test_close_sets_status_and_closed_at(self):
        self.client.force_authenticate(self.owner)
        self.client.post(self._url())
        response = self.client.post(self._url("close/"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "closed")
        self.assertIsNotNone(response.data["closed_at"])


class SendMessageViewSessionPermissionTests(APITestCase):
    """The other half of the real fix — SendMessageView/ConversationDetailView
    widened via conversations_visible_to (see that function's own docstring)."""

    url = "/api/v1/copilot/messages/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.owner = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.teammate = User.objects.create_user(
            email="bob@acme.io", password="supersecret1", name="Bob", organisation=self.org
        )
        self.stranger = User.objects.create_user(
            email="carl@acme.io", password="supersecret1", name="Carl", organisation=self.org
        )
        self.conversation = Conversation.objects.create(organisation=self.org, user=self.owner)
        Message.objects.create(conversation=self.conversation, role=Message.Role.USER, content="Hi")

    def _accept_teammate_into_a_live_session(self):
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/v1/copilot/conversations/{self.conversation.id}/session/")
        self.client.post(
            f"/api/v1/copilot/conversations/{self.conversation.id}/session/invite/",
            {"user_id": self.teammate.id},
            format="json",
        )
        session = CopilotSession.objects.get(conversation=self.conversation)
        invite = session.invites.get(invited_user=self.teammate)
        self.client.force_authenticate(self.teammate)
        self.client.post(
            f"/api/v1/copilot/sessions/invites/{invite.id}/respond/",
            {"status": "accepted"},
            format="json",
        )
        return session

    @patch("services.copilot.views.get_completion")
    def test_an_accepted_participant_can_send_into_someone_elses_conversation(
        self, mock_get_completion
    ):
        mock_get_completion.return_value = "A real reply"
        self._accept_teammate_into_a_live_session()
        response = self.client.post(
            self.url,
            {"conversation_id": self.conversation.id, "content": "Redirecting this"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_an_unrelated_user_still_404s_even_with_a_session(self):
        self._accept_teammate_into_a_live_session()
        self.client.force_authenticate(self.stranger)
        response = self.client.post(
            self.url, {"conversation_id": self.conversation.id, "content": "Hi"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_closed_session_refuses_new_messages(self):
        session = self._accept_teammate_into_a_live_session()
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/v1/copilot/conversations/{self.conversation.id}/session/close/")

        response = self.client.post(
            self.url, {"conversation_id": self.conversation.id, "content": "Hi"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        session.refresh_from_db()
        self.assertEqual(session.status, CopilotSession.Status.CLOSED)

    @patch("services.copilot.views.get_completion")
    def test_a_message_into_an_already_sessioned_conversation_logs_a_redirected_event(
        self, mock_get_completion
    ):
        mock_get_completion.return_value = "A real reply"
        session = self._accept_teammate_into_a_live_session()
        self.client.post(
            self.url,
            {"conversation_id": self.conversation.id, "content": "Redirecting this"},
            format="json",
        )

        redirect_event = session.events.get(kind=SessionEvent.Kind.REDIRECTED)
        self.assertEqual(redirect_event.actor, self.teammate)
        self.assertEqual(redirect_event.message.content, "Redirecting this")

    def test_participant_can_now_get_the_conversation_via_conversation_detail(self):
        self._accept_teammate_into_a_live_session()
        self.client.force_authenticate(self.teammate)
        response = self.client.get(f"/api/v1/copilot/conversations/{self.conversation.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class MyInvitesAndRespondViewTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.owner = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.teammate = User.objects.create_user(
            email="bob@acme.io", password="supersecret1", name="Bob", organisation=self.org
        )
        customer = Customer.objects.create(organisation=self.org, name="Globex")
        self.conversation = Conversation.objects.create(
            organisation=self.org, user=self.owner, title="Renewal risk"
        )
        self.client.force_authenticate(self.owner)
        self.client.post(
            f"/api/v1/copilot/conversations/{self.conversation.id}/session/",
            {"customer_id": customer.id},
            format="json",
        )
        self.client.post(
            f"/api/v1/copilot/conversations/{self.conversation.id}/session/invite/",
            {"user_id": self.teammate.id},
            format="json",
        )
        self.session = CopilotSession.objects.get(conversation=self.conversation)
        self.invite = self.session.invites.get(invited_user=self.teammate)

    def test_my_invites_shows_real_context_before_acceptance(self):
        self.client.force_authenticate(self.teammate)
        response = self.client.get("/api/v1/copilot/sessions/invites/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["account_label"], "Globex")
        self.assertEqual(response.data[0]["invited_by"]["name"], "Alice")
        self.assertEqual(response.data[0]["conversation_id"], self.conversation.id)

    def test_my_invites_never_shows_someone_elses_invite(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get("/api/v1/copilot/sessions/invites/")
        self.assertEqual(response.data, [])

    def test_accepting_creates_a_participant_and_a_joined_event(self):
        self.client.force_authenticate(self.teammate)
        response = self.client.post(
            f"/api/v1/copilot/sessions/invites/{self.invite.id}/respond/",
            {"status": "accepted"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            SessionParticipant.objects.filter(
                session=self.session, user=self.teammate, left_at__isnull=True
            ).exists()
        )
        self.assertTrue(
            self.session.events.filter(kind=SessionEvent.Kind.JOINED, actor=self.teammate).exists()
        )

    def test_declining_creates_no_participant(self):
        self.client.force_authenticate(self.teammate)
        self.client.post(
            f"/api/v1/copilot/sessions/invites/{self.invite.id}/respond/",
            {"status": "declined"},
            format="json",
        )
        self.assertFalse(
            SessionParticipant.objects.filter(session=self.session, user=self.teammate).exists()
        )

    def test_cant_respond_to_someone_elses_invite(self):
        other = User.objects.create_user(
            email="carl@acme.io", password="supersecret1", name="Carl", organisation=self.org
        )
        self.client.force_authenticate(other)
        response = self.client.post(
            f"/api/v1/copilot/sessions/invites/{self.invite.id}/respond/",
            {"status": "accepted"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_an_invalid_status_value_is_rejected(self):
        self.client.force_authenticate(self.teammate)
        response = self.client.post(
            f"/api/v1/copilot/sessions/invites/{self.invite.id}/respond/",
            {"status": "maybe"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class SessionEventsCarryNoMessageTextTests(APITestCase):
    """Session events name the turn they are about, never its text: a
    viewer who is only mentioned reads turns through visible_messages
    (the conversation endpoint), so the session poll and the WebSocket
    push must not hand them every turn's raw content."""

    SECRET = "Procurement quietly approved a 40% discount"

    def setUp(self):
        from services.knowledge.models import Question

        self.org = Organisation.objects.create(name="Acme Inc")
        self.owner = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.teammate = User.objects.create_user(
            email="bob@acme.io", password="supersecret1", name="Bob", organisation=self.org
        )
        self.manager = User.objects.create_user(
            email="meg@acme.io", password="supersecret1", name="Meg", organisation=self.org
        )
        self.conversation = Conversation.objects.create(
            organisation=self.org, user=self.owner, title="Renewal risk"
        )
        self.session = CopilotSession.objects.create(
            conversation=self.conversation, status=CopilotSession.Status.LIVE
        )
        SessionInvite.objects.create(
            session=self.session,
            invited_user=self.teammate,
            invited_by=self.owner,
            status=SessionInvite.Status.ACCEPTED,
        )
        SessionParticipant.objects.create(session=self.session, user=self.teammate)

        mention = Message.objects.create(
            conversation=self.conversation,
            role=Message.Role.USER,
            author=self.owner,
            content="@Meg can you check the renewal date?",
        )
        Question.objects.create(
            organisation=self.org,
            asked_by=self.owner,
            assignee=self.manager,
            message=mention,
            text="can you check the renewal date?",
        )
        self.secret_turn = Message.objects.create(
            conversation=self.conversation,
            role=Message.Role.USER,
            author=self.teammate,
            content=self.SECRET,
        )
        for turn in (mention, self.secret_turn):
            SessionEvent.objects.create(
                session=self.session,
                kind=SessionEvent.Kind.REDIRECTED,
                actor=turn.author,
                message=turn,
            )

    def _poll(self, user):
        self.client.force_authenticate(user)
        return self.client.get(f"/api/v1/copilot/conversations/{self.conversation.id}/session/")

    def _assert_events_are_references(self, events):
        messages = [e["message"] for e in events if e["message"] is not None]
        self.assertEqual(len(messages), 2)
        for message in messages:
            self.assertEqual(set(message), {"id", "role", "created_at"})
        self.assertNotIn(self.SECRET, str(events))

    def test_a_mentioned_only_viewer_polling_the_session_never_sees_turn_text(self):
        response = self._poll(self.manager)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self._assert_events_are_references(response.data["events"])

    def test_the_websocket_push_never_carries_turn_text(self):
        from services.copilot.realtime import broadcast_session_update

        sent = []

        class _Layer:
            async def group_send(self, group, message):
                sent.append(message)

        with patch("services.copilot.realtime.get_channel_layer", return_value=_Layer()):
            broadcast_session_update(self.session)

        self.assertEqual(len(sent), 1)
        self._assert_events_are_references(sent[0]["payload"]["events"])

    def test_owner_and_participant_still_get_event_message_ids_to_refetch(self):
        for user in (self.owner, self.teammate):
            response = self._poll(user)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            ids = [e["message"]["id"] for e in response.data["events"] if e["message"]]
            self.assertIn(self.secret_turn.id, ids)


class SessionNoteAndCompanyNamesFollowVisibilityTests(APITestCase):
    """A hand-off note is the owner's free text and a company name is a
    record a viewer may not be allowed to open: both reach the owner and
    participants on the per-viewer poll, a mentioned-only viewer only
    when they could see them anyway, and the one-group WebSocket push
    never."""

    NOTE = "They are about to churn, do not mention the discount"

    def setUp(self):
        from services.customers.models import Account
        from services.knowledge.models import Question

        self.org = Organisation.objects.create(name="Acme Inc")
        self.owner = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.teammate = User.objects.create_user(
            email="bob@acme.io", password="supersecret1", name="Bob", organisation=self.org
        )
        self.manager = User.objects.create_user(
            email="meg@acme.io", password="supersecret1", name="Meg", organisation=self.org
        )
        self.customer = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", owner=self.owner
        )
        self.account = Account.objects.create(name="Pizza Hut EMEA", owner=self.owner)
        self.account.customers.add(self.customer)
        self.conversation = Conversation.objects.create(
            organisation=self.org, user=self.owner, title="Renewal risk"
        )
        self.session = CopilotSession.objects.create(
            conversation=self.conversation,
            status=CopilotSession.Status.AWAITING_HANDOFF,
            customer=self.customer,
            account=self.account,
        )
        SessionInvite.objects.create(
            session=self.session,
            invited_user=self.teammate,
            invited_by=self.owner,
            status=SessionInvite.Status.ACCEPTED,
        )
        SessionParticipant.objects.create(session=self.session, user=self.teammate)
        mention = Message.objects.create(
            conversation=self.conversation,
            role=Message.Role.USER,
            author=self.owner,
            content="@Meg can you check the renewal date?",
        )
        Question.objects.create(
            organisation=self.org,
            asked_by=self.owner,
            assignee=self.manager,
            message=mention,
            text="can you check the renewal date?",
        )
        SessionEvent.objects.create(
            session=self.session,
            kind=SessionEvent.Kind.HANDED_OFF,
            actor=self.owner,
            payload={"to_user_id": self.teammate.id, "to_user_name": "Bob", "note": self.NOTE},
        )

    def _poll(self, user):
        self.client.force_authenticate(user)
        response = self.client.get(f"/api/v1/copilot/conversations/{self.conversation.id}/session/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data

    def _handoff(self, data):
        return next(e for e in data["events"] if e["kind"] == "handed_off")

    def test_a_mentioned_only_viewer_gets_no_note_and_no_names_they_cannot_see(self):
        data = self._poll(self.manager)

        handoff = self._handoff(data)
        self.assertIsNone(handoff["payload"]["note"])
        self.assertEqual(handoff["payload"]["to_user_name"], "Bob")
        self.assertIsNone(data["customer_name"])
        self.assertIsNone(data["account_name"])
        self.assertNotIn(self.NOTE, str(data))
        self.assertNotIn("Pizza Hut", str(data))

    def test_the_owner_and_a_participant_get_the_note_and_the_names(self):
        for user in (self.owner, self.teammate):
            data = self._poll(user)
            self.assertEqual(self._handoff(data)["payload"]["note"], self.NOTE)
        owner_view = self._poll(self.owner)
        self.assertEqual(owner_view["customer_name"], "Pizza Hut")
        self.assertEqual(owner_view["account_name"], "Pizza Hut EMEA")

    def test_the_websocket_push_carries_neither_the_note_nor_the_names(self):
        from services.copilot.realtime import broadcast_session_update

        sent = []

        class _Layer:
            async def group_send(self, group, message):
                sent.append(message)

        with patch("services.copilot.realtime.get_channel_layer", return_value=_Layer()):
            broadcast_session_update(self.session)

        payload = sent[0]["payload"]
        self.assertIsNone(self._handoff(payload)["payload"]["note"])
        self.assertIsNone(payload["customer_name"])
        self.assertIsNone(payload["account_name"])
        self.assertEqual(payload["customer_id"], self.customer.id)


class OnlyWholeConversationViewersChangeASessionTests(APITestCase):
    """A person who is only mentioned reads a slice of a conversation;
    changing its session (hand-off, invite, redirect, close, decisions)
    is for the owner and present participants only — otherwise a
    self-hand-off plus accepting their own invite turned a slice into
    the whole conversation."""

    SECRET = "Procurement quietly approved a 40% discount"

    def setUp(self):
        from services.customers.models import Account
        from services.knowledge.models import Question

        self.org = Organisation.objects.create(name="Acme Inc")
        self.owner = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.teammate = User.objects.create_user(
            email="bob@acme.io", password="supersecret1", name="Bob", organisation=self.org
        )
        self.manager = User.objects.create_user(
            email="meg@acme.io", password="supersecret1", name="Meg", organisation=self.org
        )
        self.customer = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", owner=self.owner
        )
        self.account = Account.objects.create(name="Pizza Hut EMEA", owner=self.owner)
        self.account.customers.add(self.customer)
        self.conversation = Conversation.objects.create(
            organisation=self.org, user=self.owner, title=self.SECRET[:50]
        )
        Message.objects.create(
            conversation=self.conversation,
            role=Message.Role.USER,
            author=self.teammate,
            content=self.SECRET,
        )
        mention = Message.objects.create(
            conversation=self.conversation,
            role=Message.Role.USER,
            author=self.teammate,
            content="@Meg can you check the renewal date?",
        )
        Question.objects.create(
            organisation=self.org,
            asked_by=self.teammate,
            assignee=self.manager,
            message=mention,
            text="can you check the renewal date?",
        )
        # Make the secret turn addressed to someone else, so Meg's slice
        # excludes it whatever the org chart says.
        Question.objects.create(
            organisation=self.org,
            asked_by=self.teammate,
            assignee=self.owner,
            message=self.conversation.messages.order_by("id").first(),
            text=self.SECRET,
        )
        self.session = CopilotSession.objects.create(
            conversation=self.conversation,
            status=CopilotSession.Status.LIVE,
            customer=self.customer,
            account=self.account,
        )
        SessionParticipant.objects.create(session=self.session, user=self.owner)

    def _base(self, suffix=""):
        return f"/api/v1/copilot/conversations/{self.conversation.id}/session/{suffix}"

    def _detail(self, user):
        self.client.force_authenticate(user)
        return self.client.get(f"/api/v1/copilot/conversations/{self.conversation.id}/")

    def test_a_mentioned_only_viewer_cannot_change_the_session(self):
        self.client.force_authenticate(self.manager)
        attempts = {
            "handoff": self.client.post(
                self._base("handoff/"), {"to_user_id": self.teammate.id}, format="json"
            ),
            "invite": self.client.post(
                self._base("invite/"), {"user_id": self.teammate.id}, format="json"
            ),
            "close": self.client.post(self._base("close/"), {}, format="json"),
            "decisions": self.client.post(self._base("decisions/"), {}, format="json"),
            "redirect": self.client.post(
                "/api/v1/copilot/messages/",
                {"conversation_id": self.conversation.id, "content": "hello"},
                format="json",
            ),
        }
        for route, response in attempts.items():
            with self.subTest(route=route):
                self.assertIn(
                    response.status_code,
                    (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
                )
        self.assertFalse(SessionInvite.objects.filter(session=self.session).exists())
        self.assertFalse(
            SessionParticipant.objects.filter(session=self.session, user=self.manager).exists()
        )
        self.assertEqual(self.session.events.count(), 0)

    def test_nobody_can_hand_off_or_invite_to_themselves(self):
        self.client.force_authenticate(self.owner)
        handoff = self.client.post(
            self._base("handoff/"), {"to_user_id": self.owner.id}, format="json"
        )
        invite = self.client.post(self._base("invite/"), {"user_id": self.owner.id}, format="json")

        self.assertEqual(handoff.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(invite.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(SessionInvite.objects.filter(session=self.session).exists())

    def test_the_escalation_chain_ends_without_full_visibility(self):
        self.client.force_authenticate(self.manager)
        self.client.post(self._base("handoff/"), {"to_user_id": self.manager.id}, format="json")
        # Even an invite that somehow names its own inviter (written before
        # this fix) is refused on accept.
        planted = SessionInvite.objects.create(
            session=self.session, invited_user=self.manager, invited_by=self.manager
        )
        response = self.client.post(
            f"/api/v1/copilot/sessions/invites/{planted.id}/respond/",
            {"status": "accepted"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        detail = self._detail(self.manager)
        self.assertEqual(detail.data["visibility"], "partial")
        self.assertNotIn(self.SECRET, str(detail.data))

    def test_respond_only_works_for_the_invites_own_target(self):
        invite = SessionInvite.objects.create(
            session=self.session, invited_user=self.teammate, invited_by=self.owner
        )
        self.client.force_authenticate(self.manager)
        response = self.client.post(
            f"/api/v1/copilot/sessions/invites/{invite.id}/respond/",
            {"status": "accepted"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_the_title_is_neutral_unless_the_first_turn_is_in_the_viewers_slice(self):
        detail = self._detail(self.manager)
        listing = self.client.get("/api/v1/copilot/conversations/")

        self.assertEqual(detail.data["title"], "Shared conversation")
        self.assertEqual(
            [c["title"] for c in listing.data if c["id"] == self.conversation.id],
            ["Shared conversation"],
        )
        self.assertEqual(self._detail(self.owner).data["title"], self.SECRET[:50])

    def test_the_handoff_notice_gates_the_note_and_the_company_name(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            self._base("handoff/"),
            {"to_user_id": self.teammate.id, "note": "Do not mention the discount"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        notice = Notification.objects.get(recipient=self.teammate)
        self.assertNotIn("discount", notice.message)
        self.assertNotIn("Pizza Hut", notice.message)

    def test_the_invite_label_is_null_for_an_invitee_who_cannot_open_the_customer(self):
        self.client.force_authenticate(self.owner)
        self.client.post(self._base("invite/"), {"user_id": self.teammate.id}, format="json")

        self.client.force_authenticate(self.teammate)
        invites = self.client.get("/api/v1/copilot/sessions/invites/").data
        self.assertEqual([i["account_label"] for i in invites], [None])
        notice = Notification.objects.get(recipient=self.teammate)
        self.assertNotIn("Pizza Hut", notice.message)

    def test_a_participant_who_cannot_open_the_customer_gets_null_names(self):
        SessionInvite.objects.create(
            session=self.session,
            invited_user=self.teammate,
            invited_by=self.owner,
            status=SessionInvite.Status.ACCEPTED,
        )
        SessionParticipant.objects.create(session=self.session, user=self.teammate)
        self.client.force_authenticate(self.teammate)
        data = self.client.get(self._base()).data

        self.assertIsNone(data["customer_name"])
        self.assertIsNone(data["account_name"])
