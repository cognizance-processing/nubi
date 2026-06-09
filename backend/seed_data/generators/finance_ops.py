"""``finance_ops`` dataset — billing, collections, spend and headcount.

A scale-up's finance picture over ~24 months: invoice volume grows ~5x while
expenses (driven by headcount) grow steadily, so the company burns cash early
and turns cash-positive in the back half — a clean cashflow/burn narrative.
A deterministic share of invoices stays unpaid, with due dates spread so the
AR-aging buckets (Current / 1-30 / 31-60 / 61-90 / 90+) are all populated as
of the demo "today" (2026-05-31).

Schema
------
``fin_invoices``  : invoice_id PK, customer, month, issue_date, due_date,
                    amount, status (paid|open|overdue), paid_date (NULL if unpaid)
``fin_payments``  : payment_id PK, invoice_id FK, customer, month (payment month),
                    paid_date, amount, method
``fin_expenses``  : expense_id PK, month, department, category, amount
``fin_headcount`` : month, department, headcount
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from seed_data.generators._common import iter_months, noise, seasonality, weighted_pick

if TYPE_CHECKING:
    import pyarrow as pa

TABLES = ("fin_invoices", "fin_payments", "fin_expenses", "fin_headcount")

# The demo "as of" date — month window ends 2026-05.
AS_OF = date(2026, 5, 31)

# department → (starting headcount, monthly growth, avg monthly salary)
DEPARTMENTS = {
    "Engineering": (14, 0.55, 9500.0),
    "Sales": (8, 0.35, 8200.0),
    "Marketing": (5, 0.20, 7000.0),
    "Customer Success": (6, 0.25, 6000.0),
    "Finance": (3, 0.08, 7800.0),
    "Operations": (4, 0.12, 6400.0),
}

CUSTOMER_FIRST = [
    "Karoo", "Atlas", "Baobab", "Meridian", "Nova", "Harbor", "Savanna", "Vertex",
    "Mosaic", "Delta", "Crest", "Halcyon",
]
CUSTOMER_SECOND = ["Mining", "Retail", "Logistics", "Health"]
# 48 customers, each with a stable size weight (drives invoice amounts).
CUSTOMERS = [f"{a} {b}" for a in CUSTOMER_FIRST for b in CUSTOMER_SECOND]

PAYMENT_METHODS = [("EFT", 0.60), ("Card", 0.25), ("Wire", 0.15)]


def _customer_weight(customer: str) -> float:
    """Stable size multiplier per customer in [0.5, 3.0]."""
    return 0.5 + 2.5 * noise("custsize", customer)


def build_tables() -> "dict[str, pa.Table]":
    """Build the finance-ops dataset as Arrow tables (deterministic)."""
    import pyarrow as pa

    invoices: list[tuple] = []
    payments: list[tuple] = []
    expenses: list[tuple] = []
    headcount: list[tuple] = []

    inv_id = 0
    pay_id = 0
    exp_id = 0

    for idx, first, month_str in iter_months():
        # ── Headcount + expenses per department ──────────────────────────────
        for dept, (start, growth, salary) in DEPARTMENTS.items():
            hc = int(start + growth * idx + noise("hc", dept, month_str) * 1.4)
            headcount.append((month_str, dept, hc))

            # Salaries (mild inflation + jitter).
            sal = round(hc * salary * (1.0 + 0.0025 * idx) * (0.96 + 0.08 * noise("sal", dept, month_str)), 2)
            exp_id += 1
            expenses.append((exp_id, month_str, dept, "Salaries", sal))

            # Office cost scales with heads.
            exp_id += 1
            expenses.append((exp_id, month_str, dept, "Office", round(hc * 260.0, 2)))

            # Department-specific spend.
            if dept == "Engineering":
                exp_id += 1
                expenses.append((exp_id, month_str, dept, "Cloud & Infra",
                                 round((18000 + 850 * idx) * (0.9 + 0.2 * noise("cloud", month_str)), 2)))
                exp_id += 1
                expenses.append((exp_id, month_str, dept, "Software", round(3800 + 90 * idx, 2)))
            elif dept == "Marketing":
                exp_id += 1
                expenses.append((exp_id, month_str, dept, "Advertising",
                                 round(26000 * seasonality(first.month) * (0.85 + 0.3 * noise("ads", month_str)), 2)))
            elif dept == "Sales":
                exp_id += 1
                expenses.append((exp_id, month_str, dept, "Travel",
                                 round(5200 * (0.7 + 0.6 * noise("travel", month_str)), 2)))

        # ── Invoices issued this month (volume grows ~5x over the window) ───
        n_inv = int((14 + 2.3 * idx) * (0.92 + 0.16 * noise("invvol", month_str)))
        for i in range(n_inv):
            inv_id += 1
            customer = CUSTOMERS[int(noise("cust", inv_id) * len(CUSTOMERS)) % len(CUSTOMERS)]
            w = _customer_weight(customer)
            r_amt = noise("amt", inv_id)
            amount = round(w * (9000 + 32000 * r_amt * r_amt), 2)
            day = 1 + int(noise("iday", inv_id) * 27)
            issue_date = date(first.year, first.month, day)
            due_date = issue_date + timedelta(days=30)

            paid_flag = noise("pay", inv_id) < 0.94
            days_to_pay = 10 + int(noise("paydays", inv_id) * 55)
            paid_date = issue_date + timedelta(days=days_to_pay)

            if paid_flag and paid_date <= AS_OF:
                status = "paid"
                invoices.append((inv_id, customer, month_str, issue_date, due_date, amount, status, paid_date))
                pay_id += 1
                pay_month = f"{paid_date.year:04d}-{paid_date.month:02d}"
                method = weighted_pick(PAYMENT_METHODS, "method", inv_id)
                payments.append((pay_id, inv_id, customer, pay_month, paid_date, amount, method))
            else:
                status = "overdue" if due_date < AS_OF else "open"
                invoices.append((inv_id, customer, month_str, issue_date, due_date, amount, status, None))

    def col(rows: list[tuple], names: list[str]) -> dict[str, list]:
        return {n_: [r[i] for r in rows] for i, n_ in enumerate(names)}

    inv_cols = col(invoices, [
        "invoice_id", "customer", "month", "issue_date", "due_date", "amount", "status", "paid_date",
    ])
    pay_cols = col(payments, [
        "payment_id", "invoice_id", "customer", "month", "paid_date", "amount", "method",
    ])
    return {
        "fin_invoices": pa.table({
            **inv_cols,
            "issue_date": pa.array(inv_cols["issue_date"], type=pa.date32()),
            "due_date": pa.array(inv_cols["due_date"], type=pa.date32()),
            "paid_date": pa.array(inv_cols["paid_date"], type=pa.date32()),
        }),
        "fin_payments": pa.table({
            **pay_cols,
            "paid_date": pa.array(pay_cols["paid_date"], type=pa.date32()),
        }),
        "fin_expenses": pa.table(col(expenses, ["expense_id", "month", "department", "category", "amount"])),
        "fin_headcount": pa.table(col(headcount, ["month", "department", "headcount"])),
    }
