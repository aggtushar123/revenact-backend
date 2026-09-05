"""Dev/demo convenience — not part of the product. Seeds Canvas rows
against real seeded Contacts so the sidebar's "Canvas" gallery and the
"Canvas List" tab on both Details pages have real, meaningful data —
run after seed_demo_contacts.

Real seeded Contact data is genuinely sparse (most demo companies have
exactly one Contact, a handful have none at all) — this doesn't invent
extra Contacts to pad the layouts out. Apple Inc is the one company
with a full 5-Contact roster (Executive Sponsor, Champion, Economic
Buyer, Technical Lead, Influencer), so it gets the one "real" multi-node
stakeholder map with labeled relationship edges between them;
single-Contact companies get a single-node canvas instead, which is
itself a realistic "just started mapping this account" state — the
same reasoning seed_demo_surveys.py's own still-`sent` rows use.

Idempotent: matched by (parent, name), same spirit as
seed_demo_risks's (parent, title) — re-running updates existing rows
instead of duplicating them. Silently skips any customer_name/
account_name/contact name that doesn't exist yet.

Usage:
    python manage.py seed_demo_canvases --org-email alice@acme.io
"""

from django.core.management.base import BaseCommand, CommandError

from services.accounts.models import User
from services.customers.models import Account, Canvas, Contact, Customer


def _node(node_id, contact_id, x, y):
    return {
        "id": node_id,
        "type": "contact",
        "position": {"x": x, "y": y},
        "data": {"contact_id": contact_id},
    }


def _edge(edge_id, source, target, label):
    return {"id": edge_id, "source": source, "target": target, "label": label}


# Org-level canvases — customer_name must match a Customer.name already
# seeded by seed_demo_customers; contact_names must match Contact.name
# values already seeded by seed_demo_contacts, under that same Customer.
DEMO_CUSTOMER_CANVASES = [
    {
        "customer_name": "Apple Inc",
        "name": "Renewal Strategy Q3",
        "contact_names": [
            "Sarah Chen",
            "James Wilson",
            "Elena Rodriguez",
            "Marcus Thorne",
            "Olivia Park",
        ],
        "edges": [
            # (source contact name, target contact name, label)
            ("James Wilson", "Sarah Chen", "Reports to"),
            ("Olivia Park", "James Wilson", "Reports to"),
            ("Marcus Thorne", "Elena Rodriguez", "Influences"),
            ("Sarah Chen", "James Wilson", "Introduced by"),
        ],
    },
    {
        "customer_name": "Kraft Heinz",
        "name": "Stakeholder Map",
        "contact_names": ["Sophie Turner"],
        "edges": [],
    },
]

# Account-level canvases — customer_name/account_name must match an
# Account already seeded by seed_demo_accounts; contact_names must
# match Contacts seeded directly under that Account.
DEMO_ACCOUNT_CANVASES = [
    {
        "customer_name": "Apple Inc",
        "account_name": "North America Enterprise",
        "name": "Stakeholder Map",
        "contact_names": ["Tim Cook Jr."],
        "edges": [],
    },
]

# A rough grid so multi-node canvases don't all stack at the origin —
# position is otherwise entirely the frontend's own concern (the CSM
# drags nodes wherever they like once opened).
_POSITIONS = [(60, 60), (320, 60), (60, 260), (320, 260), (190, 420)]


class Command(BaseCommand):
    help = "Seeds demo Canvas rows against real seeded Customers/Accounts/Contacts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org-email",
            required=True,
            help="Email of a user in the target organisation (e.g. the admin who signed up).",
        )

    def handle(self, *args, **options):
        try:
            caller = User.objects.get(email=options["org_email"])
        except User.DoesNotExist as exc:
            raise CommandError(f"No user with email {options['org_email']!r}.") from exc

        org = caller.organisation
        created, updated, skipped = 0, 0, 0

        def build_nodes_and_edges(contacts_by_name):
            node_id_by_name = {}
            nodes = []
            for i, (name, contact) in enumerate(contacts_by_name.items()):
                x, y = _POSITIONS[i % len(_POSITIONS)]
                node_id = f"n{i + 1}"
                node_id_by_name[name] = node_id
                nodes.append(_node(node_id, contact.id, x, y))
            return nodes, node_id_by_name

        for row in DEMO_CUSTOMER_CANVASES:
            try:
                customer = Customer.objects.get(organisation=org, name=row["customer_name"])
            except Customer.DoesNotExist:
                self.stderr.write(
                    f"  skipping canvas — no customer {row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            contacts_by_name = {}
            for contact_name in row["contact_names"]:
                contact = Contact.objects.filter(customer=customer, name=contact_name).first()
                if contact is None:
                    self.stderr.write(
                        f"  skipping contact {contact_name!r} — "
                        f"not found under {row['customer_name']!r}."
                    )
                    continue
                contacts_by_name[contact_name] = contact

            if not contacts_by_name:
                self.stderr.write(f"  skipping canvas {row['name']!r} — no real contacts resolved.")
                skipped += 1
                continue

            nodes, node_id_by_name = build_nodes_and_edges(contacts_by_name)
            edges = [
                _edge(
                    f"{node_id_by_name[src]}-{node_id_by_name[dst]}",
                    node_id_by_name[src],
                    node_id_by_name[dst],
                    label,
                )
                for src, dst, label in row["edges"]
                if src in node_id_by_name and dst in node_id_by_name
            ]

            _, was_created = Canvas.objects.update_or_create(
                customer=customer, name=row["name"], defaults={"nodes": nodes, "edges": edges}
            )
            created += was_created
            updated += not was_created

        for row in DEMO_ACCOUNT_CANVASES:
            try:
                account = (
                    Account.objects.filter(
                        customers__organisation=org,
                        customers__name=row["customer_name"],
                        name=row["account_name"],
                    )
                    .distinct()
                    .get()
                )
            except Account.DoesNotExist:
                self.stderr.write(
                    f"  skipping canvas — no account {row['account_name']!r} under "
                    f"{row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            contacts_by_name = {}
            for contact_name in row["contact_names"]:
                contact = Contact.objects.filter(account=account, name=contact_name).first()
                if contact is None:
                    self.stderr.write(
                        f"  skipping contact {contact_name!r} — "
                        f"not found under {row['account_name']!r}."
                    )
                    continue
                contacts_by_name[contact_name] = contact

            if not contacts_by_name:
                self.stderr.write(f"  skipping canvas {row['name']!r} — no real contacts resolved.")
                skipped += 1
                continue

            nodes, node_id_by_name = build_nodes_and_edges(contacts_by_name)
            edges = [
                _edge(
                    f"{node_id_by_name[src]}-{node_id_by_name[dst]}",
                    node_id_by_name[src],
                    node_id_by_name[dst],
                    label,
                )
                for src, dst, label in row["edges"]
                if src in node_id_by_name and dst in node_id_by_name
            ]

            _, was_created = Canvas.objects.update_or_create(
                account=account, name=row["name"], defaults={"nodes": nodes, "edges": edges}
            )
            created += was_created
            updated += not was_created

        self.stdout.write(
            self.style.SUCCESS(
                f"{org.name}: created {created}, updated {updated}, skipped {skipped} canvas(es)."
            )
        )
