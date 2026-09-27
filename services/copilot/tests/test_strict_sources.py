"""The Copilot is strict: a record is fetched, quoted or shown in a reply
only when the viewer may read the record *and* open the company it hangs
off — an account-level record needs its account in `visible_accounts`, an
organisation-level one its customer in `visible_customers`. Company
*matching* stays organisation-wide; reading never is."""

from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.copilot.models import Message as Turn
from services.copilot.retrieval import _gather_candidates
from services.copilot.views import _reply_readable_by
from services.customers.models import Customer, Email, Note
from services.customers.tests.test_views import blind_to_one_account
from services.mail.models import MailboxConnection, MailMessage


def cites(record, kind, company):
    is_account = company.__class__.__name__ == "Account"
    return Turn(
        sources=[
            {
                "type": kind,
                "id": record.id,
                "company_type": "account" if is_account else "customer",
                "company_id": company.id,
            }
        ]
    )


class StrictCopilotSources(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=self.org, name="Globex")
        self.viewer, self.seen, self.hidden = blind_to_one_account(self.customer)
        self.admin = User.objects.create_user(
            email="admin@acme.io",
            password="supersecret1",
            name="Admin",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        # No mailbox owner and no author: each record's own rule lets
        # everyone read it, so only the company stands in the way.
        self.email = self.mail_on(self.hidden, "Hidden renewal terms")
        self.note = Note.objects.create(
            account=self.hidden,
            title="Hidden champion left",
            author_name="Seed",
            body="Sam moved on.",
            logged_at=timezone.localdate(),
        )

    def mail_on(self, parent, subject):
        return Email.objects.create(
            account=parent,
            subject=subject,
            body="Can we talk?",
            sent_at=timezone.now(),
            direction=Email.Direction.RECEIVED,
            from_address="sam@globex.com",
        )

    def lines(self, company, viewer):
        return "\n".join(item.line for item in _gather_candidates(company, viewer=viewer))

    def test_a_hidden_accounts_email_and_note_are_not_quoted_to_the_blind_viewer(self):
        quoted = self.lines(self.hidden, self.viewer)
        self.assertNotIn("Hidden renewal terms", quoted)
        self.assertNotIn("Hidden champion left", quoted)

    def test_they_are_quoted_to_someone_who_sees_everything(self):
        quoted = self.lines(self.hidden, self.admin)
        self.assertIn("Hidden renewal terms", quoted)
        self.assertIn("Hidden champion left", quoted)

    def test_a_reply_citing_them_is_not_readable_by_the_blind_viewer(self):
        for record, kind in ((self.email, "email"), (self.note, "note")):
            with self.subTest(kind=kind):
                turn = cites(record, kind, self.hidden)
                self.assertFalse(_reply_readable_by(turn, self.viewer))
                self.assertTrue(_reply_readable_by(turn, self.admin))

    def test_an_organisation_level_record_needs_its_customer_open(self):
        closed = Customer.objects.create(organisation=self.org, name="Initech", owner=self.admin)
        note = Note.objects.create(
            customer=closed,
            title="Closed",
            author_name="Seed",
            body="x",
            logged_at=timezone.localdate(),
        )
        self.assertNotIn("Closed", self.lines(closed, self.viewer))
        turn = cites(note, "note", closed)
        self.assertFalse(_reply_readable_by(turn, self.viewer))
        self.assertTrue(_reply_readable_by(turn, self.admin))

    @patch("services.copilot.views.get_completion", return_value="Hi Sam")
    def test_an_inbox_row_filed_on_a_hidden_account_drafts_without_its_history(self, completion):
        other = self.mail_on(self.hidden, "Other hidden thread")
        connection = MailboxConnection.objects.create(
            organisation=self.org,
            user=self.viewer,
            provider="imap",
            address=self.viewer.email,
            display_name=self.viewer.name,
            credentials="",
        )
        row = MailMessage.objects.create(
            connection=connection,
            owner=self.viewer,
            organisation=self.org,
            provider_message_id="m1",
            direction="received",
            from_address="sam@globex.com",
            subject="Renewal",
            body="Can we talk?",
            sent_at=timezone.now(),
            email=other,
        )
        self.client.force_authenticate(self.viewer)
        response = self.client.post(
            "/api/v1/copilot/draft-reply/", {"kind": "mail_message", "id": row.id}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["sources"], [])
        system = completion.call_args.kwargs["system"]
        self.assertNotIn("Hidden renewal terms", system)
        self.assertNotIn("Hidden champion left", system)
        self.assertNotIn("Hidden", system.split("Account history", 1)[1])


class CitedIdShapeTests(APITestCase):
    """A cited record's id is checked whatever shape it was stored in: a
    numeric string is the record it names, anything else fails closed."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Globex")
        self.viewer, self.seen, self.hidden = blind_to_one_account(self.customer)

    def private_note(self):
        # On the seen account, but the colleague's own note: not the viewer's.
        return Note.objects.create(
            account=self.seen,
            title="Private",
            author_name="Owner",
            author=self.customer.owner,
            body="x",
            logged_at=timezone.localdate(),
        )

    def cite(self, record_id):
        return Turn(
            sources=[
                {
                    "type": "note",
                    "id": record_id,
                    "company_type": "account",
                    "company_id": self.seen.id,
                }
            ]
        )

    def test_a_numeric_string_id_is_checked_against_its_record(self):
        note = self.private_note()
        self.assertFalse(_reply_readable_by(self.cite(str(note.id)), self.viewer))
        self.assertFalse(_reply_readable_by(self.cite(note.id), self.viewer))

    def test_a_junk_id_fails_closed(self):
        for junk in ("abc", "5.0", None, True, [1], -3, "0"):
            with self.subTest(junk=junk):
                self.assertFalse(_reply_readable_by(self.cite(junk), self.viewer))
