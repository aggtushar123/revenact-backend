"""One-off backfill for migration 0015; takes the model so the migration can
pass its historical one.

Before every send folded its history (`views.ask_snapshot`), a plain reply
that followed an Ask reply stored no snapshot and no marker, so it was read
per source only and could repeat the Ask reply to a slice reader. Such a
reply is marked here as one fed Ask history with no snapshot
(`carries_anomaly_text=True`, `grounded_customer_ids=None`), which
`views._reply_readable_by` withholds from slice readers. Rows are not
re-derived: whatever the model was fed then is unknown, so any earlier Ask
reply in the conversation is enough, and failing closed is the point."""

BATCH = 500


def mark_legacy_followups(Message):
    """Mark every legacy plain reply that comes after an Ask reply in its
    conversation. Reads only the conversations that hold an Ask turn,
    streamed with `iterator()`, and updates in batches. Returns the count."""
    conversations = (
        Message.objects.filter(role="user", context__isnull=False)
        .values_list("conversation_id", flat=True)
        .distinct()
        .order_by()
    )
    marked, pending = 0, []
    for conversation_id in conversations.iterator():
        turns = (
            Message.objects.filter(conversation_id=conversation_id)
            .order_by("created_at", "id")
            .values_list("id", "role", "context", "reply_to_id", "carries_anomaly_text")
        )
        contexts, seen_ask, last_user = {}, False, None
        for pk, role, context, reply_to_id, carries in turns.iterator():
            if role == "user":
                contexts[pk] = bool(context)
                last_user = pk
                continue
            answered = reply_to_id if reply_to_id is not None else last_user
            if contexts.get(answered, False):
                seen_ask = True
            elif seen_ask and carries is None:
                pending.append(pk)
        if len(pending) >= BATCH:
            marked += _mark(Message, pending)
            pending = []
    if pending:
        marked += _mark(Message, pending)
    return marked


def _mark(Message, ids):
    return Message.objects.filter(pk__in=ids, carries_anomaly_text__isnull=True).update(
        carries_anomaly_text=True, grounded_customer_ids=None
    )
