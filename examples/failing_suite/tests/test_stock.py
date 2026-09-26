import pytest

from shop.stock import Wrapper, average, label, load


def test_assertion():
    assert label(0) == "zero"


def test_zero_division():
    assert average(10, 0) == 0


def test_zero_division_again():
    assert average(5, 0) == 0


def test_library():
    load("not json")


def test_in_test():
    items = []
    assert items[0] == 1


def test_chain():
    Wrapper().run("x")


@pytest.fixture
def broken():
    raise KeyError("config")


def test_fixture(broken):
    assert broken


@pytest.mark.xfail(strict=True, reason="possible bug")
def test_strict_xpass():
    assert True


@pytest.mark.parametrize("n", [1, 2])
def test_param(n):
    assert label(n) == "negative"


class TestGroup:
    def test_method(self):
        assert average(1, 0)
