# Privacy Policy

_Last updated: [DATE]_ · _This document is a POPIA- and GDPR-aware template, not legal advice. Have it reviewed by qualified counsel and complete every [PLACEHOLDER] before you rely on it._

This Privacy Policy explains how **[Nubi legal entity name] (Pty) Ltd** ("**Nubi**", "**we**", "**us**", "**our**") collects, uses, shares, and protects personal information when you use **Nubi Cloud** and the **nubi.io** website (together, the "**Service**").

It is written to meet the requirements of the **Protection of Personal Information Act 4 of 2013 ("POPIA")** in South Africa and the **EU/UK General Data Protection Regulation ("GDPR")**, and to apply whichever gives you the greater protection.

> **Self-hosted Nubi.** Nubi is open-core and can be self-hosted. **If you run Nubi on your own infrastructure, you (not Nubi) are the Responsible Party / Controller** for the data in that deployment, and this Policy does not apply to it — your own privacy notice does. This Policy governs the **managed Nubi Cloud** service and our website.

---

## 1. Who we are

| | |
|---|---|
| **Responsible Party / Controller** | [Nubi legal entity name] (Pty) Ltd, registration no. [REG NUMBER] |
| **Registered address** | [REGISTERED ADDRESS] |
| **Information Officer (POPIA)** | [NAME], reachable at **privacy@nubi.io** |
| **EU/UK representative (GDPR Art. 27)** | [EU/UK REPRESENTATIVE NAME & ADDRESS, if applicable] |
| **General privacy contact** | **privacy@nubi.io** · billing queries: **billing@nubi.io** |

For most personal information we decide why and how it is processed and act as a **Responsible Party (POPIA) / Controller (GDPR)**. For the data you load through your connected data sources ("**Customer Data**"), we act as your **Operator (POPIA) / Processor (GDPR)** — see [section 5](#5-customer-data--our-role-as-operator--processor).

---

## 2. Definitions

- **Personal information / personal data** — information relating to an identifiable living natural person (and, under POPIA, an identifiable existing juristic person).
- **Processing** — any operation on personal information (collection, storage, use, sharing, deletion, etc.).
- **Data subject** — the person to whom personal information relates.
- **Customer Data** — data you connect, query, store, or generate through the Service, including data from your warehouses and databases.
- **Sub-operator / sub-processor** — a third party we engage to process personal information on our behalf.

---

## 3. Personal information we collect

### 3.1 Information you give us

| Category | Examples | Source |
|---|---|---|
| **Account data** | Name, email address, profile avatar, hashed password | You, at sign-up |
| **Authentication data** | Google account identifier and basic profile, if you sign in with Google | Google OAuth |
| **Organization data** | Organization and project names, team members you invite, roles | You / org admins |
| **Billing contact data** | Billing name, email, and the information shown on invoices | You (Nubi Cloud) |
| **Support & communications** | Messages you send us, support requests | You |

We use **argon2id** to hash passwords; we never store passwords in plaintext. Payments are processed by **Paystack** — **we never receive or store your full card number** (see [section 7](#7-sub-operators--sub-processors)).

### 3.2 Information we collect automatically

| Category | Examples | Purpose |
|---|---|---|
| **Sign-in events** | IP address, user-agent (browser/device), timestamp | Security, fraud prevention, account protection |
| **Session data** | Session and refresh tokens (cookies) | Keeping you signed in |
| **Usage / metering** | Counts of compute units, embedded sessions, AI calls, storage, agent runs | Operating the Service and billing |
| **Preferences** | Active organization/project (stored in your browser's local storage) | Remembering your workspace |

We do **not** use third-party advertising or cross-site tracking cookies.

### 3.3 Information from connected data sources

When you connect a data source, the credentials are encrypted (see [section 9](#9-how-we-protect-your-information)) and the data you query becomes **Customer Data**, processed under [section 5](#5-customer-data--our-role-as-operator--processor).

---

## 4. Why we process personal information, and our lawful basis

| Purpose | POPIA lawful basis (s11) | GDPR lawful basis (Art. 6) |
|---|---|---|
| Create and operate your account | Performance of a contract | Art. 6(1)(b) contract |
| Provide the Service (queries, dashboards, flows, embedding) | Performance of a contract | Art. 6(1)(b) contract |
| Billing, invoicing, fraud prevention | Contract / legal obligation | Art. 6(1)(b)/(c) |
| Security, abuse detection, audit of sign-ins | Legitimate interest | Art. 6(1)(f) legitimate interests |
| Product improvement (aggregated/anonymised) | Legitimate interest | Art. 6(1)(f) |
| Service and security notices | Legitimate interest / legal obligation | Art. 6(1)(f)/(c) |
| Optional product/marketing emails | Consent | Art. 6(1)(a) consent |

Where we rely on **consent**, you may withdraw it at any time (see [section 11](#11-your-rights)); withdrawal does not affect processing already carried out.

---

## 5. Customer Data — our role as Operator / Processor

For Customer Data you bring into the Service, **you are the Responsible Party / Controller** and **Nubi is your Operator / Processor**. We process Customer Data **only on your documented instructions** to provide the Service, and:

- **Connector credentials** are encrypted at rest with **AES-256-GCM** (with key versioning and rotation). For self-hosted or VPC-bridged connectors, your credentials and data can remain inside your own network.
- **Per-tenant isolation** is enforced server-side via row-level security (RLS): embed tokens carry signed policy claims that are injected into queries and **cannot be overridden by the browser or request body**.
- We **do not sell** Customer Data or personal information, and we **do not use Customer Data to build or train our own models**.
- A **Data Processing Addendum (DPA)** with EU Standard Contractual Clauses is available to Nubi Cloud customers on request at **privacy@nubi.io**.

You are responsible for having a lawful basis to process the data you connect and for the policies/claims you mint for embedded viewers.

---

## 6. Artificial-intelligence features

Nubi offers AI features — natural-language **text-to-SQL**, an **AI chat agent**, and **MCP** tools. When you use them:

- Your **prompt** and **relevant schema/metadata** (e.g. table and column names for the selected connector) are sent to a third-party large-language-model provider — **Anthropic (Claude)** by default; an operator may configure an alternative provider (e.g. OpenAI or Google) for a self-hosted deployment.
- AI output is **generated by the model, may be inaccurate, and should be reviewed before you rely on it**.
- We do **not** use your prompts or Customer Data to train Nubi models, and we select AI sub-processors that contractually do not train their models on our API content.
- AI features are optional. If you do not use them, no content is sent to an LLM provider.

---

## 7. Sub-operators / sub-processors

We engage the following categories of sub-processor to deliver the Service. Each is bound by contract to appropriate confidentiality and security obligations.

| Sub-processor | Purpose | Region |
|---|---|---|
| **Paystack** | Payment processing (cards) | South Africa |
| **Anthropic** | AI / LLM inference for AI features | United States |
| **E2B / Modal** | On-demand Python "server kernel" compute | [REGION] |
| **Google** | OAuth sign-in (if you choose it) | Global |
| **[Hosting / object-storage provider]** | Hosting, exports, datasets, cache (S3/R2-compatible) | [REGION] |
| **[Email / SMTP provider]** | Transactional email (invoices, notices) | [REGION] |
| **[FX-rate provider]** | Daily USD→ZAR exchange rate for billing | — |

A current list of sub-processors is available on request at **privacy@nubi.io**. We will give notice of material changes so you may object as permitted by your agreement.

---

## 8. Cross-border transfers

Some sub-processors are located outside South Africa and the EU/UK. When we transfer personal information across borders we rely on a lawful transfer mechanism:

- **POPIA (s72):** transfer to a recipient subject to a law, binding corporate rules, or agreement providing an adequate level of protection; or with your consent; or where necessary to perform our contract with you.
- **GDPR (Chapter V):** an adequacy decision where one exists, or **Standard Contractual Clauses (SCCs)** with appropriate supplementary measures.

Details of the safeguards for a specific transfer are available on request.

---

## 9. How we protect your information

We apply technical and organisational measures appropriate to the risk, including:

- **Encryption in transit** (TLS) and **encryption of connector secrets at rest** (AES-256-GCM, key-versioned).
- **argon2id** password hashing.
- **Signed tokens** — first-party sessions (HS256) and embed tokens (RS256/ES256), with origin pinning.
- **Server-side row-level security** enforcing per-tenant data isolation.
- Access controls, least-privilege, and monitoring of sign-in events.

No system is perfectly secure; we cannot guarantee absolute security. If a security compromise affecting your personal information occurs, we will notify you and the relevant regulator as required by POPIA (s22) and the GDPR (Arts. 33–34).

---

## 10. Retention

We keep personal information only as long as necessary for the purposes above:

- **Account & organization data** — for the life of your account, then deleted or anonymised within a reasonable period after closure.
- **Customer Data** — for the life of your subscription; on termination it is deleted after the export window described in our Terms (subject to backups expiring on their normal cycle).
- **Billing & invoice records** — retained as required by tax and company law (typically **5 years** in South Africa).
- **Sign-in/security logs** — retained for a limited period for security purposes, then deleted.

---

## 11. Your rights

Subject to the conditions and exceptions in POPIA and the GDPR, you may:

- **Access** the personal information we hold about you (POPIA s23; GDPR Art. 15).
- **Correct** inaccurate or incomplete information (POPIA s24; GDPR Art. 16).
- **Delete / erase** information where permitted (POPIA s24; GDPR Art. 17).
- **Restrict** or **object to** processing, including processing based on legitimate interests (GDPR Arts. 18, 21; POPIA s11(3)).
- **Port** information you provided to us, in a structured, machine-readable format (GDPR Art. 20).
- **Withdraw consent** at any time where processing is based on consent.
- **Not be subject to solely-automated decisions** with legal/similarly significant effects — **Nubi does not make such decisions** about you. AI features assist you; they do not decide about you.

To exercise any right, email **privacy@nubi.io**. We will respond within the time required by law and may need to verify your identity. Where you are an end-user of a **Nubi Cloud customer's** embedded analytics, please contact that customer (the Controller); we will assist them as their Operator.

You also have the right to lodge a complaint with a regulator:

- **South Africa — Information Regulator:** enquiries / complaints via **inforeg.org.za** (POPIA & PAIA).
- **EU/UK:** your local **supervisory authority** (for the UK, the ICO).

---

## 12. Direct marketing

We send you **service and security notices** as part of operating the Service. We will only send **marketing** communications where you have opted in (or as otherwise permitted by POPIA s69), and **every marketing email contains an unsubscribe link**. You can opt out at any time via that link or by emailing **privacy@nubi.io**.

---

## 13. Cookies & local storage

We use only **first-party, functional** cookies and browser storage:

- **Session / refresh tokens** — to keep you signed in securely.
- **Local storage** — your active organization and project, and UI preferences.

We do not use advertising or cross-site tracking cookies. You can clear cookies/storage in your browser, but doing so will sign you out.

---

## 14. Children

The Service is intended for **business use and is not directed at children under 18**. We do not knowingly collect personal information from children. If you believe a child has provided us personal information, contact **privacy@nubi.io** and we will delete it.

---

## 15. Changes to this Policy

We may update this Policy from time to time. We will post the new version here with an updated "Last updated" date and, for material changes, give reasonable notice (e.g. by email or in-product). Continued use of the Service after a change takes effect means you accept the updated Policy.

---

## 16. Contact us

Questions, requests, or complaints about privacy:

- **Email:** privacy@nubi.io
- **Information Officer:** [NAME]
- **Post:** [REGISTERED ADDRESS]

See also our **[Terms of Service](/terms)**.
