"""Cross-platform asyncio entry points.

Windows gotcha (root cause of "Windows 无法修改定位"): ``asyncio.run()`` defaults to the
*Proactor* event loop on Windows. pymobiledevice3's DTX/DVT stack (strip-SSL handshake on
``dtservicehub``) and pmd-pytcp's userspace tunnel rely on selector semantics
(``add_reader`` / blocking-socket handoff), so on Proactor the DVT channel either hangs or
fails. pymobiledevice3's own CLI forces ``WindowsSelectorEventLoopPolicy`` for this reason
(doronz88/pymobiledevice3#1217). We do the same, explicitly and per call, without touching the
process-global policy.

The iAP2 runner spawns the MFi helper with ``asyncio.create_subprocess_exec``, which the Windows
selector loop does not support, so it keeps using the default loop (``selector=False``).
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def _loop_factory(selector: bool):
    if selector and sys.platform == "win32":
        return asyncio.SelectorEventLoop
    return None


def run(coro: Coroutine[Any, Any, T], *, selector: bool = True) -> T:
    """``asyncio.run`` replacement that uses a SelectorEventLoop on Windows when requested."""
    with asyncio.Runner(loop_factory=_loop_factory(selector)) as runner:
        return runner.run(coro)
