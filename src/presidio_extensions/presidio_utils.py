from typing import List

from presidio_analyzer import RecognizerResult
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import ConflictResolutionStrategy

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
