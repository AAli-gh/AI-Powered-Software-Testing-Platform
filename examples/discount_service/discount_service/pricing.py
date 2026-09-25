def calculate_discount(price: float, discount: float) -> float:
    """Return `price` reduced by `discount` percent."""
    return price - (price * discount / 100)
