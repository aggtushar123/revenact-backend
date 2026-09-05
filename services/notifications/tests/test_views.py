"""Integration tier: through the real URLconf + real test DB."""

from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.notifications.models import Notification


class NotificationListViewTests(APITestCase):
    url = "/api/v1/notifications/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.alice = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.carl = User.objects.create_user(
            email="carl@acme.io", password="supersecret1", name="Carl", organisation=self.org
        )

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_only_returns_the_callers_own_notifications_newest_first(self):
        Notification.objects.create(
            recipient=self.carl, kind=Notification.Kind.CUSTOMER_ASSIGNED, message="Not mine"
        )
        older = Notification.objects.create(
            recipient=self.alice, kind=Notification.Kind.CUSTOMER_ASSIGNED, message="Older"
        )
        newer = Notification.objects.create(
            recipient=self.alice, kind=Notification.Kind.CUSTOMER_ASSIGNED, message="Newer"
        )
        self.client.force_authenticate(self.alice)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([n["id"] for n in response.data], [newer.id, older.id])

    def test_includes_a_real_actor_when_set(self):
        Notification.objects.create(
            recipient=self.alice,
            actor=self.carl,
            kind=Notification.Kind.COPILOT_INVITE,
            message="Carl invited you",
            link="/copilot?session=1",
        )
        self.client.force_authenticate(self.alice)

        response = self.client.get(self.url)

        self.assertEqual(response.data[0]["actor"]["name"], "Carl")
        self.assertEqual(response.data[0]["link"], "/copilot?session=1")
        self.assertFalse(response.data[0]["is_read"])


class MarkNotificationReadViewTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.alice = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.carl = User.objects.create_user(
            email="carl@acme.io", password="supersecret1", name="Carl", organisation=self.org
        )
        self.notification = Notification.objects.create(
            recipient=self.alice, kind=Notification.Kind.CUSTOMER_ASSIGNED, message="x"
        )

    def _url(self, notification):
        return f"/api/v1/notifications/{notification.id}/read/"

    def test_marks_the_callers_own_notification_read(self):
        self.client.force_authenticate(self.alice)
        response = self.client.post(self._url(self.notification))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.notification.refresh_from_db()
        self.assertTrue(self.notification.is_read)

    def test_404_for_someone_elses_notification(self):
        self.client.force_authenticate(self.carl)
        response = self.client.post(self._url(self.notification))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.notification.refresh_from_db()
        self.assertFalse(self.notification.is_read)


class MarkAllNotificationsReadViewTests(APITestCase):
    url = "/api/v1/notifications/read-all/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.alice = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.carl = User.objects.create_user(
            email="carl@acme.io", password="supersecret1", name="Carl", organisation=self.org
        )

    def test_marks_only_the_callers_own_unread_notifications(self):
        mine_unread = Notification.objects.create(
            recipient=self.alice, kind=Notification.Kind.CUSTOMER_ASSIGNED, message="a"
        )
        mine_already_read = Notification.objects.create(
            recipient=self.alice,
            kind=Notification.Kind.CUSTOMER_ASSIGNED,
            message="b",
            is_read=True,
        )
        not_mine = Notification.objects.create(
            recipient=self.carl, kind=Notification.Kind.CUSTOMER_ASSIGNED, message="c"
        )
        self.client.force_authenticate(self.alice)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mine_unread.refresh_from_db()
        mine_already_read.refresh_from_db()
        not_mine.refresh_from_db()
        self.assertTrue(mine_unread.is_read)
        self.assertTrue(mine_already_read.is_read)
        self.assertFalse(not_mine.is_read)
