from fastapi import APIRouter, HTTPException

from app import store
from app.models import Product, ProductIn

router = APIRouter(prefix="/products", tags=["products"])


@router.get("", response_model=list[Product])
def list_products():
    return sorted(store.products.values(), key=lambda p: p.id)


@router.get("/{product_id}", response_model=Product)
def get_product(product_id: int):
    product = store.products.get(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.post("", response_model=Product, status_code=201)
def create_product(body: ProductIn):
    product = Product(id=store.next_id(store.products), **body.model_dump())
    store.products[product.id] = product
    return product
