from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, Text, func, select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, array
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from testmind.db.models import TestExecution

# Most of a report is its per-test list and logs, which a summary does not show. Summaries
# read the report without them and none of the other documents: with five executions of a
# 300-test suite, this is what a run's execution list costs (docs/performance.md).
REPORT_BULK = ("tests", "output")
INSTALL_LOG = ("install", "log")


@dataclass(frozen=True, slots=True)
class ExecutionSummaryRow:
    execution: TestExecution  # report, coverage, failures and api_coverage are not loaded
    report: dict[str, Any] | None  # without REPORT_BULK and the installation log
    failure_groups: int | None


def _summaries() -> Select[Any]:
    report = TestExecution.report.op("-", return_type=JSONB)(array(REPORT_BULK, type_=Text)).op(
        "#-", return_type=JSONB
    )(array(INSTALL_LOG, type_=Text).cast(ARRAY(Text)))
    return select(
        TestExecution,
        report.label("report_summary"),
        func.jsonb_array_length(TestExecution.failures["groups"]).label("failure_groups"),
    ).options(
        defer(TestExecution.report, raiseload=True),
        defer(TestExecution.coverage, raiseload=True),
        defer(TestExecution.failures, raiseload=True),
        defer(TestExecution.api_coverage, raiseload=True),
    )


def _summary_row(row: Any) -> ExecutionSummaryRow:
    return ExecutionSummaryRow(row[0], row.report_summary, row.failure_groups)


class ExecutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, execution: TestExecution) -> TestExecution:
        self._session.add(execution)
        await self._session.flush()
        return execution

    async def get(self, execution_id: uuid.UUID) -> TestExecution | None:
        return await self._session.get(TestExecution, execution_id)

    async def summary(self, execution_id: uuid.UUID) -> ExecutionSummaryRow | None:
        result = await self._session.execute(
            _summaries()
            .where(TestExecution.id == execution_id)
            .execution_options(populate_existing=True)
        )
        row = result.one_or_none()
        return _summary_row(row) if row is not None else None

    async def summaries_for_run(
        self, run_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[list[ExecutionSummaryRow], int]:
        rows = await self._session.execute(
            _summaries()
            .where(TestExecution.run_id == run_id)
            .order_by(TestExecution.created_at.desc(), TestExecution.id)
            .offset(offset)
            .limit(limit)
            .execution_options(populate_existing=True)
        )
        total = await self._session.execute(
            select(func.count()).select_from(TestExecution).where(TestExecution.run_id == run_id)
        )
        return [_summary_row(row) for row in rows], total.scalar_one()

    async def get_for_update(self, execution_id: uuid.UUID) -> TestExecution | None:
        result = await self._session.execute(
            select(TestExecution)
            .where(TestExecution.id == execution_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()
