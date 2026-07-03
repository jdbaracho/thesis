import json
from pprint import pprint

from presidio_anonymizer import AnonymizerEngine, OperatorConfig
from presidio_anonymizer.entities import RecognizerResult

# Analyzer output
analyzer_results = [
    RecognizerResult(entity_type="PERSON", start=11, end=15, score=0.8),
    RecognizerResult(entity_type="PERSON", start=17, end=27, score=0.8),
]

# Initialize the engine:
engine = AnonymizerEngine()

# Invoke the anonymize function with the text,
# analyzer results (potentially coming from presidio-analyzer) and
# Operators to get the anonymization output:
result = engine.anonymize(
    text="My name is Bond, James Bond", analyzer_results=analyzer_results
)

print("De-identified text")
print(result.text)

# Analyzer output
analyzer_results = [
    RecognizerResult(entity_type="PERSON", start=11, end=15, score=0.8),
    RecognizerResult(entity_type="PERSON", start=17, end=27, score=0.8),
    RecognizerResult(entity_type="PHONE_NUMBER", start=51, end=63, score=0.8),
    RecognizerResult(entity_type="TITLE", start=64, end=73, score=0.8),
]

text_to_anonymize = "My name is Bond, James Bond and my phone number is 123-456-7890 Professor"

anonymizer = AnonymizerEngine()

# Define anonymization operators
operators = {
    "DEFAULT": OperatorConfig("replace", {"new_value": "<ANONYMIZED>"}),
    "PHONE_NUMBER": OperatorConfig(
        "mask",
        {
            "type": "mask",
            "masking_char": "*",
            "chars_to_mask": 12,
            "from_end": True,
        },
    ),
    "TITLE": OperatorConfig("redact", {}),
}

anonymized_results = anonymizer.anonymize(
    text=text_to_anonymize, analyzer_results=analyzer_results, operators=operators
)

print(f"text: {anonymized_results.text}")
print("detailed result:")

pprint(json.loads(anonymized_results.to_json()))