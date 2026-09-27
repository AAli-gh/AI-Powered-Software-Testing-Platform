from shipping.rates import is_free, shipping_cost


def test_light_parcel():
    assert shipping_cost(2) == 9


def test_heavy_parcel_is_cheaper_per_kg():
    assert shipping_cost(20) < 45


def test_express_costs_more():
    assert shipping_cost(2, express=True) > shipping_cost(2)


def test_members_ship_free():
    assert is_free(10, member=True)
