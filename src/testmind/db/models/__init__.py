"""All ORM models. Importing this package registers every table on `Base.metadata`."""

from testmind.db.models.analysis_run import AnalysisRun
from testmind.db.models.project import Project

__all__ = ["AnalysisRun", "Project"]
