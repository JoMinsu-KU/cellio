"""Record selected, trace-linked operator camera images during a complete GUI episode."""

import argparse
import gzip
import json
from pathlib import Path
from uuid import uuid4

from cellio.environment import data_root, lock_context
from cellio.io import CellIOError, digest, file_hash, write_json
from cellio.runtime.campaign import scenario
from cellio.runtime.episode import run_episode
from .application import standalone_main
from .plant import IsaacPlant
from .video import viewport_png


SAMPLE_STEPS = {"cell_a": (4, 1920, 2440, 3360), "cell_b": (4, 2700, 3736, 5520)}


class FigurePlant(IsaacPlant):
    def __init__(self, shots, frame_dir, **options):
        super().__init__(headless=False, **options)
        self.shots, self.frame_dir = shots, frame_dir

    def advance_one_step(self, render=False):
        receipt = super().advance_one_step(render)
        if self.step in self.shots:
            if not render:
                raise CellIOError("VIDEO_VIEWPORT", "Requested still must coincide with a GUI render")
            viewport_png(self.frame_dir / f"s{self.step:04d}.png", self.rendering.render)
            if self.manager.get_simulation_time() != receipt.engine_time_after_s:
                raise CellIOError("CLOCK_ADVANCE_MISMATCH", "Still capture advanced physics")
        return receipt


def metadata(cell, run_id, attempt, session):
    episode = session / "episode"
    index = {"cell": cell, "run_id": run_id, "attempt_id": attempt,
             "camera_path": "/World/OperatorCamera", "predeclared_steps": SAMPLE_STEPS[cell], "frames": []}
    stages = []
    with gzip.open(episode / "events.jsonl.gz", "rt", encoding="utf-8") as stream:
        for event in map(json.loads, stream):
            data = event['data']
            if data['event_type'] == 'controller.stage_enter':
                stages.append((event['step'], data['payload']['stage_id']))
    with gzip.open(episode / "trace.jsonl.gz", "rt", encoding="utf-8") as stream:
        for record in map(json.loads, stream):
            if record['record_type'] != 'tick' or record['step'] not in SAMPLE_STEPS[cell]:
                continue
            step = record['step']
            truth = record['truth']
            name = f"s{step:04d}.png"
            frame = {"step": step, "sim_time_s": step/120, "frame": name,
                     "sha256": file_hash(session / 'frames' / name), "trace_record_hash": record['record_hash'],
                     "episode_id": record['episode_id'], "stage": next(stage for k, stage in reversed(stages) if k<=step),
                     "part_m": truth['bodies']['part_000']['position_m'], "tcp_m": truth['bodies']['cell_tcp']['position_m'],
                     "active_contacts": truth['contacts']}
            if 'clamp' in truth['joints']:
                frame['clamp_position_m'] = truth['joints']['clamp']['position']
            index['frames'].append(frame)
    if [frame['step'] for frame in index['frames']] != list(SAMPLE_STEPS[cell]):
        raise CellIOError("VIDEO_CAPTURE_FAILED", "The complete trace does not cover every requested still")
    result = json.loads((episode / 'result.json').read_text(encoding='utf-8'))
    manifest = json.loads((episode / 'manifest.final.json').read_text(encoding='utf-8'))
    index.update(task_outcome=result['task_outcome'], compliance_verdict=result['compliance_verdict'],
                 result_sha256=file_hash(episode / 'result.json'), trace_sha256=file_hash(episode / 'trace.jsonl.gz'),
                 trace_chain_sha256=manifest['last_record_hash'], manifest_final_sha256=file_hash(episode / 'manifest.final.json'))
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cell', required=True, choices=SAMPLE_STEPS)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cell = args.cell
    cfg = scenario(cell, 'healthy', 0)
    state = {"x_offset_m": 0., "y_offset_m": 0., "yaw_rad": 0., "mass_kg": .05}
    context, identity = lock_context('isaac62_lockstep')
    run_id = digest([cfg, state, identity, SAMPLE_STEPS[cell], 'fixed-operator-stills'])[:24]
    attempt = str(uuid4())
    session = args.output / cell / run_id / attempt
    frames = session / 'frames'
    frames.mkdir(parents=True)
    plant = FigurePlant(SAMPLE_STEPS[cell], frames, asset_root=data_root() / 'assets',
                        robot_profile=data_root() / 'locks/robot_profile.lock.json')
    try:
        result = run_episode(cfg, session / 'episode', run_id=run_id, attempt_id=attempt,
                             initial_state=state, hardware_session=plant, plant_options={"headless": False}, **context)
        if result['completion_status'] != 'COMPLETED':
            raise CellIOError('VIDEO_CAPTURE_FAILED', str(result.get('reason')))
        index = metadata(cell, run_id, attempt, session)
        write_json(session / 'frames.json', index)
        print(session, flush=True)
        return 0 if index['task_outcome'] == 'SUCCESS' and index['compliance_verdict'] == 'PASS' else 5
    finally:
        plant.close()


if __name__ == '__main__':
    raise SystemExit(standalone_main(main))
