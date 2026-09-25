from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AnalysisLimits:
    """Upper bounds that keep analysis of hostile or huge repositories cheap and predictable."""

    max_files: int = 20_000
    # Files larger than this are listed but never read (no line count, no parsing).
    max_read_bytes: int = 1_000_000
    # Stop descending below this depth (protects against pathological nesting).
    max_depth: int = 32
