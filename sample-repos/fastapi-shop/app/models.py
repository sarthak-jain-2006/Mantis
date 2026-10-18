from pydantic import BaseModel, Field


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    price: float
    stock: int = Field(ge=0)


class Product(ProductIn):
    id: int


class OrderItem(BaseModel):
    product_id: int
    quantity: int = Field(ge=1)


class OrderIn(BaseModel):
    items: list[OrderItem] = Field(min_length=1)
    discount_code: str | None = None


class Order(BaseModel):
    id: int
    items: list[OrderItem]
    subtotal: float
    discount: float
    shipping: float
    total: float
