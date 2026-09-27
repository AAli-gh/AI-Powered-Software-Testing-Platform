def shipping_cost(weight: float, express: bool = False) -> float:
    """Base 5 plus 2 per kg; 10 % off above 10 kg; express adds 15."""
    if weight <= 0:
        raise ValueError("weight must be positive")
    cost = 5 + weight * 2
    if weight > 10:
        cost = cost * 0.9
    if express:
        cost = cost + 15
    return round(cost, 2)


def is_free(total: float, member: bool) -> bool:
    """Orders of 100 or more, and members' orders, ship for free."""
    return total >= 100 or member
