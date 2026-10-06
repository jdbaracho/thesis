from typing import List, Optional, Tuple

from presidio_analyzer import AnalyzerEngine
from presidio_image_redactor.entities import ImageRecognizerResult
from presidio_image_redactor import ImageAnalyzerEngine

from src.presidio_extensions.presidio_utils import resolve_conflicts

class CustomImageAnalyzerEngine(ImageAnalyzerEngine):
    def analyze(
        self, image: object, ocr_kwargs: Optional[dict] = None, **text_analyzer_kwargs
    ) -> Tuple[List[ImageRecognizerResult], str]:
        """Analyse method to analyse the given image.

        :param image: PIL Image/numpy array or file path(str) to be processed.
        :param ocr_kwargs: Additional params for OCR methods.
        :param text_analyzer_kwargs: Additional values for the analyze method
        in AnalyzerEngine.

        :return: List of the extract entities with image bounding boxes.
        """
        ocr_result, text = self.extract_ocr(image, ocr_kwargs)

        # Difines English as default language, if not specified
        if "language" not in text_analyzer_kwargs:
            text_analyzer_kwargs["language"] = "en"
        analyzer_result = self.analyzer_engine.analyze(
            text=text, **text_analyzer_kwargs
        )
        analyzer_result = resolve_conflicts(text, analyzer_result)
        allow_list = self._check_for_allow_list(text_analyzer_kwargs)
        bboxes = self.map_results(analyzer_result, ocr_result, text, allow_list)

        return bboxes, text

    def extract_ocr(
        self, image: object, ocr_kwargs: Optional[dict] = None
    ) -> Tuple[dict, str]:
        """Run OCR and return ``(ocr_result, text)`` without any PII analysis.

        Exposed so callers can cache the OCR output and re-map new recognizer
        results (e.g. a document-wide deny-list pass) onto the same image
        without paying for OCR again.
        """
        perform_ocr_kwargs, ocr_threshold = self._parse_ocr_kwargs(ocr_kwargs)
        image, preprocessing_metadata = self.image_preprocessor.preprocess_image(image)
        ocr_result = self.ocr.perform_ocr(image, **perform_ocr_kwargs)
        ocr_result = self.remove_space_boxes(ocr_result)

        if preprocessing_metadata and ("scale_factor" in preprocessing_metadata):
            ocr_result = self._scale_bbox_results(
                ocr_result, preprocessing_metadata["scale_factor"]
            )

        # Apply OCR confidence threshold if it is passed in
        if ocr_threshold:
            ocr_result = self.threshold_ocr_result(ocr_result, ocr_threshold)

        text = self.ocr.get_text_from_ocr_dict(ocr_result)
        return ocr_result, text

    def map_results(
        self,
        analyzer_result: List,
        ocr_result: dict,
        text: str,
        allow_list: Optional[List[str]] = None,
    ) -> List[ImageRecognizerResult]:
        """Map analyzer results to merged image bounding boxes."""
        bboxes = self.map_analyzer_results_to_bounding_boxes(
            analyzer_result, ocr_result, text, allow_list or []
        )
        return self._merge_entity_bboxes(bboxes)
    
    def _merge_entity_bboxes(self, bboxes) -> List[ImageRecognizerResult]:
        """Merge bboxes that belong to the same entity span (same start/end/type)."""
        from presidio_image_redactor.entities import ImageRecognizerResult
        grouped: dict = {}
        order: list = []
        for b in bboxes:
            key = (b.start, b.end, b.entity_type)
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(b)

        merged = []
        for key in order:
            group = grouped[key]
            if len(group) == 1:
                merged.append(group[0])
                continue
            left = min(b.left for b in group)
            top = min(b.top for b in group)
            right = max(b.left + b.width for b in group)
            bottom = max(b.top + b.height for b in group)
            score = max(b.score for b in group)
            merged.append(ImageRecognizerResult(
                entity_type=group[0].entity_type,
                start=group[0].start,
                end=group[0].end,
                score=score,
                left=left, top=top,
                width=right - left, height=bottom - top,
            ))
        return merged