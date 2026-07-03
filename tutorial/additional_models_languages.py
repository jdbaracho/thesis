from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider

# import spacy
# spacy.cli.download("es_core_news_md")

# Create configuration containing engine name and models
configuration = {
    "nlp_engine_name": "spacy",
    "models": [
        {"lang_code": "es", "model_name": "es_core_news_md"},
        {"lang_code": "en", "model_name": "en_core_web_lg"},
    ],
}

# Create NLP engine based on configuration
provider = NlpEngineProvider(nlp_configuration=configuration)
nlp_engine_with_spanish = provider.create_engine()

# Pass the created NLP engine and supported_languages to the AnalyzerEngine
analyzer = AnalyzerEngine(
    nlp_engine=nlp_engine_with_spanish, supported_languages=["en", "es"]
)

# Analyze in different languages
results_spanish = analyzer.analyze(text="Mi nombre es Morris", language="es")
print("Results from Spanish request:")
print(results_spanish)

results_english = analyzer.analyze(text="My name is Morris", language="en")
print("Results from English request:")
print(results_english)