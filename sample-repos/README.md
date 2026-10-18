# Sample repositories

Small Python projects that Mantis works on during development, demos and benchmarks. Mantis never edits these folders directly: each task works on its own copy under `workspaces/<task_id>/`.

## fastapi-shop

A FastAPI shop with products, orders and pricing, plus a passing pytest suite.

It has deliberate gaps that later become benchmark tasks (Phase 9). They are listed here, outside the project folder, so Mantis doesn't see hints while it works:

| Gap | Where | Benchmark task |
| --- | --- | --- |
| `GET /products` returns everything | `app/routers/products.py` | Add limit/offset pagination |
| `ProductIn.price` has no lower bound, so negative prices are accepted | `app/models.py` | Fix the validation bug |
| `order_total` has no tests | `app/pricing.py` | Add unit tests |
| `order_total` is long and nested | `app/pricing.py` | Refactor without changing behaviour |
| `POST /orders` returns 200 instead of 201 | `app/routers/orders.py` | Fix the status code |
| `/orders` has no authentication | `app/routers/orders.py` | Add JWT auth |

Run its tests with:

```bash
cd sample-repos/fastapi-shop
pip install -r requirements.txt
pytest -q
```
