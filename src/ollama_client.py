"""Hardcoded catalogue of models surfaced in the UI.

This module previously called Ollama's ``GET /api/tags`` to discover the
downloaded models at runtime. The list is now pinned to a small
hand-curated set below; edit ``_MODELS`` to change what the UI offers.
"""

from __future__ import annotations

import dataclasses
from typing import List


__all__ = ["ModelInfo", "list_models"]


CATEGORY_FASTEST = "Fastest · lower accuracy"
CATEGORY_BALANCED = "Balanced"
CATEGORY_SLOWEST = "Slowest · highest accuracy"


@dataclasses.dataclass(frozen=True)
class ModelInfo:
    """One entry in the model list surfaced to the UI."""

    id: str
    parameter_size: str
    disk_size: str
    category: str


# Ordered fastest → slowest so the UI renders optgroups in that order.
_MODELS: List[ModelInfo] = [
    ModelInfo(
        id="gemma3:1b",
        parameter_size="1B",
        disk_size="815 MB",
        category=CATEGORY_FASTEST,
    ),
    ModelInfo(
        id="gemma3:4b",
        parameter_size="4B",
        disk_size="3.3 GB",
        category=CATEGORY_FASTEST,
    ),
    ModelInfo(
        id="gemma3:12b",
        parameter_size="12B",
        disk_size="8.1 GB",
        category=CATEGORY_BALANCED,
    ),
    ModelInfo(
        id="gemma3:27b",
        parameter_size="27B",
        disk_size="17 GB",
        category=CATEGORY_SLOWEST,
    ),
]


def list_models() -> List[ModelInfo]:
    """Return the hardcoded model catalogue."""
    return list(_MODELS)
