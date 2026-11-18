# Run Cell B

Use the Windows runtime, verified Franka cache and calibrated robot profile described in the [Cell A quickstart](quickstart_cell_a.md).

```powershell
python -m cellio validate --config examples/cell_b/healthy.yaml
python -m cellio run --config examples/cell_b/healthy.yaml --headless
```

The robot loads a cube into the fixture, retreats, closes the clamp, waits for the process dwell and unloads the part. The checker requires the two jaw contacts and the 120-step physical dwell. The result also records whether the final cube pose is stable.

Use `examples/cell_b/f4.yaml` to delay the clamp command by 24 physics steps. The intent, delivery and actuator readings in the trace distinguish the requested time from the applied time. From WSL2, activate `cellio-audit` and use the Linux path to the closed Windows attempt.

```sh
python -m cellio verify-artifact --artifact <mounted-attempt-directory>
python -m cellio evaluate --artifact <mounted-attempt-directory>
```
