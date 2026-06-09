"""``retail_sales`` dataset — FMCG sales star schema (~24 months).

The original Nubi demo dataset, refined: one fact table plus three dims and two
planning tables.  Combinations are deterministically *sampled* (~55% of the
full month×opco×region×channel×supplier×group grid) so the fact table stays
under ~50k rows while every dimension value remains well-represented — and the
sparsity is realistic (not every supplier sells every group everywhere every
month).

    dim_customers ─┐
    dim_products ──┤──< sales (fact) >── dim_regions
                   │
              budget / targets (aggregate planning tables)

Schema
------
``dim_regions``   : region_id PK, region, country
``dim_products``  : product_id PK, product_group, category, supplier, brand, unit_price
``dim_customers`` : customer_id PK, customer, channel
``sales``         : invoice_date, month, year, opco, region_id/product_id/customer_id FKs,
                    denormalised region/channel/supplier/brand/category/product_group/customer,
                    nsv, volume, units
``budget``        : month, region, budget_nsv
``targets``       : month, opco, target_nsv
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from seed_data.generators._common import iter_months, noise, pick, seasonality

if TYPE_CHECKING:
    import pyarrow as pa

# ── Fixed dimension lists ──────────────────────────────────────────────────────
OPCOS = ["Wutow", "Logico", "CASales Botswana", "Rainbow", "Eswatini PnP"]
REGIONS = ["Gauteng", "Western Cape", "KwaZulu-Natal", "Eastern Cape", "Free State"]
REGION_COUNTRY = {
    "Gauteng": "South Africa",
    "Western Cape": "South Africa",
    "KwaZulu-Natal": "South Africa",
    "Eastern Cape": "South Africa",
    "Free State": "South Africa",
}
CHANNELS = ["Modern Trade", "Traditional Trade", "Wholesale", "Mass Discounters"]
SUPPLIERS = ["Unilever", "Kimberly-Clark", "Lipton", "Nestlé", "Tiger Brands", "Rainbow"]
CUSTOMERS = ["Pick n Pay", "Shoprite", "Spar", "Game", "Makro", "Woolworths"]

# product_group → (category, base unit price, base monthly units, units→volume).
# "Frozen" is a deliberately DECLINING group so PoP / CY-vs-LY shows red as well
# as green — realistic variety for conditional-formatting demos.
PRODUCT_GROUPS = {
    "Beverages":     ("Beverages",     2.10, 4200, 0.50),
    "Tea & Coffee":  ("Beverages",     3.40, 2600, 0.20),
    "Home Care":     ("Home Care",     2.75, 3600, 0.90),
    "Personal Care": ("Personal Care", 4.10, 2900, 0.30),
    "Foods":         ("Foods",         1.85, 5200, 0.70),
    "Frozen":        ("Frozen",        3.05, 2400, 0.80),  # declining
}
_DECLINING = {"Frozen"}

# Brands per supplier (row picks one deterministically).
SUPPLIER_BRANDS = {
    "Unilever":       ["Sunlight", "Omo", "Knorr"],
    "Kimberly-Clark": ["Huggies", "Kleenex"],
    "Lipton":         ["Lipton"],
    "Nestlé":         ["Nescafé", "Maggi"],
    "Tiger Brands":   ["Jungle", "Albany"],
    "Rainbow":        ["Rainbow"],
}

# Per-dimension demand multipliers — make breakdowns look genuinely different.
_OPCO_WEIGHT = {"Wutow": 1.35, "Logico": 1.15, "CASales Botswana": 0.85, "Rainbow": 1.00, "Eswatini PnP": 0.65}
_REGION_WEIGHT = {"Gauteng": 1.40, "Western Cape": 1.10, "KwaZulu-Natal": 1.15, "Eastern Cape": 0.80, "Free State": 0.70}
_CHANNEL_WEIGHT = {"Modern Trade": 1.30, "Traditional Trade": 1.00, "Wholesale": 1.45, "Mass Discounters": 1.20}
_SUPPLIER_WEIGHT = {"Unilever": 1.50, "Kimberly-Clark": 1.10, "Lipton": 0.70, "Nestlé": 1.25, "Tiger Brands": 1.05, "Rainbow": 0.80}

# Deterministic sampling rate over the full combination grid (86,400 combos).
# 0.55 keeps the fact table at ~47.5k rows — under the ~50k budget — while
# every (month, region, channel, supplier, group) value stays well covered.
_KEEP_RATE = 0.55

TABLES = ("dim_regions", "dim_products", "dim_customers", "sales", "budget", "targets")


# ── Dimension builders (deterministic surrogate keys, 1-based) ─────────────────

def _build_region_dim() -> tuple[list[tuple], dict[str, int]]:
    rows: list[tuple] = []
    lookup: dict[str, int] = {}
    for i, region in enumerate(REGIONS, start=1):
        lookup[region] = i
        rows.append((i, region, REGION_COUNTRY[region]))
    return rows, lookup


def _build_product_dim() -> tuple[list[tuple], dict[tuple[str, str, str], int]]:
    """One product row per (product_group, supplier, brand) combo (clean star join)."""
    rows: list[tuple] = []
    lookup: dict[tuple[str, str, str], int] = {}
    pid = 0
    for pg, (category, price, _base, _vol) in PRODUCT_GROUPS.items():
        for supplier in SUPPLIERS:
            for brand in SUPPLIER_BRANDS[supplier]:
                pid += 1
                lookup[(pg, supplier, brand)] = pid
                rows.append((pid, pg, category, supplier, brand, price))
    return rows, lookup


def _build_customer_dim() -> tuple[list[tuple], dict[tuple[str, str], int]]:
    rows: list[tuple] = []
    lookup: dict[tuple[str, str], int] = {}
    cid = 0
    for customer in CUSTOMERS:
        for channel in CHANNELS:
            cid += 1
            lookup[(customer, channel)] = cid
            rows.append((cid, customer, channel))
    return rows, lookup


def _build_sales_rows(
    region_ids: dict[str, int],
    product_ids: dict[tuple[str, str, str], int],
    customer_ids: dict[tuple[str, str], int],
) -> list[tuple]:
    """Generate the ``sales`` fact table (with FK surrogate keys)."""
    rows: list[tuple] = []
    for idx, d, month_str in iter_months():
        season = seasonality(d.month)
        for opco in OPCOS:
            ow = _OPCO_WEIGHT[opco]
            for region in REGIONS:
                rw = _REGION_WEIGHT[region]
                region_id = region_ids[region]
                for channel in CHANNELS:
                    cw = _CHANNEL_WEIGHT[channel]
                    for supplier in SUPPLIERS:
                        sw = _SUPPLIER_WEIGHT[supplier]
                        for pg, (category, price, base_units, vol_f) in PRODUCT_GROUPS.items():
                            # Deterministic sparsity: skip ~45% of combos.
                            if noise("keep", month_str, opco, region, channel, supplier, pg) >= _KEEP_RATE:
                                continue

                            # Growth trend: most groups grow ~+18% over the window;
                            # the declining group shrinks ~-15% so CY < LY for it.
                            if pg in _DECLINING:
                                trend = 1.0 - 0.0065 * idx
                            else:
                                trend = 1.0 + 0.0075 * idx
                            wobble = 0.85 + 0.30 * noise(month_str, opco, region, channel, supplier, pg)
                            units = base_units * ow * rw * cw * sw * trend * season * wobble
                            units = max(1, int(round(units / (len(SUPPLIERS) * len(OPCOS)))))

                            eff_price = price * (1.0 + 0.0030 * idx)  # mild inflation
                            nsv = round(units * eff_price, 2)
                            volume = round(units * vol_f, 1)

                            brand = pick(SUPPLIER_BRANDS[supplier], supplier, pg, region)
                            customer = pick(CUSTOMERS, month_str, opco, region, channel, pg)

                            rows.append((
                                d, month_str, d.year, opco,
                                region_id,
                                product_ids[(pg, supplier, brand)],
                                customer_ids[(customer, channel)],
                                region, channel, supplier, brand, category, pg, customer,
                                nsv, volume, units,
                            ))
    return rows


def _build_budget_rows(sales_rows: list[tuple]) -> list[tuple]:
    """Per (month, region) budget ≈ 95–108% of realised NSV (over/under mix)."""
    actual: dict[tuple[str, str], float] = {}
    for r in sales_rows:
        month_str, region, nsv = r[1], r[7], r[14]
        actual[(month_str, region)] = actual.get((month_str, region), 0.0) + nsv
    return [
        (month_str, region, round(total * (0.95 + 0.13 * noise("budget", month_str, region)), 2))
        for (month_str, region), total in actual.items()
    ]


def _build_target_rows(sales_rows: list[tuple]) -> list[tuple]:
    """Per (month, opco) target ≈ 92–110% of realised NSV."""
    actual: dict[tuple[str, str], float] = {}
    for r in sales_rows:
        month_str, opco, nsv = r[1], r[3], r[14]
        actual[(month_str, opco)] = actual.get((month_str, opco), 0.0) + nsv
    return [
        (month_str, opco, round(total * (0.92 + 0.18 * noise("target", month_str, opco)), 2))
        for (month_str, opco), total in actual.items()
    ]


# Column order for the ``sales`` fact table (matches ``_build_sales_rows``).
SALES_COLS = [
    "invoice_date", "month", "year", "opco",
    "region_id", "product_id", "customer_id",
    "region", "channel", "supplier", "brand", "category", "product_group", "customer",
    "nsv", "volume", "units",
]


def _columnise(rows: list[tuple], cols: list[str]) -> dict[str, list]:
    return {name: [row[i] for row in rows] for i, name in enumerate(cols)}


def build_tables() -> "dict[str, pa.Table]":
    """Build the full retail star schema as Arrow tables (deterministic)."""
    import pyarrow as pa

    region_rows, region_ids = _build_region_dim()
    product_rows, product_ids = _build_product_dim()
    customer_rows, customer_ids = _build_customer_dim()

    sales_rows = _build_sales_rows(region_ids, product_ids, customer_ids)
    budget_rows = _build_budget_rows(sales_rows)
    target_rows = _build_target_rows(sales_rows)

    sales_cols = _columnise(sales_rows, SALES_COLS)
    return {
        "dim_regions": pa.table(_columnise(region_rows, ["region_id", "region", "country"])),
        "dim_products": pa.table(_columnise(
            product_rows, ["product_id", "product_group", "category", "supplier", "brand", "unit_price"]
        )),
        "dim_customers": pa.table(_columnise(customer_rows, ["customer_id", "customer", "channel"])),
        "sales": pa.table({
            **sales_cols,
            "invoice_date": pa.array(sales_cols["invoice_date"], type=pa.date32()),
        }),
        "budget": pa.table(_columnise(budget_rows, ["month", "region", "budget_nsv"])),
        "targets": pa.table(_columnise(target_rows, ["month", "opco", "target_nsv"])),
    }
