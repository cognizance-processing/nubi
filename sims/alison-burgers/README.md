# Alison Burgers

A point-of-sale simulator for demos and local development: a fourteen-store burger
chain with eighteen months of trade. Entirely synthetic — no real people, stores or
transactions.

```bash
./up.sh              # start (seeds on first run), then prints the connection details
./up.sh --reseed     # rebuild the dataset from scratch
./down.sh            # stop, keeping the data
./down.sh --destroy  # stop and delete the data
```

`up.sh` prints the host, port, database, username and password to paste into a Nubi
MySQL connector. The login it creates is read-only, which is what a customer would
actually hand over.

## What is in it

`stores` · `menu_items` · `staff` · `shifts` · `orders` · `order_items` · `reviews`.

`order_items` carries `store_id`, `order_date` and `channel` alongside the order key.
A line-item fact table is normally built that way, and it keeps menu analysis off the
orders table — without it, grouping every line by menu item costs half a million
random primary-key lookups just to reach the date.

## Why the numbers move

The data is shaped so a dashboard built on it has something to say. Each storyline is
a named function in `seed.py`, so you can explain any movement on screen:

1. **Eastern Cape trades down** from December 2025 — share of chain net sales slides
   11.3% to 8.0% — and it is also the slowest region on service time.
2. **Sandton Drive-Thru lost its point of sale** for twelve days in April 2026. A hole
   in the data, not a dip; the kind of thing an average quietly swallows.
3. **The DOUBLETROUBLE promo**, January to February 2026: volume up about a quarter,
   discount from 0.7% to 7.0% of gross, gross margin from 61.9% to 58.7%.
4. **Delivery-app share grows 8% to 25%**, carries 22% commission, and waits around
   300 seconds against 108 everywhere else — so revenue grows while margin drifts.
5. **The Beetroot Boss** (vegan, launched October 2025) is 19.7% of Western Cape
   burger units and 2.8% in the Free State.

Generation is deterministic: the same seed produces the same 175,204 orders and
675,203 line items every time. `SCALE` in `seed.py` trims the volume so that even the
widest query — every line item grouped by menu item, with no date bound — stays inside
a second on a laptop.
