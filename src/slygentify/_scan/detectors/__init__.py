"""Explicit ordered built-in scan detectors."""

from slygentify._scan.contracts import Detector
from slygentify._scan.detectors.autotools import detect_autotools
from slygentify._scan.detectors.bazel import detect_bazel
from slygentify._scan.detectors.cmake import detect_cmake
from slygentify._scan.detectors.cpp_dependencies import detect_cpp_dependencies
from slygentify._scan.detectors.generic import detect_generic
from slygentify._scan.detectors.javascript import detect_javascript
from slygentify._scan.detectors.meson import detect_meson
from slygentify._scan.detectors.python import detect_python
from slygentify._scan.detectors.visual_studio import detect_msbuild_make

BUILTIN_DETECTORS: tuple[Detector, ...] = (
    detect_generic,
    detect_python,
    detect_javascript,
    detect_cmake,
    detect_meson,
    detect_autotools,
    detect_bazel,
    detect_msbuild_make,
    detect_cpp_dependencies,
)

__all__ = ["BUILTIN_DETECTORS", "detect_generic", "detect_javascript", "detect_python"]
