"""Proving a company controls a domain, and mapping addresses onto tenants.

Domain ownership is the hinge of corporate onboarding. Once `accenture.com` is
mapped to a tenant, anyone who signs in with an address there is routed to that
tenant. So the mapping must never be established by somebody *typing* the
domain, only by demonstrating control of it. Publishing a DNS TXT record is that
demonstration: it requires access the company's IT has and a stranger does not.

**Why DNS over HTTPS rather than a resolver library.** The repo's stated ethos
is plain HTTP through the standard library, with no vendor SDKs "to pin, patch
or explain to an auditor" (see `services/mail/providers/base.py`). A TXT lookup
is the only DNS this application will ever do, and DoH gets it over the same
hardened, host-allow-listed HTTP path everything else already uses, with no new
dependency to audit.

The honest limitation: this trusts the resolver. A resolver that lied could
assert a record that does not exist. Both defaults are major public resolvers
reached over TLS, which is the same trust every other outbound call here makes.
Higher assurance would mean corroborating two independent resolvers, and the
structure below allows that without changing callers.

**Personal domains never map.** `gmail.com` is not a company, and treating it as
one would put every Gmail user in one tenant. Addresses there need an invitation
instead, which is the workflow the brief reserves for them.
"""

import secrets
import urllib.parse
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core import audit
from services.mail.providers.base import ProviderError, http_json

from .models import OrganizationDomain

#: What a company publishes, as a TXT record on the domain itself.
TXT_PREFIX = "revenact-site-verification="

#: JSON DoH endpoints. Queried in order; the first that answers is used.
RESOLVERS = (
    "https://dns.google/resolve",
    "https://cloudflare-dns.com/dns-query",
)

#: Only these hosts may be called for a lookup. Passed explicitly rather than
#: widening the mail providers' own allow-list, which has nothing to do with DNS.
RESOLVER_HOSTS = frozenset({"dns.google", "cloudflare-dns.com"})

#: Free and consumer mail providers. An address here identifies a person, not a
#: company, so it can never establish or join a tenant by domain alone.
#: Overridable in settings so a deployment can extend it without a release.
DEFAULT_PERSONAL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "msn.com",
        "yahoo.com",
        "yahoo.co.uk",
        "ymail.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "pm.me",
        "gmx.com",
        "gmx.net",
        "mail.com",
        "zoho.com",
        "yandex.com",
        "fastmail.com",
        "hey.com",
        "tutanota.com",
        "duck.com",
        "qq.com",
        "163.com",
        "126.com",
    }
)


def personal_domains() -> frozenset:
    configured = getattr(settings, "PERSONAL_EMAIL_DOMAINS", None)
    if configured:
        return frozenset(d.strip().lower() for d in configured if d.strip())
    return DEFAULT_PERSONAL_DOMAINS


def domain_of(email: str) -> str:
    """The lowercased domain part of an address, or empty if there isn't one."""
    _, _, domain = (email or "").strip().lower().rpartition("@")
    return domain.rstrip(".")


def is_personal(email_or_domain: str) -> bool:
    domain = email_or_domain if "@" not in email_or_domain else domain_of(email_or_domain)
    return domain.lower() in personal_domains()


def new_token() -> str:
    """The value a company publishes. Unguessable, so possession of the record
    is evidence rather than coincidence."""
    return secrets.token_urlsafe(24)


def expected_record(domain: OrganizationDomain) -> str:
    return f"{TXT_PREFIX}{domain.verification_token}"


def lookup_txt(domain: str) -> list[str]:
    """Every TXT value published on `domain`.

    Returns an empty list when nothing is published. Raises `ProviderError`
    only when no resolver could be reached at all, so "no record" and "could
    not check" stay distinguishable — treating them the same would let an
    outage look like a failed verification.
    """
    last_error = None
    for resolver in RESOLVERS:
        url = resolver + "?" + urllib.parse.urlencode({"name": domain, "type": "TXT"})
        try:
            payload = http_json(
                "GET",
                url,
                headers={"Accept": "application/dns-json"},
                allowed_hosts=RESOLVER_HOSTS,
            )
        except ProviderError as exc:
            last_error = exc
            continue

        values = []
        for answer in payload.get("Answer") or []:
            # TXT answers arrive quoted, and long ones arrive as several
            # concatenated quoted strings.
            raw = (answer.get("data") or "").strip()
            parts = [p for p in raw.split('"') if p.strip()]
            values.append("".join(parts) if parts else raw)
        return values

    raise ProviderError(f"could not reach a DNS resolver: {last_error}")


def verify(domain: OrganizationDomain, *, request=None, actor=None) -> bool:
    """Check the published record and promote the domain if it matches.

    Idempotent: verifying an already-verified domain re-checks and leaves it
    verified. A domain whose record has been withdrawn is *not* silently
    demoted here — revoking access is a deliberate act, not a side effect of a
    DNS blip — but the caller is told it no longer matches.

    **Proof supersedes claims.** Promoting one organisation's record revokes
    every other organisation's record for the same domain, verified or not. DNS
    control is the only evidence this system accepts, so whoever holds it now
    holds the domain, and an employee's earlier unverified claim (a self-made
    workspace, say) cannot stand in the company's way. Each revocation is
    audited: it is somebody losing something.
    """
    if not domain.verification_token:
        return False

    values = lookup_txt(domain.domain)
    matched = expected_record(domain) in values

    if matched and domain.verification_status != OrganizationDomain.VerificationStatus.VERIFIED:
        with transaction.atomic():
            superseded = list(
                OrganizationDomain.objects.select_for_update()
                .filter(domain=domain.domain)
                .exclude(pk=domain.pk)
                .exclude(verification_status=OrganizationDomain.VerificationStatus.REVOKED)
            )
            for other in superseded:
                other.verification_status = OrganizationDomain.VerificationStatus.REVOKED
                other.save(update_fields=["verification_status", "updated_at"])

            domain.verification_status = OrganizationDomain.VerificationStatus.VERIFIED
            domain.verified_at = timezone.now()
            domain.save(update_fields=["verification_status", "verified_at", "updated_at"])

        for other in superseded:
            audit.record(  # SOC2:LOG-01
                "domain.superseded",
                request=request,
                actor=actor,
                organisation=other.organisation,
                target=other,
                metadata={"domain": other.domain, "verified_by": domain.organisation_id},
            )

    return matched


def organisation_for_email(email: str):
    """The tenant an address belongs to, or None.

    Only a **verified** domain maps, and personal domains never do. This is the
    single place that turns an address into a tenant, so there is one rule to
    audit rather than one per caller.
    """
    route = route_for_email(email)
    return route.organisation if route.kind == "verified" else None


@dataclass(frozen=True)
class DomainRoute:
    """Where an address leads, and why.

    kind is one of:
      personal   — a consumer mailbox; never maps anywhere
      verified   — an organisation has proved it owns the domain
      claimed    — exactly one organisation holds an unverified claim
      ambiguous  — several unverified claims and no proof; nobody can be chosen
      unclaimed  — nobody has mentioned this domain
    """

    kind: str
    organisation: object = None


def route_for_email(email: str) -> DomainRoute:
    """Classify an address. The one rule every sign-in path shares.

    `verified` is the only kind that grants anything on its own. `claimed`
    exists so the second person from a domain does not create a second
    workspace: they are pointed at the one that exists and its founder must
    approve them explicitly. That is an administrator's decision, not domain
    trust, so the rule that an unverified domain maps nobody still holds.
    """
    domain = domain_of(email)
    if not domain:
        return DomainRoute("unclaimed")
    if is_personal(domain):
        return DomainRoute("personal")

    records = list(
        OrganizationDomain.objects.select_related("organisation")
        .filter(domain=domain)
        .exclude(verification_status=OrganizationDomain.VerificationStatus.REVOKED)
        .order_by("created_at")
    )
    for record in records:
        if record.is_verified:
            return DomainRoute("verified", record.organisation)
    if len(records) == 1:
        return DomainRoute("claimed", records[0].organisation)
    if records:
        return DomainRoute("ambiguous")
    return DomainRoute("unclaimed")


__all__ = [
    "TXT_PREFIX",
    "DomainRoute",
    "route_for_email",
    "domain_of",
    "expected_record",
    "is_personal",
    "lookup_txt",
    "new_token",
    "organisation_for_email",
    "personal_domains",
    "verify",
]
