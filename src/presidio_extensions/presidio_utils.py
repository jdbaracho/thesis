from importlib.metadata import PackageNotFoundError, version as _pkg_version
from typing import List
import warnings

from presidio_analyzer import RecognizerResult
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import ConflictResolutionStrategy

# resolve_conflicts() calls three underscore-prefixed methods on AnonymizerEngine
# (see below). Presidio does not expose a public equivalent, so we depend on the
# private API. This guard surfaces a warning if the installed version drifts from
# the tested one, so upgrades don't silently break conflict resolution.
_TESTED_ANONYMIZER_VERSION = "2.2.363"
try:
    _installed_version = _pkg_version("presidio-anonymizer")
except PackageNotFoundError:
    _installed_version = "unknown"
if _installed_version != _TESTED_ANONYMIZER_VERSION:
    warnings.warn(
        f"presidio-anonymizer {_installed_version} differs from tested "
        f"{_TESTED_ANONYMIZER_VERSION}; private API calls in "
        f"resolve_conflicts() may have moved or changed signature.",
        DeprecationWarning,
        stacklevel=2,
    )

_anonymizer = AnonymizerEngine()


def resolve_conflicts(
    text: str,
    results: List[RecognizerResult],
    conflict_resolution: ConflictResolutionStrategy = (
        ConflictResolutionStrategy.MERGE_SIMILAR_OR_CONTAINED
    ),
) -> List[RecognizerResult]:
    """Run Presidio's conflict-resolution + whitespace-merge pipeline."""
    results = _anonymizer._copy_recognizer_results(results)
    results.sort(key=lambda x: (x.start, x.end))
    results = _anonymizer._remove_conflicts_and_get_text_manipulation_data(
        results, conflict_resolution
    )
    return _anonymizer._merge_entities_with_spaces_between(text, results)
