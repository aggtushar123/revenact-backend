"""Personal records on a customer: notes, like mail, are the writer's.

Readable by the author and their management chain — never by peers or
seniors — in the Notes tab and in the Copilot alike. Rows with no author
(seeded, or logged before authorship existed) stay visible as before.
"""

from services.accounts.hierarchy import chain_visible_q


def visible_notes(user, queryset):
    # SOC2:AUTH-02 object-level rule for personal notes
    return queryset.filter(chain_visible_q(user, "author"))
