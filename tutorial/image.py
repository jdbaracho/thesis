from PIL import Image
from presidio_image_redactor import ImageRedactorEngine

# Get the image to redact using PIL lib (pillow)
image = Image.open("ocr_text.png")

# Initialize the engine
engine = ImageRedactorEngine()

# Redact the image with pink color
redacted_image = engine.redact(image, (0, 0, 0))

# # Optional: Redact the image and return redacted regions
# redacted_image, bboxes = engine.redact_and_return_bbox(image, (255, 192, 203))

# save the redacted image 
redacted_image.save("new_image.png")
# uncomment to open the image for viewing
# redacted_image.show()