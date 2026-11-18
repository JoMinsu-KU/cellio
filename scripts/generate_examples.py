"""Materialize both cells' versioned scenario files for distribution."""

from pathlib import Path
import yaml
from cellio.runtime.campaign import ARMS, scenario


def main():
    root = Path(__file__).resolve().parents[1]
    for cell in ("cell_a", "cell_b"):
        folder = root / "examples" / cell
        folder.mkdir(parents=True, exist_ok=True)
        for arm in ARMS:
            (folder / f"{arm}.yaml").write_text(
                yaml.safe_dump(scenario(cell, arm, seed=0), sort_keys=False),
                encoding="utf-8",
                newline="\n",
            )
    mock = scenario("cell_a", profile="mock_lockstep")
    mock["episode"].update(horizon_steps=36, warmup_steps=0)
    (root / "examples" / "mock_smoke.yaml").write_text(
        yaml.safe_dump(mock, sort_keys=False), encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
