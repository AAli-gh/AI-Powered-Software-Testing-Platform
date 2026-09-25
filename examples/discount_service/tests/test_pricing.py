from discount_service.pricing import calculate_discount


def test_normal_discount() -> None:
    assert calculate_discount(100, 20) == 80


def test_zero_discount() -> None:
    assert calculate_discount(100, 0) == 100
