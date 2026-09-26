def price_for(customer_type, quantity, base_price, coupon=None, season=None, member_years=0):
    price = base_price * quantity
    if customer_type == "wholesale":
        if quantity > 100:
            price *= 0.8
        elif quantity > 50:
            price *= 0.85
        else:
            price *= 0.9
    elif customer_type == "retail":
        if member_years > 5 and quantity > 10:
            price *= 0.95
        elif member_years > 2 or quantity > 20:
            price *= 0.97
    if coupon:
        if coupon == "SAVE10":
            price -= 10
        elif coupon == "HALF" and season != "holiday":
            price /= 2
    if season == "holiday":
        price *= 1.1
    elif season == "clearance":
        price *= 0.5
    return max(price, 0)


def apply_discount(price: float, discount: float) -> float:
    if discount < 0 or discount > 100:
        raise ValueError("discount must be between 0 and 100")
    return price - (price * discount / 100)
