"""Compatibility module alias for the canonical neutral incoming v2 contract."""

from __future__ import annotations

import importlib
import sys


_canonical = importlib.import_module("akbs_member_ops.incoming_v2.contract")
sys.modules[__name__] = _canonical
