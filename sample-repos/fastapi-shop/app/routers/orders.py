from fastapi import APIRouter, HTTPException

from app import store
from app.models import Order, OrderIn
from app.pricing import order_total

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=Order)
def create_order(body: OrderIn):
    lines = []
    for item in body.items:
        product = store.products.get(item.product_id)
        if product is None:
            raise HTTPException(status_code=404, detail=f"Product {item.product_id} not found")
        if product.stock < item.quantity:
            raise HTTPException(status_code=409, detail=f"Not enough stock for {product.name}")
        lines.append((product.price, item.quantity))

    try:
        subtotal, discount, shipping, total = order_total(lines, body.discount_code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    for item in body.items:
        store.products[item.product_id].stock -= item.quantity

    order = Order(
        id=store.next_id(store.orders),
        items=body.items,
        subtotal=subtotal,
        discount=discount,
        shipping=shipping,
        total=total,
    )
    store.orders[order.id] = order
    return order


@router.get("/{order_id}", response_model=Order)
def get_order(order_id: int):
    order = store.orders.get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order
