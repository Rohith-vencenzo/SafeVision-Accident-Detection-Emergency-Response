"""Optional OpenCV GUI preview with an isolated display-backend preflight."""

import os
import ctypes
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import Any

from .errors import SetupError
from .video import get_cv2


def local_gui_libraries() -> list[Path]:
    """Find optional Linux GUI libraries installed locally into this venv."""
    if not sys.platform.startswith("linux"):
        return []
    directory = Path(sys.prefix) / "lib" / "native" / "usr" / "lib" / str(sysconfig.get_config_var("MULTIARCH"))
    # libSM depends on libICE, so preload in this order.
    return [path for name in ("libICE.so.6", "libSM.so.6") if (path := directory / name).is_file()]


class VideoPreview:
    """Display annotated frames; Q or Escape stops processing and marks it partial."""

    def __init__(self) -> None:
        if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise SetupError("--show needs a graphical display. In headless WSL use --save-output, or enable WSLg/X11.")
        # Qt backends can abort the interpreter rather than raise a Python error.
        # Probe in a child process first so a broken display gives a useful error.
        libraries = local_gui_libraries()
        preload = f"import ctypes; [ctypes.CDLL(p,mode=ctypes.RTLD_GLOBAL) for p in {[str(path) for path in libraries]!r}]; "
        probe = preload + "import cv2,numpy as np; cv2.namedWindow('Display check'); cv2.imshow('Display check',np.zeros((32,32,3),dtype=np.uint8)); cv2.waitKey(1); cv2.destroyAllWindows()"
        try:
            result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SetupError(f"Display initialization check failed: {exc}. Run without --show and save output instead.") from exc
        if result.returncode:
            raise SetupError("OpenCV's GUI backend could not initialize. On Linux check libSM/libICE and WSLg/X11; see README.md, or run without --show.")
        try:
            for library in libraries:
                ctypes.CDLL(str(library), mode=ctypes.RTLD_GLOBAL)
        except OSError as exc:
            raise SetupError(f"Local GUI dependencies could not load: {exc}. Run without --show or repair the GUI environment.") from exc
        self.cv2 = get_cv2()
        self.name = "Traffic accident observations — Q/Escape to stop"
        try:
            self.cv2.namedWindow(self.name, self.cv2.WINDOW_NORMAL)
        except Exception as exc:
            raise SetupError(f"Cannot create preview window: {exc}. Run without --show.") from exc

    def __enter__(self) -> "VideoPreview":
        return self

    def __exit__(self, *args: Any) -> None:
        self.cv2.destroyAllWindows()

    def show(self, image: Any) -> bool:
        """Return False for Q/Escape or a closed window."""
        try:
            self.cv2.imshow(self.name, image)
            key = self.cv2.waitKey(1) & 0xFF
            return key not in (ord("q"), ord("Q"), 27) and self.cv2.getWindowProperty(self.name, self.cv2.WND_PROP_VISIBLE) >= 1
        except Exception as exc:
            raise SetupError(f"Video preview failed: {exc}. Try saving output without --show.") from exc
