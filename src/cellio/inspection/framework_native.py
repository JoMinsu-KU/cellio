"""Run Kit on its own main thread, with physics and decisions gated by CellIO."""

import multiprocessing
from pathlib import Path

from cellio.core.models import AdvanceReceipt, CellIOError

from .framework_oracle import pose_state


class StopNative(BaseException):
    """The owning episode finished or its process disappeared."""


class NativeHandshake:
    def __init__(self, channel, warmup):
        self.channel = channel
        self.warmup = warmup
        self.step = 0
        self.published = False
        self.pending = {}
        self.output = None

    def start(self, runtime):
        self.runtime = runtime
        if self.warmup:
            runtime.step(steps=self.warmup, update_fabric=True)

    def _request(self):
        try:
            msg = self.channel.recv()
        except EOFError as exc:
            raise StopNative from exc
        if msg == ("close",):
            raise StopNative
        return msg

    def _publish(self):
        if not self.published:
            self.channel.send(
                (
                    "boundary",
                    self.step,
                    float(self.runtime.get_simulation_time()),
                    self.pending,
                )
            )
            self.published = True

    def _advance(self):
        self._publish()
        if self._request() != ("advance",):
            raise RuntimeError("Expected one authorized physics advance")
        self.runtime.step(steps=1, update_fabric=True)
        self.step += 1
        self.published = False
        self.pending = {}

    def advance_frame(self):
        for _ in range(4):
            self._advance()

    def event(self, frame, event, fields):
        if self.step != frame * 4 or self.published or self.pending:
            raise RuntimeError(
                f"Unexpected native observation boundary at frame {frame}"
            )
        self.pending = {
            "event": event,
            **{k: fields[k] for k in ("part", "station") if k in fields},
        }
        if event == "output_settled":
            self.pending.update(pose_state(self.output))
            self.output = None

    def set_output(self, output):
        self.output = output

    def inspect(self, frame, part, probe, within_tolerance, geometry):
        self.event(frame, "inspection", {"part": part})
        self.pending.update(
            probe_height_mm=float(probe),
            probe_ok=bool(within_tolerance),
            geometry_height_mm=float(geometry),
        )
        self._publish()
        msg = self._request()
        if (
            len(msg) != 3
            or msg[0] != "decision"
            or msg[1] not in ("P1", "P2")
            or msg[2] not in ("OK", "NG")
        ):
            raise RuntimeError(
                "Missing or invalid inspection command from episode controller"
            )
        self.channel.send(("accepted", self.step))
        return msg[1:]

    def finish(self):
        # Preserve the fixed episode horizon by actually stepping the settled scene.
        while True:
            self._advance()


def _child_main(channel, folder, stale, assets, recipe, warmup):
    from cellio.inspection.study.run_inspection import run

    try:
        return run(
            folder,
            stale,
            assets=assets,
            recipe=recipe,
            bridge=NativeHandshake(channel, warmup),
        )
    except StopNative:
        return 0
    except Exception as exc:
        import traceback

        channel.send(("error", f"{type(exc).__name__}: {exc}", traceback.format_exc()))
        raise
    finally:
        channel.close()


def _child(*args):
    from cellio.adapters.isaac62.application import worker_main

    # The supervisor/worker pair already owns the workstation GPU leases.
    raise SystemExit(worker_main(_child_main, *args))


class NativeConnection:
    def __init__(self, folder, stale, assets, recipe, warmup):
        self.folder = Path(folder)
        self.folder.mkdir()
        ctx = multiprocessing.get_context("spawn")
        self.channel, child = ctx.Pipe()
        self.process = ctx.Process(
            target=_child,
            args=(child, self.folder, stale, assets, recipe, warmup),
            name="cellio-inspection-physx",
        )
        try:
            self.process.start()
        except BaseException:
            self.channel.close()
            raise
        finally:
            child.close()
        try:
            self.step, self.time, self.state = self.boundary(0)
        except BaseException:
            self.close()
            raise

    def receive(self):
        if not self.channel.poll(240):
            raise CellIOError(
                "SIM_NATIVE_TIMEOUT",
                "Timed out waiting for the inspection physics worker",
            )
        try:
            msg = self.channel.recv()
        except (EOFError, OSError) as exc:
            raise CellIOError(
                "SIM_NATIVE_EXIT", f"Inspection worker exited at {self.folder}"
            ) from exc
        if msg[0] == "error":
            raise CellIOError("SIM_NATIVE_ERROR", msg[1] + "\n" + msg[2])
        return msg

    def boundary(self, expected):
        msg = self.receive()
        if len(msg) != 4 or msg[:2] != ("boundary", expected):
            raise CellIOError(
                "CLOCK_ORDER", f"Unexpected inspection worker frame {msg[:2]}"
            )
        return msg[1:]

    def decision(self, owner, route):
        self.channel.send(("decision", owner, route))
        if self.receive() != ("accepted", self.step):
            raise CellIOError(
                "SIM_ACTUATION_FAILED", "Native inspection route was not accepted"
            )

    def advance(self):
        self.channel.send(("advance",))
        step, time = self.step, self.time
        self.step, self.time, self.state = self.boundary(step + 1)
        return AdvanceReceipt(step, self.step, time, self.time, True, False)

    def close(self):
        try:
            if self.process.is_alive():
                self.channel.send(("close",))
        except (OSError, EOFError):
            pass
        finally:
            self.channel.close()
        self.process.join(20)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(10)
        if self.process.is_alive():
            self.process.kill()
            self.process.join()
        self.process.close()
