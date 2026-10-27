from __future__ import annotations

import os
import subprocess
from typing import Any, cast


def run_hidden(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Run a captured helper process without opening a Windows console window."""
    if os.name == "nt":
        flags = kwargs.get("creationflags", 0)
        if not isinstance(flags, int):
            raise TypeError("creationflags must be an integer")
        kwargs["creationflags"] = flags | int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return cast(subprocess.CompletedProcess[str], subprocess.run(*args, **kwargs))
