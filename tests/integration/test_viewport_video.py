"""CPU fixtures for the native viewport encoder and simulator/recording timebase."""

import json
import shutil
import struct
import subprocess
import zlib

import pytest

from cellio.adapters.isaac62 import video
from cellio.adapters.mock import MockPlant
from cellio.analysis.replay import read_attempt
from cellio.artifacts import verify_artifact
from cellio.io import CellIOError
from cellio.runtime.campaign import initial_state, scenario
from cellio.runtime.episode import run_episode


def png(frame, _render):
    def chunk(name, payload):
        return (
            struct.pack(">I", len(payload))
            + name
            + payload
            + struct.pack(">I", zlib.crc32(name + payload))
        )

    frame.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">2I5B", 8, 8, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + bytes(range(24))) * 8))
        + chunk(b"IEND", b"")
    )


def test_missing_codec_leaves_no_partial_output(monkeypatch, tmp_path):
    monkeypatch.setattr(video.shutil, "which", lambda _: None)
    with pytest.raises(CellIOError, match="ffmpeg"):
        video.ViewportVideo(tmp_path / "videos", lambda: None)
    assert not (tmp_path / "videos").exists()


@pytest.mark.skipif(
    not shutil.which("ffmpeg"), reason="optional FFmpeg CLI unavailable"
)
def test_encoded_video_preserves_120_to_30_hz_and_detects_byte_changes(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(video, "viewport_png", png)

    class Plant(MockPlant):
        def start_video(self, directory):
            self.recorder = video.ViewportVideo(directory, lambda: None)

        def capture_video_frame(self, step):
            assert step == self.step
            self.recorder.capture(step)

        def close_video(self):
            return self.recorder.close()

    plant = Plant()
    cfg = scenario("cell_a", profile="isaac62_lockstep")
    cfg["episode"]["horizon_steps"] = 12
    cfg["artifacts"]["video"] = True
    path = tmp_path / "attempt"
    result = run_episode(
        cfg,
        path,
        run_id="unit",
        attempt_id="unit",
        initial_state=initial_state("cell_a", 1),
        environment={"test": True},
        assets={},
        protocol={},
        requirements_lock="unit",
        hardware_session=plant,
        plant_options={"headless": False},
    )
    assert result["completion_status"] == "COMPLETED", result["errors"]
    meta = result["video"]
    assert [meta[k] for k in ("first_step", "last_step", "frame_count", "fps")] == [
        4,
        12,
        3,
        30,
    ]
    assert (
        verify_artifact(path)["ticks"] == 12
        and read_attempt(path)["online_offline_equal"]
    )
    file = path / meta["relative_path"]
    if ffprobe := shutil.which("ffprobe"):
        process = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "stream=nb_frames,r_frame_rate",
                "-of",
                "json",
                str(file),
            ],
            check=True,
            capture_output=True,
        )
        assert json.loads(process.stdout)["streams"] == [
            {"r_frame_rate": "30/1", "nb_frames": "3"}
        ]
    with file.open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        verify_artifact(path)


@pytest.mark.skipif(
    not shutil.which("ffmpeg"), reason="optional FFmpeg CLI unavailable"
)
def test_capture_failure_aborts_encoder_and_releases_output(monkeypatch, tmp_path):
    def fail(path, render):
        raise CellIOError("VIDEO_VIEWPORT", "fixture viewport error")

    monkeypatch.setattr(video, "viewport_png", fail)
    recorder = video.ViewportVideo(tmp_path / "videos", lambda: None)
    with pytest.raises(CellIOError, match="fixture"):
        recorder.capture(4)
    assert recorder.close() is None and list((tmp_path / "videos").iterdir()) == []
