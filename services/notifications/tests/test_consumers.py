"""Integration tier for this app's own real-time push — through the
real ASGI application (config.asgi.application), the real JWT auth
middleware (core/ws_auth.py), and the real NotificationConsumer, using
Channels' own WebsocketCommunicator. CHANNEL_LAYERS is already the
in-memory layer under `manage.py test` (see config/settings.py's own
CHANNEL_LAYERS docstring), so no real Redis is needed here either."""

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase
from rest_framework_simplejwt.tokens import RefreshToken

from config.asgi import application
from services.accounts.models import Organisation, User
from services.notifications.models import Notification
from services.notifications.realtime import notify


def _access_token(user):
    return str(RefreshToken.for_user(user).access_token)


class NotificationConsumerTests(TransactionTestCase):
    # Same real Channels/Django interaction as
    # services.copilot.tests.test_consumers's own TransactionTestCase —
    # see that file's own comment for why plain TestCase doesn't work
    # here.
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.alice = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.carl = User.objects.create_user(
            email="carl@acme.io", password="supersecret1", name="Carl", organisation=self.org
        )
        self.alice_token = _access_token(self.alice)
        self.carl_token = _access_token(self.carl)

    async def test_a_real_user_can_connect(self):
        communicator = WebsocketCommunicator(
            application, f"/ws/notifications/?token={self.alice_token}"
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.disconnect()

    async def test_no_token_is_rejected(self):
        communicator = WebsocketCommunicator(application, "/ws/notifications/")
        connected, _ = await communicator.connect()
        self.assertFalse(connected)

    async def test_a_garbage_token_is_rejected(self):
        communicator = WebsocketCommunicator(application, "/ws/notifications/?token=not-a-real-jwt")
        connected, _ = await communicator.connect()
        self.assertFalse(connected)

    async def test_a_real_notification_reaches_only_its_own_recipient(self):
        alice_communicator = WebsocketCommunicator(
            application, f"/ws/notifications/?token={self.alice_token}"
        )
        carl_communicator = WebsocketCommunicator(
            application, f"/ws/notifications/?token={self.carl_token}"
        )
        await alice_communicator.connect()
        await carl_communicator.connect()

        @database_sync_to_async
        def send_real_notification():
            notify(
                recipient=self.alice,
                actor=self.carl,
                kind=Notification.Kind.CUSTOMER_ASSIGNED,
                message="Carl assigned you Pizza Hut",
                link="/organizations/1",
            )

        await send_real_notification()

        payload = await alice_communicator.receive_json_from()
        self.assertEqual(payload["message"], "Carl assigned you Pizza Hut")
        self.assertEqual(payload["actor"]["name"], "Carl")

        self.assertTrue(await carl_communicator.receive_nothing())

        await alice_communicator.disconnect()
        await carl_communicator.disconnect()
