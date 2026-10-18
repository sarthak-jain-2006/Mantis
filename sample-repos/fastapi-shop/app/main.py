from fastapi import FastAPI

from app.routers import orders, products

app = FastAPI(title="fastapi-shop")
app.include_router(products.router)
app.include_router(orders.router)


@app.get("/health")
def health():
    return {"status": "ok"}
