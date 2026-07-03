from presidio_analyzer import (
    LemmaContextAwareEnhancer,
    Pattern,
    PatternRecognizer,
    RecognizerRegistry,
    AnalyzerEngine,
)
import pprint

# Define the regex pattern
regex = r"(\b\d{5}(?:\-\d{4})?\b)"  # very weak regex pattern
zipcode_pattern = Pattern(name="zip code (weak)", regex=regex, score=0.01)

# Define the recognizer with the defined pattern
zipcode_recognizer = PatternRecognizer(
    supported_entity="US_ZIP_CODE", patterns=[zipcode_pattern]
)

# Define the recognizer with the defined pattern and context words
zipcode_recognizer_w_context = PatternRecognizer(
    supported_entity="US_ZIP_CODE",
    patterns=[zipcode_pattern],
    context=["zip", "zipcode"],
)

registry = RecognizerRegistry()
registry.add_recognizer(zipcode_recognizer_w_context)
analyzer = AnalyzerEngine(registry=registry)

results = analyzer.analyze(
    text="My zip code is 90210", language="en", return_decision_process=True
)

print("Results:")
print(results)

decision_process = results[0].analysis_explanation

pp = pprint.PrettyPrinter()
print("Decision process output:\n")
pp.pprint(decision_process.__dict__)