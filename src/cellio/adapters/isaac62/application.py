"""Own the native process lifetime at an executable's boundary.

The supported Kit fast-shutdown API terminates the process. A worker must first
close its stage, finish traces, flush the heartbeat, and publish its exit status.
Library users retain explicit, returning app.close() semantics.
"""

import sys
import traceback

_worker = False
_app = None


def start_application(config):
    global _app
    if _worker and _app is not None:
        raise RuntimeError("An Isaac worker can own only one Kit application")
    from isaacsim.simulation_app import SimulationApp
    app = SimulationApp({**config, "fast_shutdown": _worker})
    if _worker:
        _app = app
    return app


def release_application(app):
    if not _worker:
        app.close(wait_for_replicator=False)


def worker_main(main, *args):
    global _worker, _app
    _worker = True
    status = 4
    try:
        status = main(*args)
    except KeyboardInterrupt:
        status = 130
    except SystemExit as exc:
        status = exc.code if isinstance(exc.code, int) else 1
    except Exception:
        traceback.print_exc()
    finally:
        app, _app = _app, None
        sys.stdout.flush()
        sys.stderr.flush()
        if app is not None:
            app.close(wait_for_replicator=False, exit_code=status)
        _worker = False
    return status
