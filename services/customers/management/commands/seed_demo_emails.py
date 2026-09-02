"""Dev/demo convenience — not part of the product. Seeds Email rows
under existing demo Customers and Accounts so ActivityFeed's "Emails"
filter has real, per-entity data on both the Organization Details
page (General tab) and the standalone Account page — run after
seed_demo_customers and seed_demo_accounts.

Covers every company seed_demo_customers creates (1-2 org-level emails
each) and every sub-account seed_demo_accounts creates (1-2
account-level emails each) — a handful of these match the original
activityData.ts/accountActivityData.ts mock content for the companies
that mock happened to name (Apple Inc, Pizza Hut, Kraft Heinz, and
Apple's North America Enterprise/Apple EMEA sub-accounts); the rest
are new demo content invented to give every other seeded
company/account something to show too, same reasoning as
seed_demo_activities.

Idempotent: matched by (parent, subject, sent_at), so re-running
updates existing rows instead of duplicating them. Silently skips any
customer_name/account_name that doesn't exist yet in the target
organisation.

Usage:
    python manage.py seed_demo_emails --org-email alice@acme.io
"""

from django.core.management.base import BaseCommand, CommandError

from services.accounts.models import User
from services.customers.models import Account, Customer, Email

# Org-level emails — customer_name must match a Customer.name already
# seeded by seed_demo_customers.
DEMO_CUSTOMER_EMAILS = [
    {
        "customer_name": "Apple Inc",
        "subject": "Re: Welcome — Let's Begin Your Onboarding Journey",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Natalie Reyes",
        "body": "Noted on this Natalie! Thanks. Regards, Edgar",
        "sent_at": "2026-02-26T18:20:00Z",
        "links": 3,
        "watchers": 2,
        "is_starred": True,
    },
    {
        "customer_name": "Apple Inc",
        "subject": "Re: Follow-Up: Renewal Readiness & Expansion Opportunities",
        "sender_name": "Natalie Reyes",
        "recipient_name": "Edgar Holmes",
        "body": "Hi Edgar, following up on our last conversation regarding the "
        "upcoming renewal. We've prepared a summary of expansion options for "
        "your review.",
        "sent_at": "2025-12-08T11:57:00Z",
        "links": 2,
        "watchers": 1,
        "is_starred": False,
    },
    {
        "customer_name": "Apple Inc",
        "subject": "Quarterly Business Review - Q4 2025 Recap",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Sarah Chen",
        "body": "Hi Sarah, please find attached the QBR deck for Q4. Happy to "
        "discuss any items that need follow-up.",
        "sent_at": "2026-01-15T15:45:00Z",
        "links": 5,
        "watchers": 3,
        "is_starred": True,
    },
    {
        "customer_name": "Apple Inc",
        "subject": "Feature Request: Advanced Analytics Dashboard",
        "sender_name": "Natalie Reyes",
        "recipient_name": "Product Team",
        "body": "The customer has requested enhancements to the analytics "
        "module — specifically drill-down capability and custom report builder.",
        "sent_at": "2026-01-10T09:30:00Z",
        "links": 1,
        "watchers": 4,
        "is_starred": False,
    },
    {
        "customer_name": "Pizza Hut",
        "subject": "Escalation: Critical Integration Issue",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Support Team",
        "body": "Priority escalation — the Salesforce integration has been "
        "intermittently failing since yesterday. Customer impact is significant.",
        "sent_at": "2026-03-02T08:15:00Z",
        "links": 0,
        "watchers": 5,
        "is_starred": True,
    },
    {
        "customer_name": "Pizza Hut",
        "subject": "Onboarding Check-In: Week 3 Status",
        "sender_name": "Sarah Chen",
        "recipient_name": "Edgar Holmes",
        "body": "Quick check-in on the onboarding progress. All milestones are "
        "on track and the team is ramping up well.",
        "sent_at": "2026-02-20T14:00:00Z",
        "links": 1,
        "watchers": 2,
        "is_starred": False,
    },
    {
        "customer_name": "Kraft Heinz",
        "subject": "Success Plan Review: H1 2026 Goals",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Natalie Reyes",
        "body": "Attached is the updated success plan with revised KPIs for H1. "
        "Let me know if we need to adjust timelines.",
        "sent_at": "2026-03-01T10:00:00Z",
        "links": 2,
        "watchers": 1,
        "is_starred": False,
    },
    {
        "customer_name": "Arista Networks",
        "subject": "Network Performance Review — Q2 Results",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Priya Patel",
        "body": "Hi Priya, sharing the Q2 network performance benchmarks — "
        "latency down 18% since the upgrade.",
        "sent_at": "2026-06-11T10:00:00Z",
        "links": 1,
        "watchers": 2,
        "is_starred": False,
    },
    {
        "customer_name": "Arista Networks",
        "subject": "Re: Onboarding Milestone Reached",
        "sender_name": "Natalie Reyes",
        "recipient_name": "Edgar Holmes",
        "body": "Great news — Arista hit their first onboarding milestone "
        "ahead of schedule.",
        "sent_at": "2026-05-03T09:15:00Z",
        "links": 0,
        "watchers": 1,
        "is_starred": False,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "subject": "Escalation: Booking API Downtime",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Support Team",
        "body": "Booking API returned 500s for ~20 minutes this morning — "
        "RCA attached.",
        "sent_at": "2026-07-19T08:45:00Z",
        "links": 2,
        "watchers": 6,
        "is_starred": True,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "subject": "Health Check Summary — July",
        "sender_name": "Sarah Chen",
        "recipient_name": "Edgar Holmes",
        "body": "Health score steady at 8.6. No major risks flagged this cycle.",
        "sent_at": "2026-06-02T13:30:00Z",
        "links": 1,
        "watchers": 2,
        "is_starred": False,
    },
    {
        "customer_name": "Notion Labs",
        "subject": "Welcome to Revenact — Onboarding Kickoff",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Notion Team",
        "body": "Excited to get Notion Labs onboarded — kickoff call scheduled "
        "for next week.",
        "sent_at": "2026-08-06T11:00:00Z",
        "links": 0,
        "watchers": 3,
        "is_starred": False,
    },
    {
        "customer_name": "Notion Labs",
        "subject": "Re: Feature Request — Workspace Analytics",
        "sender_name": "Natalie Reyes",
        "recipient_name": "Product Team",
        "body": "Notion Labs asked about workspace-level analytics — logging "
        "as a feature request.",
        "sent_at": "2026-07-23T15:20:00Z",
        "links": 1,
        "watchers": 1,
        "is_starred": False,
    },
    {
        "customer_name": "Oracle",
        "subject": "Executive Alignment Recap",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Sarah Chen",
        "body": "Great session with Oracle's VP of Ops — aligned on Q3 "
        "priorities.",
        "sent_at": "2026-06-29T16:00:00Z",
        "links": 1,
        "watchers": 4,
        "is_starred": True,
    },
    {
        "customer_name": "Oracle",
        "subject": "Usage Report — June",
        "sender_name": "Sarah Chen",
        "recipient_name": "Edgar Holmes",
        "body": "Oracle's active seat usage is up 12% month over month.",
        "sent_at": "2026-05-16T09:40:00Z",
        "links": 0,
        "watchers": 2,
        "is_starred": False,
    },
    {
        "customer_name": "Salesforce",
        "subject": "Success Plan Created — H2 Goals",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Natalie Reyes",
        "body": "Drafted the H2 success plan for Salesforce — 3 key expansion "
        "metrics.",
        "sent_at": "2026-07-02T10:30:00Z",
        "links": 2,
        "watchers": 3,
        "is_starred": False,
    },
    {
        "customer_name": "Salesforce",
        "subject": "Health Check Review Notes",
        "sender_name": "Sarah Chen",
        "recipient_name": "Edgar Holmes",
        "body": "Health check complete — no red flags, CSAT holding at 94%.",
        "sent_at": "2026-05-21T14:10:00Z",
        "links": 1,
        "watchers": 1,
        "is_starred": False,
    },
    {
        "customer_name": "Shopify",
        "subject": "Renewal Proposal Submitted",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Natalie Reyes",
        "body": "Renewal proposal sent to Shopify's procurement team — "
        "awaiting sign-off.",
        "sent_at": "2026-08-13T09:00:00Z",
        "links": 1,
        "watchers": 5,
        "is_starred": True,
    },
    {
        "customer_name": "Shopify",
        "subject": "Re: Enablement Session Follow-Up",
        "sender_name": "Natalie Reyes",
        "recipient_name": "Support Team",
        "body": "Sent the recorded enablement session and slide deck to the "
        "Shopify team.",
        "sent_at": "2026-07-01T12:00:00Z",
        "links": 0,
        "watchers": 1,
        "is_starred": False,
    },
    {
        "customer_name": "Spotify",
        "subject": "Value Reinforcement — Quarterly ROI Summary",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Sarah Chen",
        "body": "Shared the ROI summary highlighting time saved across "
        "Spotify's CS team.",
        "sent_at": "2026-07-10T11:15:00Z",
        "links": 1,
        "watchers": 2,
        "is_starred": False,
    },
    {
        "customer_name": "Spotify",
        "subject": "Onboarding Milestone: Week 1 Complete",
        "sender_name": "Natalie Reyes",
        "recipient_name": "Edgar Holmes",
        "body": "Spotify's team completed week 1 setup — no blockers.",
        "sent_at": "2026-04-12T10:00:00Z",
        "links": 0,
        "watchers": 0,
        "is_starred": False,
    },
    {
        "customer_name": "Stripe",
        "subject": "Usage Analysis — API Call Volume",
        "sender_name": "Sarah Chen",
        "recipient_name": "Edgar Holmes",
        "body": "API call volume up sharply — flagging for the usage review.",
        "sent_at": "2026-08-02T08:30:00Z",
        "links": 2,
        "watchers": 3,
        "is_starred": False,
    },
    {
        "customer_name": "Stripe",
        "subject": "Escalation: Payment Webhook Delays",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Engineering",
        "body": "Webhook delivery delays reported by Stripe — escalating to "
        "engineering.",
        "sent_at": "2026-06-15T09:00:00Z",
        "links": 1,
        "watchers": 7,
        "is_starred": True,
    },
    {
        "customer_name": "Twilio",
        "subject": "Health Check Review — July",
        "sender_name": "Sarah Chen",
        "recipient_name": "Edgar Holmes",
        "body": "Twilio's health check came back strong — 9.1 score.",
        "sent_at": "2026-07-26T13:00:00Z",
        "links": 1,
        "watchers": 2,
        "is_starred": False,
    },
    {
        "customer_name": "Twilio",
        "subject": "Success Plan Updated",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Natalie Reyes",
        "body": "Updated Twilio's success plan with new Q3 milestones.",
        "sent_at": "2026-05-31T10:45:00Z",
        "links": 0,
        "watchers": 1,
        "is_starred": False,
    },
    {
        "customer_name": "Uber",
        "subject": "Executive Alignment Session Recap",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Sarah Chen",
        "body": "Productive session with Uber's leadership on renewal strategy.",
        "sent_at": "2026-08-19T15:00:00Z",
        "links": 1,
        "watchers": 3,
        "is_starred": False,
    },
    {
        "customer_name": "Uber",
        "subject": "Value Reinforcement Email",
        "sender_name": "Natalie Reyes",
        "recipient_name": "Edgar Holmes",
        "body": "Sent Uber a summary of adoption wins from this quarter.",
        "sent_at": "2026-06-06T09:30:00Z",
        "links": 0,
        "watchers": 1,
        "is_starred": False,
    },
    {
        "customer_name": "WeWork",
        "subject": "Escalation: Billing Discrepancy",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Support Team",
        "body": "WeWork flagged a billing discrepancy — investigating with "
        "finance.",
        "sent_at": "2026-07-03T08:00:00Z",
        "links": 2,
        "watchers": 5,
        "is_starred": True,
    },
    {
        "customer_name": "WeWork",
        "subject": "Health Check Review Notes",
        "sender_name": "Sarah Chen",
        "recipient_name": "Edgar Holmes",
        "body": "WeWork's health check shows moderate risk — churn signals "
        "present.",
        "sent_at": "2026-05-09T11:20:00Z",
        "links": 1,
        "watchers": 2,
        "is_starred": False,
    },
    {
        "customer_name": "Zoom",
        "subject": "Product Usage Analysis — August",
        "sender_name": "Sarah Chen",
        "recipient_name": "Edgar Holmes",
        "body": "Zoom's usage remains strong across all licensed seats.",
        "sent_at": "2026-08-21T10:00:00Z",
        "links": 1,
        "watchers": 2,
        "is_starred": False,
    },
    {
        "customer_name": "Zoom",
        "subject": "Onboarding Milestone Reached",
        "sender_name": "Natalie Reyes",
        "recipient_name": "Edgar Holmes",
        "body": "Zoom completed their final onboarding milestone.",
        "sent_at": "2026-06-23T09:00:00Z",
        "links": 0,
        "watchers": 1,
        "is_starred": False,
    },
]

# Account-level emails — customer_name/account_name must match an
# Account already seeded by seed_demo_accounts (under that customer).
DEMO_ACCOUNT_EMAILS = [
    {
        "customer_name": "Apple Inc",
        "account_name": "North America Enterprise",
        "subject": "Apr 2026 Success Plan Update — Account Review",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Tim Cook",
        "body": "Hi Tim, please find the updated success plan for Q2 attached. "
        "Key milestones highlighted in yellow.",
        "sent_at": "2026-03-28T10:15:00Z",
        "links": 2,
        "watchers": 3,
        "is_starred": True,
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "North America Enterprise",
        "subject": "Integration Sync Issue — Action Required",
        "sender_name": "Natalie Reyes",
        "recipient_name": "Edgar Holmes",
        "body": "The nightly Salesforce sync failed last night — engineering "
        "is investigating. ETA for fix is 4 hours.",
        "sent_at": "2026-03-20T08:30:00Z",
        "links": 0,
        "watchers": 5,
        "is_starred": True,
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple EMEA",
        "subject": "Renewal Prep: Expansion Proposal Draft",
        "sender_name": "Sarah Chen",
        "recipient_name": "Edgar Holmes",
        "body": "Draft expansion proposal attached. Looking to grow from 200 "
        "to 500 seats ahead of renewal.",
        "sent_at": "2026-03-15T14:00:00Z",
        "links": 1,
        "watchers": 2,
        "is_starred": False,
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple APAC",
        "subject": "Onboarding Milestone — APAC Rollout",
        "sender_name": "Natalie Reyes",
        "recipient_name": "Edgar Holmes",
        "body": "APAC rollout milestone hit — 3 offices fully onboarded.",
        "sent_at": "2026-06-20T09:00:00Z",
        "links": 0,
        "watchers": 1,
        "is_starred": False,
    },
    {
        "customer_name": "Apple Inc",
        "account_name": "Apple APAC",
        "subject": "Value Reinforcement — APAC Adoption",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Sarah Chen",
        "body": "APAC adoption metrics look great this quarter.",
        "sent_at": "2026-05-01T10:00:00Z",
        "links": 1,
        "watchers": 2,
        "is_starred": False,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "APAC Division",
        "subject": "Usage Analysis — APAC Division",
        "sender_name": "Sarah Chen",
        "recipient_name": "Edgar Holmes",
        "body": "Usage in the APAC division ticked up after the new rollout.",
        "sent_at": "2026-07-15T09:30:00Z",
        "links": 1,
        "watchers": 2,
        "is_starred": False,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "APAC Division",
        "subject": "Health Check Review — APAC",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Natalie Reyes",
        "body": "Health check for APAC division came back healthy.",
        "sent_at": "2026-05-26T11:00:00Z",
        "links": 0,
        "watchers": 1,
        "is_starred": False,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "Heinz Europe",
        "subject": "Executive Alignment — Europe Leadership",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Sarah Chen",
        "body": "Aligned with Heinz Europe's leadership on renewal timeline.",
        "sent_at": "2026-08-04T14:00:00Z",
        "links": 1,
        "watchers": 2,
        "is_starred": False,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "Heinz Europe",
        "subject": "Health Check Review — Europe",
        "sender_name": "Sarah Chen",
        "recipient_name": "Edgar Holmes",
        "body": "Heinz Europe health check stable, no concerns.",
        "sent_at": "2026-06-17T09:00:00Z",
        "links": 0,
        "watchers": 1,
        "is_starred": False,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "Kraft Heinz North America (Renamed)",
        "subject": "Value Reinforcement — North America",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Natalie Reyes",
        "body": "Shared adoption wins with Kraft Heinz North America.",
        "sent_at": "2026-07-12T10:00:00Z",
        "links": 1,
        "watchers": 1,
        "is_starred": False,
    },
    {
        "customer_name": "Kraft Heinz",
        "account_name": "Kraft Heinz North America (Renamed)",
        "subject": "Health Check Review Notes",
        "sender_name": "Sarah Chen",
        "recipient_name": "Edgar Holmes",
        "body": "North America account health check complete.",
        "sent_at": "2026-05-19T11:30:00Z",
        "links": 0,
        "watchers": 2,
        "is_starred": False,
    },
    {
        "customer_name": "Arista Networks",
        "account_name": "Arista Global",
        "subject": "Success Plan Created — Global Rollout",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Sarah Chen",
        "body": "Created a success plan for Arista Global's rollout.",
        "sent_at": "2026-07-29T10:00:00Z",
        "links": 2,
        "watchers": 3,
        "is_starred": False,
    },
    {
        "customer_name": "Arista Networks",
        "account_name": "Arista Global",
        "subject": "Escalation: Data Sync Delay",
        "sender_name": "Natalie Reyes",
        "recipient_name": "Engineering",
        "body": "Arista Global flagged a data sync delay — investigating.",
        "sent_at": "2026-05-13T08:30:00Z",
        "links": 1,
        "watchers": 4,
        "is_starred": True,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "account_name": "Hyatt Americas",
        "subject": "Renewal Proposal Submitted — Americas",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Natalie Reyes",
        "body": "Sent the renewal proposal to Hyatt Americas procurement.",
        "sent_at": "2026-07-21T09:00:00Z",
        "links": 1,
        "watchers": 3,
        "is_starred": False,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "account_name": "Hyatt Americas",
        "subject": "Usage Analysis — Americas",
        "sender_name": "Sarah Chen",
        "recipient_name": "Edgar Holmes",
        "body": "Americas usage trending up ahead of peak season.",
        "sent_at": "2026-05-05T10:15:00Z",
        "links": 0,
        "watchers": 1,
        "is_starred": False,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "account_name": "Hyatt EMEA & APAC",
        "subject": "Success Plan Updated — EMEA & APAC",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Natalie Reyes",
        "body": "Updated the success plan for the EMEA & APAC region.",
        "sent_at": "2026-08-10T13:00:00Z",
        "links": 1,
        "watchers": 2,
        "is_starred": False,
    },
    {
        "customer_name": "Hyatt Hotels Corporation",
        "account_name": "Hyatt EMEA & APAC",
        "subject": "Onboarding Milestone — EMEA & APAC",
        "sender_name": "Natalie Reyes",
        "recipient_name": "Edgar Holmes",
        "body": "EMEA & APAC hit their first onboarding milestone.",
        "sent_at": "2026-06-25T09:30:00Z",
        "links": 0,
        "watchers": 1,
        "is_starred": False,
    },
    {
        "customer_name": "Oracle",
        "account_name": "Oracle Cloud Division",
        "subject": "Escalation: Cloud Migration Delay",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Engineering",
        "body": "Oracle Cloud Division's migration is delayed — escalating.",
        "sent_at": "2026-08-15T08:00:00Z",
        "links": 2,
        "watchers": 5,
        "is_starred": True,
    },
    {
        "customer_name": "Oracle",
        "account_name": "Oracle Cloud Division",
        "subject": "Success Plan Created — Cloud Division",
        "sender_name": "Sarah Chen",
        "recipient_name": "Edgar Holmes",
        "body": "Created a success plan for the cloud division rollout.",
        "sent_at": "2026-06-09T10:00:00Z",
        "links": 1,
        "watchers": 2,
        "is_starred": False,
    },
    {
        "customer_name": "Pizza Hut",
        "account_name": "Pizza Hut International",
        "subject": "Usage Analysis — International",
        "sender_name": "Sarah Chen",
        "recipient_name": "Edgar Holmes",
        "body": "International usage steady, no major shifts.",
        "sent_at": "2026-07-07T09:00:00Z",
        "links": 0,
        "watchers": 1,
        "is_starred": False,
    },
    {
        "customer_name": "Pizza Hut",
        "account_name": "Pizza Hut International",
        "subject": "Executive Alignment — International Leadership",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Natalie Reyes",
        "body": "Aligned with international leadership on Q3 goals.",
        "sent_at": "2026-05-28T11:00:00Z",
        "links": 1,
        "watchers": 3,
        "is_starred": False,
    },
    {
        "customer_name": "Pizza Hut",
        "account_name": "Pizza Hut US Operations",
        "subject": "Onboarding Milestone — US Operations",
        "sender_name": "Natalie Reyes",
        "recipient_name": "Edgar Holmes",
        "body": "US Operations completed onboarding milestone 2.",
        "sent_at": "2026-08-23T10:00:00Z",
        "links": 0,
        "watchers": 2,
        "is_starred": False,
    },
    {
        "customer_name": "Pizza Hut",
        "account_name": "Pizza Hut US Operations",
        "subject": "Value Reinforcement — US Ops",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Sarah Chen",
        "body": "Shared ROI highlights with the US Operations team.",
        "sent_at": "2026-06-14T09:00:00Z",
        "links": 1,
        "watchers": 1,
        "is_starred": False,
    },
    {
        "customer_name": "Salesforce",
        "account_name": "Salesforce Core Platform",
        "subject": "Health Check Review — Core Platform",
        "sender_name": "Sarah Chen",
        "recipient_name": "Edgar Holmes",
        "body": "Core Platform health check complete, all green.",
        "sent_at": "2026-07-31T10:00:00Z",
        "links": 1,
        "watchers": 2,
        "is_starred": False,
    },
    {
        "customer_name": "Salesforce",
        "account_name": "Salesforce Core Platform",
        "subject": "Success Plan Updated — Core Platform",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Natalie Reyes",
        "body": "Updated Core Platform's success plan for H2.",
        "sent_at": "2026-05-10T11:00:00Z",
        "links": 0,
        "watchers": 1,
        "is_starred": False,
    },
    {
        "customer_name": "Shopify",
        "account_name": "Shopify Plus",
        "subject": "Renewal Proposal Submitted — Plus Tier",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Natalie Reyes",
        "body": "Sent the renewal proposal for the Plus tier account.",
        "sent_at": "2026-08-07T09:00:00Z",
        "links": 2,
        "watchers": 4,
        "is_starred": True,
    },
    {
        "customer_name": "Shopify",
        "account_name": "Shopify Plus",
        "subject": "Escalation: Checkout API Errors",
        "sender_name": "Sarah Chen",
        "recipient_name": "Engineering",
        "body": "Shopify Plus reported checkout API errors during peak load.",
        "sent_at": "2026-06-28T08:30:00Z",
        "links": 1,
        "watchers": 3,
        "is_starred": True,
    },
    {
        "customer_name": "Spotify",
        "account_name": "Spotify Business",
        "subject": "Usage Analysis — Business Tier",
        "sender_name": "Sarah Chen",
        "recipient_name": "Edgar Holmes",
        "body": "Business tier usage climbing steadily month over month.",
        "sent_at": "2026-07-18T10:00:00Z",
        "links": 1,
        "watchers": 2,
        "is_starred": False,
    },
    {
        "customer_name": "Spotify",
        "account_name": "Spotify Business",
        "subject": "Onboarding Milestone — Business Tier",
        "sender_name": "Natalie Reyes",
        "recipient_name": "Edgar Holmes",
        "body": "Business tier team completed onboarding milestone 1.",
        "sent_at": "2026-05-02T09:00:00Z",
        "links": 0,
        "watchers": 1,
        "is_starred": False,
    },
    {
        "customer_name": "Stripe",
        "account_name": "Stripe Payments",
        "subject": "Success Plan Created — Payments",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Sarah Chen",
        "body": "Created a success plan for the Payments account.",
        "sent_at": "2026-08-12T10:00:00Z",
        "links": 2,
        "watchers": 3,
        "is_starred": False,
    },
    {
        "customer_name": "Stripe",
        "account_name": "Stripe Payments",
        "subject": "Value Reinforcement — Payments",
        "sender_name": "Natalie Reyes",
        "recipient_name": "Edgar Holmes",
        "body": "Shared adoption wins with the Payments account team.",
        "sent_at": "2026-06-03T09:00:00Z",
        "links": 1,
        "watchers": 1,
        "is_starred": False,
    },
    {
        "customer_name": "WeWork",
        "account_name": "WeWork US",
        "subject": "Executive Alignment — US Leadership",
        "sender_name": "Edgar Holmes",
        "recipient_name": "Sarah Chen",
        "body": "Aligned with WeWork US leadership on renewal path.",
        "sent_at": "2026-07-25T14:00:00Z",
        "links": 1,
        "watchers": 2,
        "is_starred": False,
    },
    {
        "customer_name": "WeWork",
        "account_name": "WeWork US",
        "subject": "Health Check Review — US",
        "sender_name": "Sarah Chen",
        "recipient_name": "Edgar Holmes",
        "body": "WeWork US health check shows steady improvement.",
        "sent_at": "2026-05-16T10:00:00Z",
        "links": 0,
        "watchers": 1,
        "is_starred": False,
    },
]


class Command(BaseCommand):
    help = "Seeds demo Email rows under existing demo Customers/Accounts."

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

        for row in DEMO_CUSTOMER_EMAILS:
            try:
                customer = Customer.objects.get(organisation=org, name=row["customer_name"])
            except Customer.DoesNotExist:
                self.stderr.write(
                    f"  skipping email — no customer {row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            _, was_created = Email.objects.update_or_create(
                customer=customer,
                subject=row["subject"],
                sent_at=row["sent_at"],
                defaults={
                    "sender_name": row["sender_name"],
                    "recipient_name": row["recipient_name"],
                    "body": row["body"],
                    "links": row["links"],
                    "watchers": row["watchers"],
                    "is_starred": row["is_starred"],
                },
            )
            created += was_created
            updated += not was_created

        for row in DEMO_ACCOUNT_EMAILS:
            try:
                account = Account.objects.get(
                    customer__organisation=org,
                    customer__name=row["customer_name"],
                    name=row["account_name"],
                )
            except Account.DoesNotExist:
                self.stderr.write(
                    f"  skipping email — no account {row['account_name']!r} under "
                    f"{row['customer_name']!r} in {org.name}."
                )
                skipped += 1
                continue

            _, was_created = Email.objects.update_or_create(
                account=account,
                subject=row["subject"],
                sent_at=row["sent_at"],
                defaults={
                    "sender_name": row["sender_name"],
                    "recipient_name": row["recipient_name"],
                    "body": row["body"],
                    "links": row["links"],
                    "watchers": row["watchers"],
                    "is_starred": row["is_starred"],
                },
            )
            created += was_created
            updated += not was_created

        self.stdout.write(
            self.style.SUCCESS(
                f"{org.name}: created {created}, updated {updated}, "
                f"skipped {skipped} email(s)."
            )
        )
