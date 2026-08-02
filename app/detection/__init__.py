from .base import TextDetector
from .opencv_detector import OpenCVTextDetector
from .rapidocr_detector import RapidOCRDetector

__all__ = ["OpenCVTextDetector", "RapidOCRDetector", "TextDetector"]
