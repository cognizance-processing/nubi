---
title: Honest cost-claim scope
---

The **10–50× cost advantage** vs naive warehouse usage is real only at high cache-hit / pre-aggregation rates — e.g. 500 viewers of the *same* dashboard collapsing to one backend hit. For 500 analysts each slicing differently, cache hit rate craters and you are back to warehouse scans. Nubi prices and designs for the *repeated-query* (embedded) shape first, and uses automatic pre-aggregations so the advantage survives diverse workloads.

Competitor data was researched June 2026 from public pricing pages and independent analysts. Fields marked **est.** contain estimates — re-verify before publishing and at least once per quarter.
