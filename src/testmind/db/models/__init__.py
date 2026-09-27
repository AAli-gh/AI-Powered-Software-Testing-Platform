"""All ORM models. Importing this package registers every table on `Base.metadata`."""

from testmind.db.models.analysis_run import AnalysisRun
from testmind.db.models.api_test_suggestion import ApiTestSuggestion
from testmind.db.models.code_analysis import CodeAnalysis
from testmind.db.models.failure_analysis import FailureAnalysis
from testmind.db.models.mutation_run import MutantTestSuggestion, MutationRun
from testmind.db.models.project import Project
from testmind.db.models.regression import Regression
from testmind.db.models.repository_analysis import RepositoryAnalysis
from testmind.db.models.test_execution import TestExecution
from testmind.db.models.test_generation import TestGeneration

__all__ = [
    "AnalysisRun",
    "ApiTestSuggestion",
    "CodeAnalysis",
    "FailureAnalysis",
    "MutantTestSuggestion",
    "MutationRun",
    "Project",
    "Regression",
    "RepositoryAnalysis",
    "TestExecution",
    "TestGeneration",
]
