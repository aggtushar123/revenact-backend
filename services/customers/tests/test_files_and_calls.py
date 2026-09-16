"""Files tab and CallSense: uploads are checked, downloads are gated, calls
are logged with a transcript the model summarises."""

from datetime import datetime
from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import AuditEvent
from services.accounts.models import Organisation, User
from services.customers.files import safe_name, validate_upload
from services.customers.models import Account, Attachment, Call, Customer

PDF = b"%PDF-1.4\n%fake\n"


class Fixture(APITestCase):
    def setUp(self):
        from services.copilot.anthropic_client import CopilotNotConfigured

        no_model = patch(
            "services.customers.classification.get_completion",
            side_effect=CopilotNotConfigured("no model in tests"),
        )
        no_model.start()
        self.addCleanup(no_model.stop)
        self.org = Organisation.objects.create(name="Acme")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="x",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.carl = User.objects.create_user(
            email="carl@acme.io",
            password="x",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.dana = User.objects.create_user(
            email="dana@acme.io",
            password="x",
            name="Dana",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.pizza = Customer.objects.create(organisation=self.org, name="Pizza Hut")
        self.hut_uk = Account.objects.create(name="Pizza Hut UK")
        self.hut_uk.customers.add(self.pizza)
        other = Organisation.objects.create(name="Other")
        self.outsider = User.objects.create_user(
            email="o@other.io", password="x", name="Otto", organisation=other, role=User.Role.ADMIN
        )

    def upload(
        self, user, name="contract.pdf", content=PDF, content_type="application/pdf", **fields
    ):
        self.client.force_authenticate(user)
        return self.client.post(
            f"/api/v1/customers/{self.pizza.id}/files/",
            {"file": SimpleUploadedFile(name, content, content_type=content_type), **fields},
            format="multipart",
        )


class UploadRulesTests(APITestCase):
    def test_names_are_sanitised(self):
        self.assertEqual(safe_name("../../etc/passwd"), "etc/passwd".split("/")[-1])
        self.assertEqual(safe_name("C:\\Users\\me\\Q3 deck.pptx"), "Q3 deck.pptx")
        self.assertEqual(safe_name("  weird\x00name<>.pdf "), "weirdname.pdf")
        self.assertEqual(safe_name(""), "file")

    def test_only_the_closed_list_of_types_is_accepted(self):
        for name, ctype, body in [
            ("page.html", "text/html", b"<html>"),
            ("logo.svg", "image/svg+xml", b"<svg>"),
            ("run.exe", "application/octet-stream", b"MZ"),
            ("bundle.zip", "application/zip", b"PK\x03\x04"),
        ]:
            with self.assertRaises(Exception, msg=name):
                validate_upload(SimpleUploadedFile(name, body, content_type=ctype))

    def test_a_pdf_that_is_not_a_pdf_is_refused(self):
        with self.assertRaisesRegex(Exception, "does not look like"):
            validate_upload(
                SimpleUploadedFile("x.pdf", b"<html>hi</html>", content_type="application/pdf")
            )
        with self.assertRaisesRegex(Exception, "sent as"):
            validate_upload(SimpleUploadedFile("x.pdf", PDF, content_type="text/html"))

    @override_settings(ATTACHMENT_MAX_BYTES=10)
    def test_the_size_cap_applies(self):
        with self.assertRaisesRegex(Exception, "limited to"):
            validate_upload(SimpleUploadedFile("x.txt", b"x" * 11, content_type="text/plain"))
        with self.assertRaisesRegex(Exception, "empty"):
            validate_upload(SimpleUploadedFile("x.txt", b"", content_type="text/plain"))


class FilesTests(Fixture):
    def test_upload_list_download_and_delete(self):
        response = self.upload(self.carl, description="Signed MSA")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        row = response.data
        self.assertEqual(
            (row["name"], row["content_type"], row["size"]),
            ("contract.pdf", "application/pdf", len(PDF)),
        )
        self.assertEqual(row["uploaded_by"]["name"], "Carl")
        self.assertNotIn("file", row)
        self.assertTrue(AuditEvent.objects.filter(action="file.upload").exists())
        attachment = Attachment.objects.get()
        self.assertTrue(attachment.file.name.startswith(f"attachments/{self.org.id}/"))
        self.assertNotIn("contract", attachment.file.name)

        listed = self.client.get(f"/api/v1/customers/{self.pizza.id}/files/")
        self.assertEqual([r["name"] for r in listed.data], ["contract.pdf"])

        download = self.client.get(row["download_url"])
        self.assertEqual(download.status_code, status.HTTP_200_OK)
        self.assertEqual(b"".join(download.streaming_content), PDF)
        self.assertIn('attachment; filename="contract.pdf"', download["Content-Disposition"])
        self.assertEqual(download["X-Content-Type-Options"], "nosniff")
        self.assertEqual(download["Cache-Control"], "private, no-store")

        self.client.force_authenticate(self.dana)
        self.assertEqual(self.client.delete(f"/api/v1/files/{row['id']}/").status_code, 403)
        self.client.force_authenticate(self.carl)
        path = attachment.file.path
        self.assertEqual(self.client.delete(f"/api/v1/files/{row['id']}/").status_code, 204)
        self.assertFalse(Attachment.objects.exists())
        import os

        self.assertFalse(os.path.exists(path))
        self.assertTrue(AuditEvent.objects.filter(action="file.delete").exists())

    def test_an_admin_may_delete_anyones_file(self):
        row = self.upload(self.carl).data
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.delete(f"/api/v1/files/{row['id']}/").status_code, 204)

    def test_a_bad_upload_is_a_400_and_stores_nothing(self):
        response = self.upload(
            self.carl, name="page.html", content=b"<html>", content_type="text/html"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not an accepted", response.data["detail"])
        self.assertFalse(Attachment.objects.exists())
        self.client.force_authenticate(self.carl)
        missing = self.client.post(
            f"/api/v1/customers/{self.pizza.id}/files/", {}, format="multipart"
        )
        self.assertEqual(missing.status_code, status.HTTP_400_BAD_REQUEST)

    def test_files_on_an_account(self):
        self.client.force_authenticate(self.carl)
        response = self.client.post(
            f"/api/v1/customers/{self.pizza.id}/accounts/{self.hut_uk.id}/files/",
            {"file": SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Attachment.objects.get().account, self.hut_uk)
        self.assertEqual(len(self.client.get(f"/api/v1/customers/{self.pizza.id}/files/").data), 0)

    def test_another_organisation_cannot_see_or_download(self):
        row = self.upload(self.carl).data
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(row["download_url"]).status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/files/{row['id']}/").status_code, 404)
        self.assertEqual(
            self.client.get(f"/api/v1/customers/{self.pizza.id}/files/").status_code, 404
        )
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(row["download_url"]).status_code, 401)


class CallsTests(Fixture):
    WHEN = datetime(2026, 9, 16, 10, 0, tzinfo=dt_timezone.utc)

    def test_log_a_call_with_a_written_summary(self):
        self.client.force_authenticate(self.carl)
        response = self.client.post(
            f"/api/v1/customers/{self.pizza.id}/calls/",
            {
                "title": "Renewal readiness",
                "occurred_at": self.WHEN.isoformat(),
                "duration_minutes": 45,
                "summary": "They want the enterprise tier.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["host_name"], "Carl")
        self.assertEqual(response.data["logged_by"]["name"], "Carl")
        self.assertIsNone(response.data["transcript"])
        self.assertTrue(AuditEvent.objects.filter(action="call.log").exists())
        listed = self.client.get(f"/api/v1/customers/{self.pizza.id}/calls/")
        self.assertEqual([c["title"] for c in listed.data], ["Renewal readiness"])

    def test_a_transcript_is_kept_and_summarised(self):
        self.client.force_authenticate(self.carl)
        with patch(
            "services.customers.calls.get_completion", return_value="  They asked for SSO by Q4. "
        ) as model:
            response = self.client.post(
                f"/api/v1/customers/{self.pizza.id}/accounts/{self.hut_uk.id}/calls/",
                {
                    "title": "QBR",
                    "host_name": "Sam",
                    "occurred_at": self.WHEN.isoformat(),
                    "transcript": SimpleUploadedFile(
                        "qbr.vtt",
                        b"WEBVTT\n\n00:01 --> 00:02\nWe need SSO by Q4.",
                        content_type="text/vtt",
                    ),
                },
                format="multipart",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["summary"], "They asked for SSO by Q4.")
        self.assertIn("We need SSO", model.call_args.kwargs["messages"][0]["content"])
        self.assertEqual(model.call_args.kwargs["purpose"], "call_summary")
        transcript = response.data["transcript"]
        self.assertEqual((transcript["name"], transcript["source"]), ("qbr.vtt", "transcript"))
        call = Call.objects.get()
        self.assertEqual((call.account, call.host_name), (self.hut_uk, "Sam"))
        self.assertEqual(call.transcript.account, self.hut_uk)
        download = self.client.get(transcript["download_url"])
        self.assertEqual(download.status_code, status.HTTP_200_OK)

    def test_pasted_transcript_without_a_model_still_logs_the_call(self):
        self.client.force_authenticate(self.carl)
        with patch(
            "services.customers.calls.get_completion",
            side_effect=__import__(
                "services.copilot.anthropic_client", fromlist=["CopilotNotConfigured"]
            ).CopilotNotConfigured("no"),
        ):
            response = self.client.post(
                f"/api/v1/customers/{self.pizza.id}/calls/",
                {
                    "title": "Check-in",
                    "occurred_at": self.WHEN.isoformat(),
                    "transcript_text": "long talk",
                },
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["summary"], "")

    def test_a_transcript_must_be_a_text_format(self):
        self.client.force_authenticate(self.carl)
        response = self.client.post(
            f"/api/v1/customers/{self.pizza.id}/calls/",
            {
                "title": "x",
                "occurred_at": self.WHEN.isoformat(),
                "transcript": SimpleUploadedFile("deck.pdf", PDF, content_type="application/pdf"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("transcript", response.data)
        self.assertFalse(Call.objects.exists())
        self.assertFalse(Attachment.objects.exists())

    def test_calls_are_scoped_to_the_organisation(self):
        Call.objects.create(customer=self.pizza, title="t", host_name="h", occurred_at=self.WHEN)
        self.client.force_authenticate(self.outsider)
        self.assertEqual(
            self.client.get(f"/api/v1/customers/{self.pizza.id}/calls/").status_code, 404
        )
