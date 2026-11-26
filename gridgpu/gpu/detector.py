import subprocess


def get_gpu_info(simulated_vram_mb: int | None = None) -> dict:
    """
    Reads GPU stats via nvidia-smi.
    Simulation is opt-in. Without a usable device, advertise CPU-only.
    """
    if simulated_vram_mb is not None:
        if simulated_vram_mb <= 0:
            raise ValueError("Simulated VRAM must be positive")
        return {
            "gpu_name": f"Simulated {simulated_vram_mb} MiB GPU",
            "vram_total_mb": simulated_vram_mb,
            "vram_free_mb": simulated_vram_mb,
            "gpu_utilization": 0, "temperature_c": None, "mock": True,
        }
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, result.args)

        line  = result.stdout.strip().split("\n")[0]
        parts = [p.strip() for p in line.split(",")]

        return {
            "gpu_name":        parts[0],
            "vram_total_mb":   int(parts[1]),
            "vram_free_mb":    int(parts[2]),
            "gpu_utilization": int(parts[3]),
            "temperature_c":   int(parts[4]) if parts[4].isdigit() else None,
            "mock":            False,
        }

    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return {
            "gpu_name":        "CPU only (no usable NVIDIA device detected)",
            "vram_total_mb":   0,
            "vram_free_mb":    0,
            "gpu_utilization": 0,
            "temperature_c":   None,
            "mock":            False,
        }


def is_available(gpu: dict, required_vram_mb: int = 1000) -> bool:
    """
    A GPU is considered available when:
    - utilization is below 80%
    - enough free VRAM for the job
    """
    return (
        gpu["gpu_utilization"] < 80
        and gpu["vram_free_mb"] >= required_vram_mb
    )
