"""A contacts segment as a CSV: the person, how to reach them, and their
organisation and account (only ones the reader may open, as on the Contacts
list). Organisations and accounts segments use their lists' own exports.

The table is Organizations' `build_table`, so every cell goes through its
`cell` (a name starting with `=` is data, not a formula). A contact has no
money, so the Currency column `build_table` adds is dropped."""

from services.organizations.export import build_table
from services.organizations.fields import Field


def _parent(key):
    return lambda row: row[key]["name"] if row[key] else ""


CONTACT_FIELDS = (
    Field("name", "Name", lambda row: row["name"]),
    Field("email", "Email", lambda row: row["email"]),
    Field("phone", "Phone", lambda row: row["phone"]),
    Field("role", "Role", lambda row: row["role_display"]),
    Field("status", "Status", lambda row: row["status"]),
    Field("sentiment", "Sentiment", lambda row: row["sentiment"]),
    Field("language", "Language", lambda row: row["language"]),
    Field("lastContacted", "Last Contacted", lambda row: row["last_contacted_at"]),
    Field("organisation", "Organisation", _parent("organisation")),
    Field("account", "Account", _parent("account")),
)


def contacts_table(rows):
    """`rows` are `ContactSerializer` rows."""
    return [line[:-1] for line in build_table(rows, CONTACT_FIELDS, "")]
