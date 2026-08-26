"""Private orchestration entry points for repository scans."""

from slygentify._scan.kernel import _ScanFoundationError
from slygentify._scan.orchestration import _scan_foundation

__all__ = ["_ScanFoundationError", "_scan_foundation"]
