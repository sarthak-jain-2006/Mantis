DISCOUNT_CODES = {"WELCOME10": 0.10, "BULK20": 0.20}
FREE_SHIPPING_FROM = 50.0
SHIPPING_FEE = 4.99


def order_total(lines, discount_code=None):
    """lines: list of (unit_price, quantity). Returns (subtotal, discount, shipping, total)."""
    subtotal = 0.0
    for unit_price, quantity in lines:
        subtotal = subtotal + unit_price * quantity

    discount = 0.0
    if discount_code is not None:
        code = discount_code.strip().upper()
        if code in DISCOUNT_CODES:
            if code == "BULK20":
                count = 0
                for _, quantity in lines:
                    count = count + quantity
                if count >= 10:
                    discount = subtotal * DISCOUNT_CODES[code]
                else:
                    discount = 0.0
            else:
                discount = subtotal * DISCOUNT_CODES[code]
        else:
            raise ValueError(f"Unknown discount code: {discount_code}")

    after_discount = subtotal - discount
    if after_discount >= FREE_SHIPPING_FROM:
        shipping = 0.0
    else:
        if after_discount == 0:
            shipping = 0.0
        else:
            shipping = SHIPPING_FEE

    total = after_discount + shipping
    return round(subtotal, 2), round(discount, 2), round(shipping, 2), round(total, 2)
