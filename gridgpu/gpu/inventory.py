"""Local hardware inspection: no backend calls and no implicit simulation."""
import csv
import importlib.util
import json
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys

import psutil


def command(args, timeout=10):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def gpu_inventory():
    output = command(["nvidia-smi", "--query-gpu=uuid,name,memory.total,memory.free,driver_version",
                      "--format=csv,noheader,nounits"])
    devices = []
    if output:
        for row in csv.reader(output.splitlines()):
            try:
                devices.append({"gpuId": row[0].strip(), "name": row[1].strip(),
                                "vramTotalMiB": int(row[2]), "vramFreeMiB": int(row[3]),
                                "driverVersion": row[4].strip(), "vendor": "NVIDIA"})
            except (ValueError, IndexError):
                continue
    if devices:
        return devices
    system = platform.system()
    names = []
    if system == "Windows":
        output = command(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"])
        names = output.splitlines()
    elif system == "Linux":
        for line in command(["lspci", "-mm"]).splitlines():
            parts = shlex.split(line)
            if len(parts) >= 4 and any(kind in parts[1] for kind in ("VGA", "3D", "Display")):
                names.append(" ".join(parts[2:4]))
    elif system == "Darwin":
        try:
            data = json.loads(command(["system_profiler", "SPDisplaysDataType", "-json"]))
            names = [item["sppci_model"] for item in data.get("SPDisplaysDataType", []) if "sppci_model" in item]
        except (ValueError, TypeError):
            pass
    return [{"gpuId": f"display-{index}", "name": name.strip(), "vramTotalMiB": None,
             "vramFreeMiB": None, "driverVersion": None, "vendor": "unknown"}
            for index, name in enumerate(names[:16]) if name.strip()]


def runtime_inventory():
    runtimes = []
    if importlib.util.find_spec("torch") is not None:
        probe = command([sys.executable, "-c",
            "import json, torch; print(json.dumps({'name':'pytorch', 'version':torch.__version__, "
            "'cudaAvailable':torch.cuda.is_available(), 'cudaRuntimeVersion':torch.version.cuda}))"], timeout=20)
        try:
            runtimes.append(json.loads(probe))
        except ValueError:
            runtimes.append({"name": "pytorch", "probeSucceeded": False})
    if shutil.which("llama-cli") or shutil.which("llama-server") or importlib.util.find_spec("llama_cpp") is not None:
        runtimes.append({"name": "llama.cpp", "installed": True})
    return runtimes


def suggested_tier(gpu, thresholds=(4096, 8192, 12288)):
    total = gpu.get("vramTotalMiB") or 0
    for tier, threshold in reversed(list(zip(("A", "B", "C"), thresholds))):
        if total >= threshold:
            return tier
    return None


def inspect_hardware(thresholds=(4096, 8192, 12288)):
    gpus = gpu_inventory()
    for gpu in gpus:
        gpu["suggestedTier"] = suggested_tier(gpu, thresholds)
    driver_output = command(["nvidia-smi"]) if any(g["vendor"] == "NVIDIA" for g in gpus) else ""
    match = re.search(r"CUDA Version:\s*([0-9.]+)", driver_output)
    return {
        "hostname": socket.gethostname(), "os": platform.system(), "osVersion": platform.release(),
        "architecture": platform.machine(), "cpu": platform.processor() or platform.machine(),
        "ramTotalMiB": psutil.virtual_memory().total // (1024 * 1024), "gpus": gpus,
        "runtimes": runtime_inventory(), "simulated": False,
        "driverCudaMaxVersion": match.group(1) if match else None,
    }
