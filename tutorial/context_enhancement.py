from presidio_analyzer import (
    LemmaContextAwareEnhancer,
    Pattern,
    PatternRecognizer,
    RecognizerRegistry,
    AnalyzerEngine,
)

# Define the regex pattern
regex = r"(\b\d{5}(?:\-\d{4})?\b)"  # very weak regex pattern
zipcode_pattern = Pattern(name="zip code (weak)", regex=regex, score=0.01)

# Define the recognizer with the defined pattern
zipcode_recognizer = PatternRecognizer(
    supported_entity="US_ZIP_CODE", patterns=[zipcode_pattern]
)

registry = RecognizerRegistry()
registry.add_recognizer(zipcode_recognizer)
analyzer = AnalyzerEngine(registry=registry)

# Test
results = analyzer.analyze(text="My zip code is 90210", language="en")

print(f"Result:\n {results}")

# Define the recognizer with the defined pattern and context words
zipcode_recognizer_w_context = PatternRecognizer(
    supported_entity="US_ZIP_CODE",
    patterns=[zipcode_pattern],
    context=["zip", "zipcode"],
)

registry = RecognizerRegistry()
registry.add_recognizer(zipcode_recognizer_w_context)
analyzer = AnalyzerEngine(registry=registry)

# Test
results = analyzer.analyze(text="My zip code is 90210", language="en")
print("Result:")
print(results)

context_aware_enhancer = LemmaContextAwareEnhancer(
    context_similarity_factor=0.45, min_score_with_context_similarity=0.4
)

registry = RecognizerRegistry()
registry.add_recognizer(zipcode_recognizer_w_context)
analyzer = AnalyzerEngine(
    registry=registry, context_aware_enhancer=context_aware_enhancer
)

# Test
results = analyzer.analyze(text="My zip code is 90210", language="en")
print("Result:")
print(results)

# Define the recognizer with the defined pattern and context words
zipcode_recognizer = PatternRecognizer(
    supported_entity="US_ZIP_CODE",
    patterns=[zipcode_pattern],
    context=["zip", "zipcode"],
)
registry = RecognizerRegistry()
registry.add_recognizer(zipcode_recognizer)
analyzer = AnalyzerEngine(registry=registry)

# Test with an example record having a column name which could be injected as context
record = {"column_name": "zip", "text": "My code is 90210"}

result = analyzer.analyze(
    text=record["text"], language="en", context=[record["column_name"]]
)

print("Result:")
print(result)
