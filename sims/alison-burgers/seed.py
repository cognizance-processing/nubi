#!/usr/bin/env python3
"""Generate and load the Alison Burgers point-of-sale dataset.

Everything here is synthetic. The data is shaped so that a dashboard built on
top of it has something to *say* — each of the storylines below is a finding a
real analyst would go looking for, planted deliberately so that filtering the
board reveals it rather than flattening it into noise.

Storylines
----------
1. Eastern Cape trades down from Dec 2025 onward (a competitor opened) and is
   also the slowest region on service time — visible only once you filter.
2. Sandton Drive-Thru lost its POS for twelve days in April 2026. A hole, not a
   dip; the kind of thing an average-based KPI quietly swallows.
3. The DOUBLETROUBLE promo (Jan-Feb 2026) lifts order volume about a quarter,
   takes discount from 0.7% to 7.0% of gross, and knocks gross margin from
   61.9% to 58.7%. It worked and it didn't.
4. Delivery-app share grows 8% -> 25% over the period. It carries a 22%
   commission and waits about 300s against 108s everywhere else, so revenue
   grows while chain gross margin drifts 63.4% -> 60.5%.
5. The Beetroot Boss (vegan, launched Oct 2025) is 19.7% of Western Cape
   burger units and 2.8% in the Free State.

Usage:  seed.py --host 127.0.0.1 --port 3307 --password <root pw>
"""

from __future__ import annotations

import argparse
import datetime as dt
import random
import sys
import time
from pathlib import Path

import pymysql

HERE = Path(__file__).resolve().parent

START_DATE = dt.date(2025, 3, 1)
END_DATE = dt.date(2026, 8, 25)
RNG_SEED = 20260826

# Trade volume scale. The per-store baselines below are chain-realistic; this
# trims the row count so that even the widest query — grouping every line item
# by menu item, with no date bound — stays inside a second on a laptop.
SCALE = 0.28

# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------

# store_id, code, name, city, region, format, opened, seats, base orders/day,
# service quality (1.0 = chain average; higher is slower), price index
# (menu prices vary by site — a Waterfront burger is not a Soweto burger)
STORES = [
    (1,  "GP-SAN", "Sandton Drive-Thru",    "Sandton",          "Gauteng",       "Drive-thru", "2019-04-12", 24,  105, 0.92, 1.10),
    (2,  "GP-ROS", "Rosebank Mall",         "Johannesburg",     "Gauteng",       "Mall",       "2018-09-01", 62,   88, 1.00, 1.06),
    (3,  "GP-MEN", "Menlyn Park",           "Pretoria",         "Gauteng",       "Mall",       "2020-02-20", 58,   82, 1.04, 1.00),
    (4,  "GP-MAP", "Maponya Mall",          "Soweto",           "Gauteng",       "Mall",       "2021-06-15", 48,   76, 0.97, 0.90),
    (5,  "GP-MID", "Grand Central",         "Midrand",          "Gauteng",       "Standalone", "2022-11-03", 40,   61, 1.01, 0.98),
    (6,  "WC-VAW", "V&A Waterfront",        "Cape Town",        "Western Cape",  "Mall",       "2017-12-08", 74,   96, 0.95, 1.14),
    (7,  "WC-CLA", "Claremont Main",        "Cape Town",        "Western Cape",  "Standalone", "2019-07-19", 44,   70, 0.99, 1.04),
    (8,  "WC-STB", "Stellenbosch Eikestad", "Stellenbosch",     "Western Cape",  "Kiosk",      "2023-03-01", 12,   44, 1.02, 0.88),
    (9,  "KZ-GAT", "Gateway Umhlanga",      "Umhlanga",         "KwaZulu-Natal", "Mall",       "2018-05-25", 66,   90, 1.03, 1.03),
    (10, "KZ-UMH", "Umhlanga Drive-Thru",   "Umhlanga",         "KwaZulu-Natal", "Drive-thru", "2021-10-11", 20,   72, 0.94, 1.00),
    (11, "KZ-PMB", "Liberty Midlands",      "Pietermaritzburg", "KwaZulu-Natal", "Mall",       "2022-04-08", 46,   55, 1.06, 0.96),
    (12, "EC-BAY", "Baywest Mall",          "Gqeberha",         "Eastern Cape",  "Mall",       "2020-08-14", 52,   64, 1.18, 0.94),
    (13, "EC-VIN", "Vincent Park",          "East London",      "Eastern Cape",  "Standalone", "2021-02-26", 36,   48, 1.22, 0.92),
    (14, "FS-LOC", "Loch Logan",            "Bloemfontein",     "Free State",    "Mall",       "2019-11-29", 50,   58, 1.05, 0.95),
]

# item_id, name, category, subcategory, price, cost, vegan, limited, launched, popularity
MENU = [
    (101, "Classic Alison",      "Burgers",  "Beef",      74.00, 26.90, 0, 0, None,         26),
    (102, "Double Trouble",      "Burgers",  "Beef",     112.00, 44.50, 0, 0, None,         19),
    (103, "Bacon Baron",         "Burgers",  "Beef",      98.00, 38.20, 0, 0, None,         14),
    (104, "Smoky BBQ Stack",     "Burgers",  "Beef",     104.00, 41.10, 0, 0, None,          9),
    (105, "Chicken Royale",      "Burgers",  "Chicken",   86.00, 30.40, 0, 0, None,         15),
    (106, "Mushroom Swiss",      "Burgers",  "Beef",      92.00, 34.60, 0, 0, None,          7),
    (107, "The Beetroot Boss",   "Burgers",  "Plant",     89.00, 27.30, 1, 1, "2025-10-01",  8),
    (108, "Kiddies Alison",      "Burgers",  "Kids",      55.00, 19.80, 0, 0, None,          6),
    (201, "Skin-on Fries",       "Sides",    "Fries",     32.00,  8.10, 1, 0, None,         38),
    (202, "Loaded Fries",        "Sides",    "Fries",     58.00, 19.40, 0, 0, None,         16),
    (203, "Onion Rings",         "Sides",    "Fried",     41.00, 12.60, 1, 0, None,         11),
    (204, "Coleslaw",            "Sides",    "Cold",      25.00,  6.20, 1, 0, None,          5),
    (205, "Side Salad",          "Sides",    "Cold",      36.00,  9.90, 1, 0, None,          6),
    (301, "Cola 330ml",          "Drinks",   "Soft",      22.00,  5.40, 1, 0, None,         27),
    (302, "Cola 500ml",          "Drinks",   "Soft",      29.00,  7.10, 1, 0, None,         18),
    (303, "Sparkling Water",     "Drinks",   "Water",     24.00,  5.80, 1, 0, None,          8),
    (304, "Craft Root Beer",     "Drinks",   "Soft",      38.00, 11.20, 1, 0, None,          7),
    (305, "Vanilla Milkshake",   "Drinks",   "Shakes",    48.00, 16.30, 0, 0, None,         13),
    (306, "Chocolate Milkshake", "Drinks",   "Shakes",    48.00, 16.80, 0, 0, None,         14),
    (307, "Iced Coffee",         "Drinks",   "Coffee",    42.00, 12.40, 0, 0, None,          9),
    (401, "Soft Serve",          "Desserts", "Frozen",    28.00,  7.30, 0, 0, None,         14),
    (402, "Choc Brownie",        "Desserts", "Baked",     45.00, 14.10, 0, 0, None,          9),
    (403, "Apple Pie",           "Desserts", "Baked",     38.00, 11.70, 0, 0, None,          8),
]

DAYPARTS = [
    ("Breakfast", 7, 10, 0.08),
    ("Lunch",    11, 14, 0.34),
    ("Afternoon", 14, 17, 0.14),
    ("Dinner",   17, 21, 0.36),
    ("Late night", 21, 23, 0.08),
]

PAYMENT_TYPES = [("Card", 0.58), ("Cash", 0.17), ("Mobile wallet", 0.19), ("Voucher", 0.06)]

FIRST_NAMES = ["Thandi", "Sipho", "Lerato", "Johan", "Aisha", "Nomsa", "Pieter", "Zanele",
               "Karabo", "Fatima", "Andile", "Chantal", "Musa", "Yolanda", "Bongani",
               "Riaan", "Naledi", "Devan", "Kirsten", "Tebogo"]
LAST_NAMES = ["Mokoena", "Naidoo", "van Wyk", "Dlamini", "Botha", "Khumalo", "Petersen",
              "Nkosi", "du Plessis", "Mahlangu", "Adams", "Sithole", "Fourie", "Mabaso"]
ROLES = [("Crew", 0.58, 34.0), ("Grill", 0.16, 41.0), ("Shift lead", 0.16, 52.0), ("Manager", 0.10, 78.0)]

# --- storyline constants -------------------------------------------------
PROMO_CODE = "DOUBLETROUBLE"
PROMO_START, PROMO_END = dt.date(2026, 1, 5), dt.date(2026, 2, 28)
OUTAGE_STORE, OUTAGE_START, OUTAGE_END = 1, dt.date(2026, 4, 6), dt.date(2026, 4, 17)
EC_DECLINE_START = dt.date(2025, 12, 1)
DELIVERY_COMMISSION = 0.22


# --------------------------------------------------------------------------
# Shape functions — one per storyline, so the data is explainable
# --------------------------------------------------------------------------

def _progress(day: dt.date) -> float:
    """0.0 at the first day of data, 1.0 at the last."""
    return (day - START_DATE).days / max(1, (END_DATE - START_DATE).days)


def volume_multiplier(day: dt.date, store: tuple, rng: random.Random) -> float:
    m = SCALE
    m *= 1.0 + 0.14 * _progress(day)                       # steady chain growth
    dow = day.weekday()
    m *= {0: 0.86, 1: 0.88, 2: 0.93, 3: 1.02, 4: 1.24, 5: 1.38, 6: 1.15}[dow]
    if day.month == 12:
        m *= 1.22                                          # festive season
    elif day.month in (1, 6):
        m *= 0.91                                          # post-festive and mid-winter
    if PROMO_START <= day <= PROMO_END:
        m *= 1.38                                          # storyline 3
    if store[4] == "Eastern Cape" and day >= EC_DECLINE_START:  # storyline 1
        elapsed = (day - EC_DECLINE_START).days / max(1, (END_DATE - EC_DECLINE_START).days)
        m *= 1.0 - 0.32 * elapsed
    return m * rng.uniform(0.88, 1.12)


def delivery_share(day: dt.date) -> float:
    """Storyline 4: delivery apps grow from 8% to 26% of orders."""
    return 0.08 + 0.18 * _progress(day)


def channel_weights(day: dt.date, store_format: str) -> list[tuple[str, float]]:
    delivery = delivery_share(day)
    if store_format == "Drive-thru":
        base = {"Drive-thru": 0.62, "In-store": 0.18, "Own app": 0.09}
    elif store_format == "Kiosk":
        base = {"In-store": 0.74, "Own app": 0.15}
    else:
        base = {"In-store": 0.63, "Own app": 0.16, "Drive-thru": 0.05}
    remaining = 1.0 - delivery
    total = sum(base.values())
    weights = [(name, w / total * remaining) for name, w in base.items()]
    weights.append(("Delivery app", delivery))
    return weights


def item_weight(item: tuple, day: dt.date, region: str) -> float:
    """Storyline 5: the vegan LTO lands very differently by region."""
    item_id, _, _, _, _, _, _, _, launched, popularity = item
    if launched and day < dt.date.fromisoformat(launched):
        return 0.0
    w = float(popularity)
    if item_id == 107:
        w *= {"Western Cape": 3.1, "Gauteng": 1.2, "KwaZulu-Natal": 0.9,
              "Eastern Cape": 0.6, "Free State": 0.35}.get(region, 1.0)
    if item_id == 102 and PROMO_START <= day <= PROMO_END:
        w *= 2.4                                           # the promo pulls the mix
    return w


def service_seconds(day: dt.date, daypart: str, channel: str, store: tuple,
                    item_count: int, rng: random.Random) -> tuple[int, int]:
    quality = store[9]
    prep = 165 * quality
    prep += 22 * max(0, item_count - 2)
    if daypart in ("Lunch", "Dinner"):
        prep *= 1.26
    if PROMO_START <= day <= PROMO_END:
        prep *= 1.11                                       # the promo strains the kitchen
    wait = prep * 0.42
    if channel == "Delivery app":
        wait += 190 * (0.7 + delivery_share(day))          # courier handover
    elif channel == "Drive-thru":
        wait *= 0.72
    return (int(prep * rng.uniform(0.82, 1.24)), int(wait * rng.uniform(0.8, 1.3)))


def review_score(wait_seconds: int, store: tuple, rng: random.Random) -> int:
    base = 4.62 - (wait_seconds - 90) / 260.0
    base -= (store[9] - 1.0) * 1.4
    score = round(rng.gauss(base, 0.75))
    return max(1, min(5, score))


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

def weighted_pick(rng: random.Random, pairs: list[tuple]) -> object:
    total = sum(w for _, w in pairs)
    r = rng.random() * total
    upto = 0.0
    for value, w in pairs:
        upto += w
        if r <= upto:
            return value
    return pairs[-1][0]


def generate(rng: random.Random) -> dict[str, list]:
    stores = [(s[0], s[1], s[2], s[3], s[4], s[5], s[6], s[7]) for s in STORES]
    menu = [(m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8]) for m in MENU]

    staff, shifts = [], []
    staff_id = shift_id = 0
    by_store: dict[int, list[int]] = {}
    for store in STORES:
        by_store[store[0]] = []
        for _ in range(rng.randint(14, 21)):
            staff_id += 1
            role, _, rate = weighted_pick(rng, [(r, r[1]) for r in ROLES])
            staff.append((staff_id, store[0],
                          f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
                          role, str(START_DATE - dt.timedelta(days=rng.randint(30, 1400))),
                          round(rate * rng.uniform(0.94, 1.16), 2)))
            by_store[store[0]].append(staff_id)

    staff_rate = {s[0]: float(s[5]) for s in staff}

    orders, order_items, reviews = [], [], []
    order_id = item_row_id = review_id = 0
    day = START_DATE
    menu_by_cat: dict[str, list] = {}
    for m in MENU:
        menu_by_cat.setdefault(m[2], []).append(m)

    while day <= END_DATE:
        day_str = str(day)
        for store in STORES:
            store_id, _, _, _, region, store_format, _, _, base_orders, _, price_index = store
            if store_id == OUTAGE_STORE and OUTAGE_START <= day <= OUTAGE_END:
                continue                                   # storyline 2: no POS, no rows

            # --- rostering -------------------------------------------------
            for daypart, _, _, _ in DAYPARTS[:4]:
                for member in rng.sample(by_store[store_id], rng.randint(1, 3)):
                    shift_id += 1
                    hours = round(rng.uniform(3.0, 4.5), 2)
                    shifts.append((shift_id, store_id, member, day_str, daypart, hours,
                                   round(hours * staff_rate[member], 2)))

            # --- trade -----------------------------------------------------
            n_orders = max(0, int(base_orders * volume_multiplier(day, store, rng)))
            ch_weights = channel_weights(day, store_format)
            for _ in range(n_orders):
                order_id += 1
                daypart, h_from, h_to, _ = weighted_pick(
                    rng, [(d, d[3]) for d in DAYPARTS])
                ordered_at = dt.datetime.combine(
                    day, dt.time(rng.randrange(h_from, h_to), rng.randrange(60), rng.randrange(60)))
                channel = weighted_pick(rng, ch_weights)
                payment = "Mobile wallet" if channel == "Delivery app" else \
                    weighted_pick(rng, PAYMENT_TYPES)

                lines = []
                for category, count in (("Burgers", rng.choices([1, 1, 1, 2, 3], k=1)[0]),
                                        ("Sides", rng.choices([0, 1, 1, 2], k=1)[0]),
                                        ("Drinks", rng.choices([0, 1, 1, 2], k=1)[0]),
                                        ("Desserts", rng.choices([0, 0, 0, 1], k=1)[0])):
                    pool = [(m, item_weight(m, day, region)) for m in menu_by_cat[category]]
                    pool = [p for p in pool if p[1] > 0]
                    for _ in range(count):
                        lines.append(weighted_pick(rng, pool))
                if not lines:
                    lines.append(menu_by_cat["Burgers"][0])

                gross = discount = cost = 0.0
                item_count = 0
                promo_code = None
                for item in lines:
                    item_row_id += 1
                    qty = 1 if rng.random() > 0.12 else 2
                    price = float(item[4]) * price_index
                    unit_cost = float(item[5])
                    if channel == "Delivery app":
                        price = round(price * 1.12, 2)     # delivery menu markup
                    line_discount = 0.0
                    if item[0] == 102 and PROMO_START <= day <= PROMO_END:
                        line_discount = round(price * qty * 0.25, 2)
                        promo_code = PROMO_CODE
                    elif rng.random() < 0.045:
                        line_discount = round(price * qty * rng.choice([0.10, 0.15, 0.20]), 2)
                        promo_code = promo_code or "STAFF10"
                    order_items.append((item_row_id, order_id, store_id, day_str,
                                        channel, item[0], qty, round(price, 2),
                                        round(unit_cost, 2), line_discount))
                    gross += price * qty
                    discount += line_discount
                    cost += unit_cost * qty
                    item_count += qty

                net = gross - discount
                commission = round(net * DELIVERY_COMMISSION, 2) if channel == "Delivery app" else 0.0
                prep_s, wait_s = service_seconds(day, daypart, channel, store, item_count, rng)
                orders.append((order_id, store_id, ordered_at.strftime("%Y-%m-%d %H:%M:%S"),
                               day_str, daypart, channel, payment, promo_code, item_count,
                               round(gross, 2), round(discount, 2), round(net, 2),
                               round(cost, 2), commission, prep_s, wait_s))

                if rng.random() < 0.11:
                    review_id += 1
                    reviews.append((review_id, order_id, store_id,
                                    str(day + dt.timedelta(days=rng.randint(0, 2))),
                                    review_score(wait_s, store, rng),
                                    weighted_pick(rng, [("Own app", 0.42), ("Google", 0.28),
                                                        ("Delivery app", 0.19), ("In-store tablet", 0.11)])))
        day += dt.timedelta(days=1)

    return {"stores": stores, "menu_items": menu, "staff": staff, "shifts": shifts,
            "orders": orders, "order_items": order_items, "reviews": reviews}


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def run_script(cur, path: Path) -> None:
    body = "\n".join(line for line in path.read_text().splitlines()
                     if not line.lstrip().startswith("--"))
    for statement in (s.strip() for s in body.split(";")):
        if statement:
            cur.execute(statement)


def load(conn, data: dict[str, list]) -> None:
    cur = conn.cursor()
    cur.execute("SET unique_checks = 0")
    cur.execute("SET foreign_key_checks = 0")
    for table, rows in data.items():
        if not rows:
            continue
        placeholders = ",".join(["%s"] * len(rows[0]))
        sql = f"INSERT INTO {table} VALUES ({placeholders})"
        started = time.time()
        for i in range(0, len(rows), 5000):
            cur.executemany(sql, rows[i:i + 5000])
        conn.commit()
        print(f"    {table:<12} {len(rows):>9,} rows  ({time.time() - started:.1f}s)", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3307)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", required=True)
    args = ap.parse_args()

    rng = random.Random(RNG_SEED)
    print("==> generating…", flush=True)
    started = time.time()
    data = generate(rng)
    print(f"    generated {sum(len(v) for v in data.values()):,} rows "
          f"in {time.time() - started:.1f}s", flush=True)

    conn = pymysql.connect(host=args.host, port=args.port, user=args.user,
                           password=args.password, autocommit=False,
                           local_infile=True, charset="utf8mb4")
    try:
        cur = conn.cursor()
        print("==> creating schema…", flush=True)
        run_script(cur, HERE / "schema.sql")
        conn.commit()
        print("==> loading…", flush=True)
        load(conn, data)
        print("==> building indexes…", flush=True)
        started = time.time()
        run_script(cur, HERE / "indexes.sql")
        conn.commit()
        print(f"    done in {time.time() - started:.1f}s", flush=True)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
