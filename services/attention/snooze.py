"""Snooze filtering for the Dashboard Overview's "Needs attention" list.

`rules.build_items` returns every candidate, unfiltered; this module is the
one place that drops the ones a person has already dealt with. An item stays
hidden while its snooze is active — `until` in the future, or null for
Done — unless the item has since got worse than it was when snoozed
(`rules.worse` on the stored vs. current fingerprint). An expired snooze is
never deleted here: it's simply not "active" any more, so the item shows up
again on its own, no cleanup job required.
"""

from .rules import worse


def visible_items(user, items, *, now):
    """`items` with every currently-active, not-yet-worse snooze dropped."""
    snoozes = {snooze.key: snooze for snooze in user.attention_snoozes.all()}
    visible = []
    for item in items:
        snooze = snoozes.get(item["key"])
        if snooze is None:
            visible.append(item)
            continue
        active = snooze.until is None or snooze.until > now
        if active and not worse(item["kind"], snooze.fingerprint, item["fingerprint"]):
            continue
        visible.append(item)
    return visible
