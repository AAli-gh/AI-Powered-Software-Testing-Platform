from testmind.sandbox.junit import parse_junit
from testmind.sandbox.models import Outcome

REPORT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="5">
  <testcase classname="test_x" name="test_ok" time="0.010">
    <system-out>printed</system-out></testcase>
  <testcase classname="test_x" name="test_fail" time="0.020">
    <failure message="assert 1 == 2">Traceback details</failure></testcase>
  <testcase classname="test_x" name="test_error" time="0">
    <error message="fixture failed">boom</error></testcase>
  <testcase classname="test_x" name="test_skip">
    <skipped type="pytest.skip" message="later"/></testcase>
  <testcase classname="test_x" name="test_xfail">
    <skipped type="pytest.xfail" message="possible bug: x"/></testcase>
</testsuite></testsuites>"""


def test_outcomes() -> None:
    cases = {c.name: c for c in parse_junit(REPORT)}
    assert {n: c.outcome for n, c in cases.items()} == {
        "test_ok": Outcome.PASSED,
        "test_fail": Outcome.FAILED,
        "test_error": Outcome.ERROR,
        "test_skip": Outcome.SKIPPED,
        "test_xfail": Outcome.XFAILED,
    }
    assert cases["test_fail"].message == "assert 1 == 2"
    assert cases["test_fail"].details == "Traceback details"
    assert cases["test_fail"].duration_seconds == 0.02
    assert cases["test_xfail"].message == "possible bug: x"


def test_empty_and_malformed() -> None:
    assert parse_junit("") == []
    assert parse_junit("<testsuite><testcase") == []


def test_entity_expansion_is_refused() -> None:
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;">]><testsuite><testcase name="&lol2;"/></testsuite>'
    )
    assert parse_junit(bomb) == []


def test_long_messages_are_truncated() -> None:
    failure = f'<failure message="{"m" * 900}">{"d" * 9000}</failure>'
    xml = f'<testsuite><testcase name="t">{failure}</testcase></testsuite>'
    [case] = parse_junit(xml)
    assert case.message is not None
    assert len(case.message) == 500
    assert case.details is not None
    assert len(case.details) == 4000


def test_xunit1_file_and_line() -> None:
    xml = (
        '<testsuite><testcase classname="c" name="t" file="tests/test_a.py" line="9"/></testsuite>'
    )
    [case] = parse_junit(xml)
    assert (case.file, case.line) == ("tests/test_a.py", 10)


def test_missing_or_invalid_line() -> None:
    xml = '<testsuite><testcase name="a"/><testcase name="b" line="x"/></testsuite>'
    assert [(c.file, c.line) for c in parse_junit(xml)] == [(None, None), (None, None)]
