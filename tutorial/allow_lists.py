from presidio_analyzer import AnalyzerEngine
text1 = "My favorite website is bing.com, his is microsoft.com"
analyzer = AnalyzerEngine()
result = analyzer.analyze(text = text1, language = 'en')
print(f"Result: \n {result}")

result = analyzer.analyze(text = text1, language = 'en', allow_list = ["bing.com"] )
print(f"Result:\n {result}")