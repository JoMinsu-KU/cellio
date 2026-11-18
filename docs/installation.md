# Workstation installation

Clone this repository and run the following commands from its root. Use the NVMe disk for the Windows environment, Kit cache and active experiment output.

```powershell
conda create -n cellio-isaac62 -c conda-forge python=3.12.16 pip
conda activate cellio-isaac62
python -m pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cu130
python -m pip install "isaacsim[all,extscache]==6.2.0.0" --extra-index-url https://pypi.nvidia.com
python -m pip install -e ".[dev,plots]"
```

The first Kit launch requests the NVIDIA Omniverse license agreement. See the [NVIDIA 6.2 Python installation guide](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/install_python.html).

In WSL2, create a separate Linux environment and install CellIO from the same source revision.

```sh
conda create -n cellio-audit -c conda-forge python=3.12.16 pip
conda activate cellio-audit
python -m pip install -e '.[dev,plots]'
python -m pytest
```

Windows owns the simulator and its worker processes. Offline evaluation reads closed artifacts after checking their completion marker and digest. Each platform records its own interpreter and dependencies. The [Windows inventory](../locks/windows-isaac62/python-environment.json) and [WSL2 inventory](../locks/wsl-audit/python-environment.json) contain the measured working versions. To reproduce the Windows build in a clean environment and collect its ten nominal trials, use the [clean-install phase](run_campaigns.md).
