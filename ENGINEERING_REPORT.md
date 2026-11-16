# MerchantOps Engineering Report

## Findings and priorities

I investigated the product catalogue and product editor paths, plus the inventory model that already encodes reserved-stock constraints. Here are my findings and the priorities i made:

**Highest impact (catalogue UX):** Opening the products page loaded the list once, then fetched each product detail again one-by-one (25 extra requests per page) before showing the table. Those second requests repeated data the list already had. The list payload already included every field the table renders (`name`, `sku`, `status`, `category`, `image_url`). The detail responses differed only in ways the catalogue does not use (`description`, category as an id). Waiting on them made the catalogue feel slow.

**High impact (editor backend):** Opening a product’s editor asked the database for prices and inventory separately for every variant. On a complex product like ASSESS-000001 (50 variants), that meant about 100 extra database trips just for one page load. This is of highest impact on the backend.

**Medium impact (list ORM):** The product list shows each product’s category name, but the API loaded categories one query at a time per row instead of with the products. On a 25-item page, that meant about 25 extra database trips.

**Feature gap (Task 2):** Inventory was read-only. There was no adjustment API, no audit for stock changes, and no UI to correct quantity after a count.

I prioritized (1) removing wasted catalogue HTTP work, (2) eliminating editor N+1 queries, (3) list `select_related`, then (4) a concurrent-safe inventory adjustment workflow with audit and merchant isolation.

## Performance evidence

### Method

Comparable conditions: seeded catalogue (5000 products + complex products), signed-in `demo_operator`, Docker Compose stack.

| Path | How measured |
|------|----------------|
| Catalogue page 1 | Chrome DevTools Network (XHR/fetch to `/api/products`) |
| Product list API | Django query capture / regression test that query count stays flat as page fills |
| Product editor (`ASSESS-000001`) | Django query capture on `GET …/editor/`; regression test that query count stays flat as variant count grows |

### Results

| Scenario                          | Before                                                                                                                                                                   | After                                                                                                                                                                                                                                                            |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Catalogue page load (25 rows)     | 1 list + 25 sequential detail requests (**26** HTTP calls), and took 24.65s [static/Screenshot 2026-11-15 at 13.46.44.png](static/Screenshot 2026-11-15 at 13.46.44.png) | **1** list request, and took 1.41s [static/Screenshot 2026-11-15 at 13.46.25.png](static/Screenshot 2026-11-15 at 13.46.25.png)                                                                                                                                  |
| List API categories (25 products) | ~1 query per row for `category.name` (N+1) [static/Screenshot 2026-11-15 at 16.25.47.png](static/Screenshot 2026-11-15 at 16.25.47.png)                                  | Category is joined via `select_related`; query count **does not grow** with page size (`test_list_query_count_does_not_grow_with_page_size`) with 0.33s difference. [static/Screenshot 2026-11-15 at 16.26.21.png](static/Screenshot 2026-11-15 at 16.26.21.png) |
| Editor for many variants          | ~2 SQL queries **per variant** for prices + inventory                                                                                                                    | **2** bulk queries total for prices + inventory; overall editor query count **does not grow** with variant count (`test_editor_query_count_does_not_grow_with_variants`)                                                                                         |

The catalogue change is the largest operator-visible win. The editor change matters most on complex SKUs.

### Reproduce

```bash
./setup.sh
# Catalogue: open http://127.0.0.1:5173/ → Network → confirm a single products list call per page
# Editor: open ASSESS-000001 → Pricing/Inventory tabs populate; backend query count stays flat vs a normal 2-variant product

docker compose exec backend python manage.py test \
  catalog.tests.test_catalogue_api.ProductListAPITests.test_list_query_count_does_not_grow_with_page_size \
  catalog.tests.test_editor_api.ProductEditorAPITests.test_editor_query_count_does_not_grow_with_variants \
  --keepdb
```

## Implementation

### Task 1 — Performance

1. **Catalogue:** I removed the detail-hydration loop in `frontend/src/views/CatalogueView.vue`. Render `catalogue.results` directly.
2. **Editor:** I replaced per-variant `PriceRule` / `Inventory` queries in `ProductEditorView` with bulk `filter(variant_id__in=…)` loads grouped in Python. Same response shape but far fewer round-trips. Chose explicit bulk fetch over `prefetch_related` because the view already builds a hand-shaped dicts (including `None` inventory).
3. **List:** I added `select_related("category")` on `ProductListView.get_queryset()`.

### Task 2 — Inventory adjustments

**API:** `POST /api/products/<product_id>/variants/<variant_id>/inventory/adjust/?merchant_id=`

**Body:** `{ "delta": <nonzero int>, "reason": "<required string>" }`

**Core logic** (`catalog/services/inventory.py`):

- Find that variant’s inventory only when it matches the given merchant and product; if not found, return 404
- Inside `transaction.atomic()`, lock the row with `select_for_update()` so another request cannot change the same stock at the same time
- Compute `new quantity = current quantity + delta`. If that would go under reserved (`quantity + delta < reserved`), reject the request
- Update quantity and create an `AuditLog` (`action="inventory.adjusted"`) together. If either step fails, neither is kept
- Put the useful facts in audit `details`: which variant (`variant_id`, `variant_sku`), `reason`, `delta`, and stock before/after (`quantity_before`, `quantity_after`)

**Why i choose this design:**

- **Send a change amount, not a final stock number**: the API takes `delta` (for example `-3` meaning “remove 3”). The server adds that to the current quantity. If two people adjust at the same time, both changes can apply instead of one overwriting the other, and the audit clearly records how much moved
- **Reuse** `AuditLog`: stock changes use the same activity history as product edits, so operators see them on the existing activity page without a new database table
- **Row lock with** `select_for_update()`: while one adjustment is saving, others wait on that inventory row. This is the same pattern used when updating a product, and it stops lost updates
- **Same merchant check as other APIs**: the request must include `merchant_id`, and the user must be a member of that merchant. Otherwise the API returns 404

**Frontend:** Inventory tab in `EditorView.vue` (delta, reason, Apply, success/error banner). Activity view renders `inventory.adjusted`. `api.js` supports POST and clearer field error labels.

## Correctness

Automated coverage (run with MySQL via Compose):

```bash
docker compose exec backend python manage.py test catalog --keepdb
```

Inventory suite (`catalog/tests/test_inventory_adjust_api.py`) verifies:

| Concern | Coverage |
|---------|----------|
| Increase / decrease | Happy-path tests + quantity assertions |
| Validation | Zero delta, blank/missing reason, non-integer delta |
| Reserved floor | Reject below reserved; no audit written |
| Atomicity | Forced audit `IntegrityError` rolls back quantity |
| Authorization | Other merchant, non-member, unauthorized operator, unauthenticated, product/variant mismatch, missing merchant_id, variant without inventory row |
| Concurrency | Two parallel adjustments both apply; final qty = start + 3 + 5; two audit rows |
| Activity feed | Adjust appears on `GET …/activity/` with expected details |

Performance regressions assert list/editor SQL count should stays constant as data volume grows.

Manual checks: adjust on inventory tab, reserved-stock error banner, activity history entry.

## Trade-offs

- **Audit model:** Stock changes are stored as JSON on the existing product `AuditLog`, which is enough for the activity page. A separate inventory-adjustment table would make it easier later to report all changes for one variant or run analytics.
- **Editor still returns all variants in one response.** We fixed the per-variant database queries, but a product with hundreds of variants would still download a large payload. Pagination or loading pricing/inventory only when that tab opens would help.
- **Editor still makes a second request for** `image_url`**.** The editor API does not return the image path today, so the UI also calls the product detail endpoint. Putting `image_url` on the editor response would remove that extra call.
- **No frontend unit tests** in this repo. The UI was checked by hand; correctness of stock, auth, and audit is covered by backend tests.