"""Explicit ordered built-in scan detectors."""

from slygentify._scan.contracts import Detector
from slygentify._scan.detectors.generic import detect_generic
from slygentify._scan.detectors.javascript import detect_javascript
from slygentify._scan.detectors.python import detect_python

BUILTIN_DETECTORS: tuple[Detector, ...] = (
    detect_generic,
    detect_python,
    detect_javascript,
)

__all__ = ["BUILTIN_DETECTORS", "detect_generic", "detect_javascript", "detect_python"]
