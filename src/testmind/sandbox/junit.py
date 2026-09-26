"""Parse pytest's JUnit XML. The XML is produced inside the sandbox, so it is untrusted input:
defusedxml refuses entity expansion (billion laughs), external entities and DTDs."""

from __future__ import annotations

from typing import TYPE_CHECKING

import defusedxml.ElementTree as SafeElementTree
from defusedxml import DefusedXmlException

from testmind.sandbox.models import Outcome, TestCaseResult

if TYPE_CHECKING:  # type annotations only; all parsing goes through defusedxml
    from xml.etree.ElementTree import Element  # nosec B405

MAX_DETAILS = 4000
MAX_MESSAGE = 500


def parse_junit(xml_text: str) -> list[TestCaseResult]:
    if not xml_text.strip():
        return []
    try:
        root = SafeElementTree.fromstring(xml_text)
    except (SafeElementTree.ParseError, DefusedXmlException, ValueError):
        return []
    return [_case(case) for case in root.iter("testcase")]


def _case(case: Element) -> TestCaseResult:
    outcome, message, details = Outcome.PASSED, None, None
    for child in case:
        if child.tag in {"failure", "error"}:
            outcome = Outcome.FAILED if child.tag == "failure" else Outcome.ERROR
            message, details = child.get("message"), child.text
            break
        if child.tag == "skipped":
            is_xfail = (child.get("type") or "") == "pytest.xfail"
            outcome = Outcome.XFAILED if is_xfail else Outcome.SKIPPED
            message = child.get("message")
            break
    return TestCaseResult(
        name=case.get("name", ""),
        classname=case.get("classname", ""),
        outcome=outcome,
        message=message[:MAX_MESSAGE] if message else None,
        details=details[:MAX_DETAILS] if details else None,
        duration_seconds=float(case.get("time") or 0.0),
    )
