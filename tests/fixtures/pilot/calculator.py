"""Small isolated coding pilot: positive integer invoice quantities."""


def invoice_total(unit_price: int, quantity: int) -> int:
    if unit_price < 0 or quantity < 0:
        raise ValueError("Invoice values must be nonnegative")
    return unit_price * quantity
