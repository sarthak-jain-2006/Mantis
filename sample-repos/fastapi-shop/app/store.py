"""In-memory storage. Good enough for a demo; restarting the app resets it."""

from app.models import Order, Product

SEED_PRODUCTS = [
    Product(id=1, name="Notebook", price=4.5, stock=120),
    Product(id=2, name="Pen", price=1.2, stock=500),
    Product(id=3, name="Backpack", price=35.0, stock=15),
    Product(id=4, name="Water bottle", price=12.0, stock=40),
    Product(id=5, name="Desk lamp", price=22.0, stock=8),
]

products: dict[int, Product] = {}
orders: dict[int, Order] = {}


def reset() -> None:
    products.clear()
    orders.clear()
    for product in SEED_PRODUCTS:
        products[product.id] = product.model_copy()


def next_id(table: dict) -> int:
    return max(table, default=0) + 1


reset()
