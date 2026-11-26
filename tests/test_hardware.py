import subprocess

from gridgpu.gpu import detector, inventory
from functions.registration.service import Settings


def test_detects_all_nvidia_gpus_and_vram_without_summing(monkeypatch):
    def command(args, timeout=10):
        if "--format=csv,noheader,nounits" in args:
            return "GPU-a, GTX 1660, 6144, 5000, 560.1\nGPU-b, RTX 4060, 8192, 7000, 560.1"
        return "Driver Version: 560.1   CUDA Version: 12.6"
    monkeypatch.setattr(inventory, "command", command)
    monkeypatch.setattr(inventory, "runtime_inventory", lambda: [])
    report = inventory.inspect_hardware()
    assert [g["vramTotalMiB"] for g in report["gpus"]] == [6144, 8192]
    assert [g["suggestedTier"] for g in report["gpus"]] == ["A", "B"]
    assert report["driverCudaMaxVersion"] == "12.6"
    assert Settings().tier(report) == "B"  # 6+8 does not make a 14 GiB GPU.
    assert report["simulated"] is False


def test_missing_driver_does_not_advertise_mock_gpu(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("No driver")
    monkeypatch.setattr(subprocess, "run", missing)
    assert detector.get_gpu_info()["vram_total_mb"] == 0
    assert detector.get_gpu_info()["mock"] is False
    assert detector.get_gpu_info(4096)["mock"] is True


def test_failed_driver_is_cpu_only(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 1, stdout="", stderr="driver unavailable"))
    assert detector.get_gpu_info()["vram_total_mb"] == 0


def test_windows_fallback_does_not_invent_vram(monkeypatch):
    monkeypatch.setattr(inventory.platform, "system", lambda: "Windows")
    monkeypatch.setattr(inventory, "command", lambda args, timeout=10:
                        "AMD Radeon RX 6600" if args[0] == "powershell" else "")
    gpu = inventory.gpu_inventory()[0]
    assert gpu["name"] == "AMD Radeon RX 6600"
    assert gpu["vramTotalMiB"] is None
    assert inventory.suggested_tier(gpu) is None
