# Billing, plans & usage wallet

This is the practical guide to paying for Nubi: the five plans, what we meter (and the long list of things we never charge for), how the prepaid **usage wallet** covers overages, how monthly invoices and VAT work, and exactly where to find all of it in your **organization settings → Billing** page.

Everything here is managed from one screen: **Billing** (`/billing`). It is org-scoped, so each organization has its own plan, wallet, and invoices.

> **One principle.** Nubi only meters things that cost us money to run for you. Dashboard *views*, *editors*, and *viewers* are free at every tier — you are never charged per seat. See [What we meter (and never meter)](#what-we-meter-and-never-meter).

---

## Prices in USD, billed in ZAR

Nubi's plan prices are **set in US dollars (USD)** and **charged in South African Rand (ZAR)**.

- Your **USD price is your anchor** — it stays fixed for the duration of your plan.
- The ZAR amount charged each cycle is converted from USD using a **daily exchange rate** refreshed from a tier-1 FX provider.
- Because the rate moves day to day, the **ZAR amount may vary slightly** from one cycle to the next. This is disclosed at checkout and on the Billing page.

The current rate and when it was last refreshed are shown right on the wallet card, for example:

```
ZAR balance calculated at  1 USD = R 16.26  (updated 8 Jun 2026).
```

All payments are processed by **Paystack**. You are redirected to Paystack's hosted, PCI-compliant page to pay — Nubi never stores your raw card number.

---

## The five plans

Nubi has five plans. **Every plan includes unlimited editors and viewers** — there is no per-seat charge anywhere. You move up a plan when you need more *connectors, dashboards, flows, embedded volume, AI calls, governance, or support* — not to unlock basic usage.

| Plan | USD / mo | Annual (USD) | Who it's for |
|------|----------|--------------|--------------|
| **Free** | $0 | $0 | A real, production-usable free tier — no time limit |
| **Starter** | $9 | $90 (2 months free) | Individuals, side-projects, early-stage startups |
| **Team** | $49 | $490 (2 months free) | Small teams that outgrew Starter but don't need Pro |
| **Pro** | $149 | $1,490 (2 months free) | Growing teams shipping embedded analytics |
| **Enterprise** | from $1,000 | from $10,000 | Unlimited scale, BYOC, SLA, dedicated support |

Annual billing charges 10 months and gives you 2 months free.

### Included quotas by plan

These are the monthly allowances *included* in each plan. Usage beyond them is an overage drawn from your [usage wallet](#the-usage-wallet) (Free has hard stops instead — see below).

| Included allowance | Free | Starter | Team | Pro | Enterprise |
|--------------------|------|---------|------|-----|------------|
| Editors & viewers | Unlimited | Unlimited | Unlimited | Unlimited | Unlimited |
| Connectors | 3 | 5 | 15 | All | Unlimited |
| Dashboards | 5 | 10 | 30 | 100 | Unlimited |
| Scheduled flows | 2 | 3 | 8 | 20 | Unlimited |
| Storage | 1 GB | 5 GB | 15 GB | 50 GB | 500 GB+ |
| Compute units / mo | 500 | 2,000 | 6,000 | 15,000 | 200,000 |
| Embedded sessions / mo | 0 | 1,000 | 5,000 | 25,000 | Unlimited |
| Agent / kernel runs / mo | 0 | 0 | 10 | 50 | 1,000 |
| AI calls / mo | 0 | 5 | 15 | 50 | 500 |
| Max rows / query | 10,000 | 100,000 | 1,000,000 | 5,000,000 | Unlimited |

### Governance & support by plan

| Feature | Free | Starter | Team | Pro | Enterprise |
|---------|------|---------|------|-----|------------|
| Remove Nubi branding | — | — | Yes | Full white-label | Full + custom SDK |
| Row-level security | — | Basic | Full (JWT) | Full (JWT) | Full (JWT) |
| Google SSO | — | Yes | Yes | Yes | Yes |
| SAML SSO | — | — | — | 1 IdP *(coming soon)* | Unlimited IdPs *(coming soon)* |
| SCIM provisioning | — | — | — | — | Coming soon |
| Custom domain | — | — | — | Yes | Yes |
| Audit log retention | None | 7 days | 30 days | 90 days | Unlimited |
| Uptime SLA | — | — | — | 99.5% | 99.95% |
| Support | Community | Email | Email | Email + Slack | Dedicated CSM, 24/7 P1 |

Enterprise is custom-quoted (BYOC, on-prem, air-gap, and a BAA are available on request). The $1,000/mo figure is a floor.

---

## What we meter (and never meter)

### Never charged

Nubi never bills for dimensions that cost us essentially nothing to run, because dashboards compute in the viewer's browser (DuckDB-WASM):

- **Dashboard views** — compute runs in your users' browsers.
- **Per-viewer "seats"** — people who only look at dashboards are free.
- **Editor seats** — unlimited at every plan, including Free.
- **Counts** of connectors, dashboards, saved queries, and flow definitions (beyond your plan's quota limit — these are caps, not meters).
- **Warehouse compute for cached / pre-aggregated reads.**

### Metered

We meter only the five dimensions that map to a real cost we pay on your behalf:

| Metered dimension | What it measures |
|-------------------|------------------|
| **Storage (GB)** | Object storage your org consumes |
| **Compute units (CU)** | Flow runs + query compute on Nubi's nodes. Each server-side query execution records one event (compute-seconds + bytes returned); cached reads are never metered |
| **Embedded sessions** | Embedded dashboard loads per month |
| **AI calls** | Text-to-SQL, MCP tools, and agent steps |
| **Agent / kernel runs** | On-demand server kernels (Team & Pro+) |

---

## Overages and the wallet model

When you exceed a plan's included quota, the extra usage is an **overage**. Overages let you buy more of *one* thing — say more AI calls — without jumping a whole plan.

Overage rates are **fixed in ZAR**: Storage R1.50/GB/mo, Compute R100/1,000 CU, AI R5/call, Embedded R50/10,000 sessions, Agent/kernel runs R2/run. Agent/kernel run overages are **not available on the Starter plan**.

| Dimension | Rate | Unit |
|-----------|------|------|
| Storage | R1.50 | / GB / mo (~$0.09 USD) |
| Compute | R100 | / 1,000 CU |
| AI calls | R5 | / call |
| Embedded sessions | R50 | / 10,000 |
| Agent / kernel runs | R2.00 | / run (≈ $0.12 USD at reference rate) |

How overages are paid:

1. They are drawn from your prepaid **usage wallet** first.
2. If the wallet runs out, the remainder lands on your **next monthly invoice**.

> **Free plan has no overages.** On Free, exceeding a quota is a hard stop — you upgrade to continue rather than paying an overage.

---

## The usage wallet

The **Usage Wallet** is a prepaid credit balance for your organization. Metered overages draw it down. You'll find it on the Billing page under the **Usage Wallet** heading.

The wallet card shows:

- **Balance** — displayed prominently in **ZAR**, with the **USD** equivalent below it. The amount turns amber when low (under $5) and red at zero.
- **Spend this month** — a meter showing how much you've spent against your optional monthly spend cap.
- **Usage this month** — a breakdown by category: AI / LLM calls, Storage, Compute, Embedded sessions, and Overage.
- **The FX line** — the exact rate used to convert your USD balance to ZAR, and when it was last updated.
- **Recent transactions** — the last 10 ledger entries (credits and debits).

### What the ledger entries mean

Each line in **Recent transactions** is one credit (green, `+`) or debit (red, `−`) with the running balance after it:

| Entry | Meaning |
|-------|---------|
| Manual top-up | You added credit via Paystack |
| Auto top-up | Your saved card was charged automatically |
| Promo credit | Promotional / granted credit |
| Top-up failed | An auto top-up attempt failed (no balance change) |
| AI / LLM usage | An AI / agent call was charged |
| Storage | A storage overage was charged |
| Compute | A compute-unit overage was charged |
| Embedded sessions | An embedded-session overage was charged |
| Overage | A generic overage debit |
| Credit / Debit adjustment | A manual adjustment by support |
| Credit expiry | Credit that expired |

### Add credit manually

To top up the wallet yourself:

1. On the Billing page, in the **Usage Wallet** card, click **Add credit**.
2. Pick a preset amount ($10, $25, $50, $100, $250) or choose **Custom** and type any amount (minimum **$1.00**).
3. Click **Add $…**. You're redirected to **Paystack** to pay; the ZAR equivalent is charged at the current rate.
4. On return you'll see a green **"Payment successful — your wallet has been topped up."** banner and the new balance.

Your card is saved automatically after a manual top-up, which then enables auto top-up.

### Auto top-up

Auto top-up keeps the wallet from hitting zero by charging your saved card when the balance runs low. Configure it under the **Auto Top-up Settings** card.

> You must do a **manual top-up first** to put a card on file. Until then, the auto top-up toggle is disabled and the card shows as "No saved payment method."

1. Toggle **Enable auto-topup** on.
2. Set **Balance threshold** — when the balance drops below this, a charge is triggered (default $10).
3. Set **Top-up amount** — how much credit to buy each time it triggers (default $50, minimum $5).
4. (Optional) Tick **Monthly auto-topup cap** — a *soft* ceiling on total auto top-ups per calendar month. Once reached, auto top-up pauses for the rest of the month, but manual top-ups still work.
5. (Optional) Tick **Monthly spend cap** — a *hard* stop (see below).
6. Click **Save settings**. The saved card (brand and last 4 digits) is shown for confirmation.

### Spend caps and the zero-balance stop

Two independent safety limits protect you from runaway spend:

- **Monthly spend cap** (hard): once your cumulative metered usage for the month exceeds this amount, metered actions beyond your plan's included quota are **refused with an error** until the next month begins, and auto top-up halts. Manual top-ups are still allowed.
- **Zero balance** (hard): if the wallet reaches zero and your included quota is used up, metered usage is **paused** — the wallet card shows *"Balance depleted — usage beyond your included quota is paused."* Top up to resume.

Note that your plan's *included* quota always works regardless of wallet balance — these limits only affect overage usage.

---

## Upgrading or changing your plan

The **Plans** section of the Billing page shows every plan as a card. Your current plan is labelled **Current plan**.

1. Click the upgrade button on the plan you want (Enterprise opens an email to sales instead).
2. You're redirected to **Paystack** to complete checkout.
3. On return you'll see **"Payment successful — your plan has been updated."** If you cancel, you'll see **"Checkout was cancelled — no charge was made."** and nothing is charged.

The **Current plan** card at the top shows your plan badge, seat usage, renewal date, and trial end date if you're on a trial. If you're on a paid plan, **Manage billing** opens the Paystack billing portal where you can update your card or cancel.

---

## Tracking usage this cycle

The **This billing cycle** card shows live usage against your included quotas so you can see an overage coming before it lands on an invoice. For each metered dimension — Compute units, AI calls, Embedded sessions, Storage, Agent runs — it shows a progress bar of **used / included**, turning amber if you've gone over.

At the bottom it shows **Projected overage this cycle** (in ZAR) and a reminder:

> Overages draw from your usage wallet first; anything beyond that is added to your next monthly invoice.

---

## Monthly invoices, PDFs & VAT

Your subscription is billed monthly via Paystack. Any overage not already covered by your wallet is added to that invoice.

The **Invoices** card lists your invoice history. Each row shows the invoice number, date, total (in ZAR), a status badge (**paid**, **pending**, **past due**, **draft**, or **void**), and a **download** button.

- Click the **download** icon to save the invoice as a **PDF**.
- An invoice that includes VAT is marked **(incl. VAT)** next to its total.
- Your first invoice appears after your first billing cycle.

### VAT

Whether VAT appears on your invoice depends on the issuing entity:

- If the issuer is **VAT-registered**, the PDF is a **TAX INVOICE** and itemizes **Subtotal**, **VAT @ <rate>%**, and **Total** in ZAR, including the issuer's VAT number.
- If the issuer is **not VAT-registered**, the PDF is a plain **INVOICE** with the note *"No VAT charged: the issuing entity is not VAT-registered."*

---

## FAQ

**Why aren't there per-viewer charges?**
Dashboards compute in the user's browser, so an extra viewer costs us essentially nothing — and we don't bill you for it. You pay for storage, compute, embedded volume, and AI, never for someone looking at a chart.

**Will my ZAR price change month to month?**
Possibly, slightly. Your USD anchor price is fixed, but the ZAR conversion uses a daily exchange rate, so the charged ZAR can drift a little between cycles.

**What happens if my wallet hits zero?**
Your plan's included quota keeps working. Only *overage* usage pauses until you top up (or auto top-up fires).

**Can I cap my spend?**
Yes — set a **Monthly spend cap** in Auto Top-up Settings for a hard monthly ceiling, and a **Monthly auto-topup cap** for a soft limit on automatic recharges.

**Is the Free plan really usable in production?**
Yes. Free includes unlimited editors and viewers and the in-browser kernel, with no time limit. You upgrade for more connectors, embed volume, AI, or governance.

---

## Related

- [Embedding](/docs/embedding) — embedded sessions, the main driver of plan selection.
- [Flows](/docs/flows) — compute units and agent/kernel runs come from flow execution.
- [AI, Chat & MCP](/docs/ai-and-mcp) — what counts as an AI call.

*Questions about billing? Contact billing@nubi.io.*
