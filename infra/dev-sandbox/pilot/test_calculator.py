import pytest
from calculator import invoice_total


def test_total():
    assert invoice_total(125, 3) == 375
    assert invoice_total(125, 0) == 0


@pytest.mark.parametrize("price,quantity", [(-1, 2), (1, -2)])
def test_negative_values(price, quantity):
    with pytest.raises(ValueError):
        invoice_total(price, quantity)
