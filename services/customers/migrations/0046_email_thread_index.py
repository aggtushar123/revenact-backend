"""An index for the Communications queue's one non-trivial predicate.

"Replies owed" asks, per received email, whether a later email exists in the
same thread for the same mailbox. Without this the correlated subquery scans
the whole table once per candidate row; `thread_id` was stored but never
indexed because nothing had queried it before.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0045_pipeline_departments"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="email",
            index=models.Index(
                fields=["mailbox_owner", "thread_id", "sent_at"],
                name="email_thread_lookup_idx",
            ),
        ),
    ]
