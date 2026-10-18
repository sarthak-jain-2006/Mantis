# fastapi-shop

A small shop API: products, orders and order pricing. Data is kept in memory.

## Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
pytest -q
```

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | /products | List all products |
| GET | /products/{id} | One product |
| POST | /products | Add a product |
| POST | /orders | Place an order |
| GET | /orders/{id} | One order |
