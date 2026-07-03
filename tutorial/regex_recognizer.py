from presidio_analyzer import Pattern, PatternRecognizer

# Define the regex pattern in a Presidio `Pattern` object:
numbers_pattern = Pattern(name="numbers_pattern", regex=r"\d+", score=0.5)

# Define the recognizer with one or more patterns
number_recognizer = PatternRecognizer(
    supported_entity="NUMBER", patterns=[numbers_pattern]
)

text2 = "I live in 510 Broad st."

numbers_result = number_recognizer.analyze(text=text2, entities=["NUMBER"])

print("Result:")
print(numbers_result)