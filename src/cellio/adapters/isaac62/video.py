"""Encode optional native GUI viewport observations at the simulator's 30 Hz cadence."""

import asyncio
import os
import shutil
import subprocess
import tempfile
from contextlib import ExitStack
from pathlib import Path
from time import monotonic

from cellio.io import CellIOError, file_hash


def viewport_png(destination, render):
    from omni.kit.viewport.utility import capture_viewport_to_file, get_active_viewport

    viewport = get_active_viewport()
    if viewport is None:
        raise CellIOError("VIDEO_VIEWPORT", "The simulator has no active GUI viewport")
    capture = capture_viewport_to_file(viewport, str(destination), is_hdr=False)
    future = asyncio.ensure_future(capture.wait_for_result(completion_frames=0))
    deadline = monotonic() + 15
    try:
        while not future.done():
            if monotonic() >= deadline:
                raise CellIOError(
                    "VIDEO_TIMEOUT", "Timed out capturing the simulator viewport"
                )
            render()  # RenderingManager.render() updates Kit with playSimulations disabled.
        if not future.result():
            raise CellIOError(
                "VIDEO_VIEWPORT", "Viewport returned no LDR color render product"
            )
        # GPU file capture is asynchronous to the delegate scheduling request.
        import omni.kit.renderer_capture

        omni.kit.renderer_capture.acquire_renderer_capture_interface().wait_async_capture()
        if not destination.is_file():
            raise CellIOError(
                "VIDEO_VIEWPORT", "Viewport did not produce the requested PNG"
            )
    finally:
        if not future.done():
            future.cancel()


class ViewportVideo:
    def __init__(self, directory, render):
        self.directory = Path(directory)
        self.render = render
        self.count = 0
        self.first = self.last = None
        self.metadata = None
        self.finished = False
        encoder = shutil.which("ffmpeg")
        if encoder is None:
            raise CellIOError(
                "VIDEO_CODEC",
                "GUI video requires ffmpeg with the libx264 encoder on PATH",
            )
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        check = subprocess.run(
            [encoder, "-hide_banner", "-encoders"],
            check=False,
            capture_output=True,
            timeout=15,
            creationflags=flags,
        )
        if check.returncode or b" libx264 " not in check.stdout:
            raise CellIOError(
                "VIDEO_CODEC", "The installed ffmpeg does not provide libx264"
            )
        self.directory.mkdir(parents=True, exist_ok=True)
        self.target = self.directory / "viewport.mp4"
        self.partial = self.directory / "viewport.mp4.partial"
        if self.target.exists() or self.partial.exists():
            raise CellIOError(
                "VIDEO_OUTPUT", "This attempt already contains viewport video"
            )
        self.resources = ExitStack()
        try:
            self.staging = Path(
                self.resources.enter_context(
                    tempfile.TemporaryDirectory(prefix="capture-", dir=self.directory)
                )
            )
            self.log = self.resources.enter_context(tempfile.TemporaryFile())  # noqa: SIM115
            self.process = subprocess.Popen(
                [
                    encoder,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-n",
                    "-f",
                    "image2pipe",
                    "-framerate",
                    "30",
                    "-vcodec",
                    "png",
                    "-i",
                    "pipe:0",
                    "-an",
                    "-vf",
                    "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-pix_fmt",
                    "yuv420p",
                    "-f",
                    "mp4",
                    str(self.partial),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=self.log,
                creationflags=flags,
            )
        except BaseException:
            self.resources.close()
            raise

    def capture(self, step):
        if (
            self.finished
            or type(step) is not int
            or step < 0
            or (self.last is not None and step != self.last + 4)
        ):
            raise CellIOError(
                "VIDEO_FRAME_ORDER", f"Invalid GUI video boundary S{step}"
            )
        frame = self.staging / "viewport.png"
        try:
            viewport_png(frame, self.render)
            with frame.open("rb") as stream:
                if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                    raise CellIOError(
                        "VIDEO_VIEWPORT", "The viewport PNG is incomplete"
                    )
                stream.seek(0)
                shutil.copyfileobj(stream, self.process.stdin)
            frame.unlink()
            if self.count == 0:
                self.first = step
            self.count += 1
            self.last = step
        except Exception:
            self.abort()
            raise

    def close(self):
        if self.finished:
            return self.metadata
        if not self.count:
            self.abort()
            return None
        try:
            self.process.stdin.close()
            result = self.process.wait(timeout=40)
            if result or not self.partial.is_file() or self.partial.stat().st_size == 0:
                self.log.seek(0)
                message = self.log.read(1536).decode(errors="replace")
                raise CellIOError(
                    "VIDEO_ENCODE", f"Viewport encoder exited {result}: {message}"
                )
            with self.partial.open("r+b") as stream:
                os.fsync(stream.fileno())
            metadata = {
                "relative_path": "videos/viewport.mp4",
                "fps": 30,
                "physics_hz": 120,
                "first_step": self.first,
                "last_step": self.last,
                "frame_count": self.count,
                "size": self.partial.stat().st_size,
                "sha256": file_hash(self.partial),
                "source": "isaac_gui_viewport",
                "encoder": "ffmpeg/libx264",
            }
            self.partial.replace(self.target)
        except Exception:
            self.abort()
            raise
        self.finished = True
        self.resources.close()
        self.metadata = metadata
        return metadata

    def abort(self):
        if self.finished:
            return
        self.finished = True
        if self.process.poll() is None:
            self.process.kill()
        try:
            self.process.stdin.close()
        except OSError:
            pass
        self.process.wait(timeout=10)
        self.resources.close()
        self.partial.unlink(missing_ok=True)
