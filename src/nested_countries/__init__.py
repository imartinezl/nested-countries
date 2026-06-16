"""Nested Countries Challenge solver.

Find the longest chain of countries A1 -> A2 -> ... -> An such that each Ai+1
fits strictly inside Ai using translation and rotation only (no scaling).
"""

from __future__ import annotations

from .models import CountryShape, Placement, SearchSettings

__version__ = "0.1.0"

__all__ = ["CountryShape", "Placement", "SearchSettings", "__version__"]
