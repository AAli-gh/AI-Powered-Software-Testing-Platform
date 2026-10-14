from calc import add, clamp, is_even, mul, sign, sub


def test_add() -> None:
    assert add(2, 3) == 5


def test_sub() -> None:
    assert sub(5, 2) == 3


def test_mul() -> None:
    assert mul(4, 3) == 12


def test_is_even() -> None:
    assert is_even(4) is True
    assert is_even(5) is False


def test_clamp_low() -> None:
    assert clamp(-1, 0, 10) == 0


def test_clamp_high() -> None:
    assert clamp(99, 0, 10) == 10


def test_clamp_inside() -> None:
    assert clamp(5, 0, 10) == 5


def test_sign() -> None:
    assert sign(3) == 1
    assert sign(-3) == -1
    assert sign(0) == 0
