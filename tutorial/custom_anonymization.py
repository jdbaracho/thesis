from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig, EngineResult, RecognizerResult
from faker import Faker


fake = Faker()

# Create faker function (note that it has to receive a value)
def fake_name(x):
    return fake.name()

# Create custom operator for the PERSON entity
operators = {"PERSON": OperatorConfig("custom", {"lambda": fake_name})}

# Analyzer output
analyzer_results = [RecognizerResult(entity_type="PERSON", start=11, end=18, score=0.8)]

text_to_anonymize = "My name is Raphael and I like to fish."

anonymizer = AnonymizerEngine()

anonymized_results = anonymizer.anonymize(
    text=text_to_anonymize, analyzer_results=analyzer_results, operators=operators
)

print(anonymized_results.text)