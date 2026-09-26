from inventory.models import Item


def test_total_value():
    assert Item(sku="A1", name="Bolt", quantity=3, price=2.0).total_value() == 6.0
