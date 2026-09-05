"""Integration tier for Multiplayer Copilot Phase 2b's own real-time push
— through the real ASGI application (config.asgi.application), the real
JWT auth middleware, and the real SessionConsumer, using Channels' own
WebsocketCommunicator. CHANNEL_LAYERS is overridden to the in-memory
layer for the whole module — same consumer code path as the real Redis
one, no real Redis dependency for `manage.py test` (see
config/settings.py's own CHANNEL_LAYERS docstring)."""

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings
from rest_framework_simplejwt.tokens import RefreshToken

from config.asgi import application
from services.accounts.models import Organisation, User
from services.copilot.models import Conversation, CopilotSession, SessionInvite, SessionParticipant

IN_MEMORY_CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


def _access_token(user):
    return str(RefreshToken.for_user(user).access_token)


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNEL_LAYERS)
class SessionConsumerTests(TransactionTestCase):
    # A real, known Channels/Django interaction, not a stylistic choice:
    # database_sync_to_async runs its sync DB work on a worker thread
    # whose connection gets closed on teardown — plain TestCase holds one
    # shared per-test transaction across the whole test and breaks the
    # moment that happens; TransactionTestCase reopens connections as
    # needed instead, so a closed one from a prior async test's cleanup
    # never poisons the next one's setUp.
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
        self.session = CopilotSession.objects.create(
            conversation=self.conversation, status=CopilotSession.Status.LIVE
        )
        SessionParticipant.objects.create(session=self.session, user=self.owner)

        # Real JWTs, generated here in setUp (a plain sync method, even
        # though the test bodies below are async) — SimpleJWT's own
        # RefreshToken.for_user does a real DB write (the outstanding-
        # token blacklist record), which would raise
        # SynchronousOnlyOperation if called directly from an async test
        # body instead.
        self.owner_token = _access_token(self.owner)
        self.teammate_token = _access_token(self.teammate)
        self.stranger_token = _access_token(self.stranger)

    def _url(self, token):
        return f"/ws/copilot/sessions/{self.conversation.id}/?token={token}"

    async def test_the_owner_can_connect(self):
        communicator = WebsocketCommunicator(application, self._url(self.owner_token))
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.disconnect()

    async def test_an_accepted_active_participant_can_connect(self):
        @database_sync_to_async
        def accept_teammate():
            SessionInvite.objects.create(
                session=self.session,
                invited_user=self.teammate,
                invited_by=self.owner,
                status=SessionInvite.Status.ACCEPTED,
            )
            SessionParticipant.objects.create(session=self.session, user=self.teammate)

        await accept_teammate()

        communicator = WebsocketCommunicator(application, self._url(self.teammate_token))
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.disconnect()

    async def test_an_uninvited_user_is_rejected(self):
        communicator = WebsocketCommunicator(application, self._url(self.stranger_token))
        connected, _ = await communicator.connect()
        self.assertFalse(connected)

    async def test_an_invited_but_not_yet_accepted_user_is_still_rejected(self):
        @database_sync_to_async
        def invite_teammate():
            SessionInvite.objects.create(
                session=self.session,
                invited_user=self.teammate,
                invited_by=self.owner,
                status=SessionInvite.Status.PENDING,
            )

        await invite_teammate()

        communicator = WebsocketCommunicator(application, self._url(self.teammate_token))
        connected, _ = await communicator.connect()
        self.assertFalse(connected)

    async def test_no_token_at_all_is_rejected(self):
        communicator = WebsocketCommunicator(
            application, f"/ws/copilot/sessions/{self.conversation.id}/"
        )
        connected, _ = await communicator.connect()
        self.assertFalse(connected)

    async def test_a_garbage_token_is_rejected(self):
        communicator = WebsocketCommunicator(
            application, f"/ws/copilot/sessions/{self.conversation.id}/?token=not-a-real-jwt"
        )
        connected, _ = await communicator.connect()
        self.assertFalse(connected)

    async def test_a_real_make_live_action_pushes_a_real_update_to_a_connected_participant(self):
        # The other half of the fix: a real REST action (see
        # services.copilot.realtime.broadcast_session_update, called
        # from SessionHandoffView.post) reaches an already-connected
        # socket instantly, not on the next poll tick.
        communicator = WebsocketCommunicator(application, self._url(self.owner_token))
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        @database_sync_to_async
        def hand_off():
            from services.copilot.models import SessionEvent
            from services.copilot.realtime import broadcast_session_update

            SessionEvent.objects.create(
                session=self.session, kind=SessionEvent.Kind.HANDED_OFF, actor=self.owner
            )
            broadcast_session_update(self.session)

        await hand_off()

        payload = await communicator.receive_json_from()
        self.assertEqual(payload["conversation_id"], self.conversation.id)
        kinds = [e["kind"] for e in payload["events"]]
        self.assertIn("handed_off", kinds)

        await communicator.disconnect()
