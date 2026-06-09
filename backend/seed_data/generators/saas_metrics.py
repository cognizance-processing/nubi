"""``saas_metrics`` dataset — subscription business over ~24 months.

A B2B SaaS with per-seat pricing.  Accounts sign up at an accelerating rate,
then live a deterministic month-by-month lifecycle (upgrade / downgrade /
churn), producing a realistic growing-MRR story with visible churn and clean
cohort retention curves.

Schema
------
``saas_plans``               : plan_id PK, plan, price_per_seat, tier
``saas_accounts``            : account_id PK, account, segment, industry,
                               country, channel, signup_month
``saas_subscriptions``       : subscription_id PK, account_id FK, plan_id FK,
                               plan, seats, mrr, start_month, end_month (NULL = active)
``saas_subscription_events`` : event_id PK, month, account_id FK, event_type
                               (new|upgrade|downgrade|churn), from_plan, to_plan, mrr_delta
``saas_invoices``            : invoice_id PK, month, account_id FK, plan, seats,
                               amount, status (paid|open|overdue)
                               — one row per active account-month; SUM(amount)
                               per month IS the MRR series.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from seed_data.generators._common import N_MONTHS, iter_months, noise, weighted_pick

if TYPE_CHECKING:
    import pyarrow as pa

TABLES = (
    "saas_plans",
    "saas_accounts",
    "saas_subscriptions",
    "saas_subscription_events",
    "saas_invoices",
)

# plan_id, plan, price per seat / month, tier order
PLANS = [
    (1, "Starter", 9.0, 1),
    (2, "Growth", 19.0, 2),
    (3, "Pro", 39.0, 3),
    (4, "Enterprise", 79.0, 4),
]
_PLAN_BY_TIER = {tier: (pid, name, price) for pid, name, price, tier in PLANS}

SEGMENTS = ["SMB", "Mid-Market", "Enterprise"]
INDUSTRIES = ["Fintech", "Retail", "Healthcare", "Logistics", "Media", "Education", "Manufacturing", "Hospitality"]
COUNTRIES = [
    ("South Africa", 0.30), ("United States", 0.22), ("United Kingdom", 0.12),
    ("Germany", 0.08), ("Netherlands", 0.05), ("Kenya", 0.06),
    ("Nigeria", 0.07), ("Australia", 0.05), ("Brazil", 0.05),
]
ACQ_CHANNELS = [
    ("Organic search", 0.30), ("Paid search", 0.18), ("Referral", 0.16),
    ("Outbound", 0.14), ("Partner", 0.12), ("Event", 0.10),
]

_NAME_PREFIX = [
    "Acme", "Nova", "Atlas", "Zenith", "Kite", "Harbor", "Lumen", "Vertex",
    "Baobab", "Mosaic", "Quill", "Orbit", "Karoo", "Delta", "Forge", "Pinnacle",
    "Causeway", "Meridian", "Savanna", "Crest", "Ironwood", "Halcyon", "Marula",
    "Summit", "Beacon", "Cobalt", "Drift", "Ember", "Fathom", "Granite",
]
_NAME_SUFFIX = [
    "Analytics", "Systems", "Labs", "Logistics", "Capital", "Health", "Media",
    "Retail", "Mobility", "Foods", "Energy", "Mining", "Travel", "Studios",
    "Networks", "Robotics", "Insurance", "Payments", "Learning", "Security",
    "Freight", "Farms", "Properties", "Telecom", "Outfitters", "Works",
    "Group", "Partners", "Dynamics", "Solutions",
]

N_ACCOUNTS = 900

# Monthly lifecycle probabilities by segment: (churn, upgrade, downgrade)
_LIFECYCLE = {
    "SMB": (0.032, 0.018, 0.008),
    "Mid-Market": (0.018, 0.022, 0.006),
    "Enterprise": (0.008, 0.016, 0.004),
}


def _signup_weights() -> list[tuple[int, float]]:
    """Month-index weights: signups accelerate over the window (~+7%/mo)."""
    return [(idx, 1.0 + 0.07 * idx) for idx in range(N_MONTHS)]


def _initial_tier(segment: str, account_id: int) -> int:
    r = noise("tier", account_id)
    if segment == "SMB":
        return 1 if r < 0.70 else 2
    if segment == "Mid-Market":
        return 2 if r < 0.55 else 3
    return 3 if r < 0.40 else 4


def _seats(segment: str, account_id: int) -> int:
    r = noise("seats", account_id)
    if segment == "SMB":
        return 2 + int(r * 11)        # 2–12
    if segment == "Mid-Market":
        return 8 + int(r * 53)        # 8–60
    return 30 + int(r * 151)          # 30–180


def build_tables() -> "dict[str, pa.Table]":
    """Build the full SaaS dataset as Arrow tables (deterministic)."""
    import pyarrow as pa

    months = iter_months()
    month_strs = [m for _, _, m in months]
    signup_w = _signup_weights()

    accounts: list[tuple] = []          # account_id, account, segment, industry, country, channel, signup_month
    subscriptions: list[tuple] = []     # subscription_id, account_id, plan_id, plan, seats, mrr, start_month, end_month
    events: list[tuple] = []            # event_id, month, account_id, event_type, from_plan, to_plan, mrr_delta
    invoices: list[tuple] = []          # invoice_id, month, account_id, plan, seats, amount, status

    sub_id = 0
    event_id = 0
    invoice_id = 0

    for aid in range(1, N_ACCOUNTS + 1):
        name = f"{_NAME_PREFIX[(aid - 1) % 30]} {_NAME_SUFFIX[((aid - 1) // 30) % 30]}"
        r_seg = noise("segment", aid)
        segment = "SMB" if r_seg < 0.62 else ("Mid-Market" if r_seg < 0.92 else "Enterprise")
        industry = INDUSTRIES[int(noise("industry", aid) * len(INDUSTRIES)) % len(INDUSTRIES)]
        country = weighted_pick(COUNTRIES, "country", aid)
        channel = weighted_pick(ACQ_CHANNELS, "channel", aid)
        signup_idx = weighted_pick(signup_w, "signup", aid)
        signup_month = month_strs[signup_idx]

        accounts.append((aid, name, segment, industry, country, channel, signup_month))

        # ── Month-by-month lifecycle simulation ──────────────────────────────
        churn_p, up_p, down_p = _LIFECYCLE[segment]
        tier = _initial_tier(segment, aid)
        seats = _seats(segment, aid)
        plan_id, plan, price = _PLAN_BY_TIER[tier]
        mrr = round(price * seats, 2)

        sub_id += 1
        cur_sub = [sub_id, aid, plan_id, plan, seats, mrr, signup_month, None]

        event_id += 1
        events.append((event_id, signup_month, aid, "new", None, plan, mrr))

        active = True
        for m_idx in range(signup_idx, N_MONTHS):
            m_str = month_strs[m_idx]
            if not active:
                break

            if m_idx > signup_idx:
                r = noise("lifecycle", aid, m_str)
                if r < churn_p:
                    # Churn: close the subscription at this month.
                    event_id += 1
                    events.append((event_id, m_str, aid, "churn", plan, None, round(-mrr, 2)))
                    cur_sub[7] = m_str
                    subscriptions.append(tuple(cur_sub))
                    active = False
                    break
                if r < churn_p + up_p and tier < 4:
                    # Upgrade: close period, open the next tier (seats grow ~15%).
                    old_plan, old_mrr = plan, mrr
                    cur_sub[7] = m_str
                    subscriptions.append(tuple(cur_sub))
                    tier += 1
                    seats = max(seats, int(round(seats * 1.15)))
                    plan_id, plan, price = _PLAN_BY_TIER[tier]
                    mrr = round(price * seats, 2)
                    sub_id += 1
                    cur_sub = [sub_id, aid, plan_id, plan, seats, mrr, m_str, None]
                    event_id += 1
                    events.append((event_id, m_str, aid, "upgrade", old_plan, plan, round(mrr - old_mrr, 2)))
                elif r < churn_p + up_p + down_p and tier > 1:
                    # Downgrade one tier.
                    old_plan, old_mrr = plan, mrr
                    cur_sub[7] = m_str
                    subscriptions.append(tuple(cur_sub))
                    tier -= 1
                    plan_id, plan, price = _PLAN_BY_TIER[tier]
                    mrr = round(price * seats, 2)
                    sub_id += 1
                    cur_sub = [sub_id, aid, plan_id, plan, seats, mrr, m_str, None]
                    event_id += 1
                    events.append((event_id, m_str, aid, "downgrade", old_plan, plan, round(mrr - old_mrr, 2)))

            # Invoice for this active month.
            invoice_id += 1
            if m_idx < N_MONTHS - 2:
                status = "paid"
            else:
                r_pay = noise("invstatus", aid, m_str)
                status = "paid" if r_pay < 0.90 else ("overdue" if r_pay < 0.97 else "open")
            invoices.append((invoice_id, m_str, aid, plan, seats, mrr, status))

        if active:
            subscriptions.append(tuple(cur_sub))

    def col(rows: list[tuple], names: list[str]) -> dict[str, list]:
        return {n: [r[i] for r in rows] for i, n in enumerate(names)}

    return {
        "saas_plans": pa.table(col(PLANS, ["plan_id", "plan", "price_per_seat", "tier"])),
        "saas_accounts": pa.table(col(
            accounts, ["account_id", "account", "segment", "industry", "country", "channel", "signup_month"]
        )),
        "saas_subscriptions": pa.table(col(
            subscriptions,
            ["subscription_id", "account_id", "plan_id", "plan", "seats", "mrr", "start_month", "end_month"],
        )),
        "saas_subscription_events": pa.table(col(
            events, ["event_id", "month", "account_id", "event_type", "from_plan", "to_plan", "mrr_delta"]
        )),
        "saas_invoices": pa.table(col(
            invoices, ["invoice_id", "month", "account_id", "plan", "seats", "amount", "status"]
        )),
    }
