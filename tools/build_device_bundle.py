"""Build a contributor-only archive without backend code or secrets."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "install.ps1", "install.sh", "pyproject.toml", "requirements.txt",
    "gridgpu/__init__.py", "gridgpu/__main__.py",
    "gridgpu/gpu/__init__.py", "gridgpu/gpu/inventory.py",
    "gridgpu/registration/__init__.py", "gridgpu/registration/client.py",
]
GUIDE = """# Install GridGPU on a contributing PC

Requirements: Windows or Linux, Python 3.11 or newer, and internet access for
downloading the Python dependencies. Install the NVIDIA driver to report NVIDIA
GPU memory accurately. Other GPUs may be detected by name with unknown VRAM.
This is a Python installer, not a standalone Windows executable.

Extract the ZIP and open a terminal inside the gridgpu-device folder.

Windows PowerShell, local installation and hardware detection:

    powershell -ExecutionPolicy Bypass -File .\install.ps1

Linux, local installation and hardware detection:

    bash install.sh

To register, obtain the deployed registration API URL from the GridGPU owner.
Then run setup with that real URL:

Windows:

    powershell -ExecutionPolicy Bypass -File .\install.ps1 -ApiUrl "YOUR_REAL_API_URL"

Linux:

    bash install.sh --api-url "YOUR_REAL_API_URL"

Confirm registration and enter your email. A token is emailed immediately.
Paste it into the agent to verify your email and submit the detected hardware.
Your device now waits for owner approval with no credential or heartbeats.
The owner reviews the submitted GPUs and approves or rejects the device.
The waiting agent checks approval using its private installation nonce.
Only after approval does it receive a credential and start heartbeats.
You can press Enter at the token prompt and submit it later:

Windows:

    .\.venv-agent\Scripts\python.exe -m gridgpu register

Linux:

    .venv-agent/bin/python -m gridgpu register

To resume waiting for approval or restart an approved device's heartbeats, use
the same Python command with 'run' instead of 'register'. Keep the terminal
running. The device ID and private nonce persist under the user's .gridgpu
directory; the device credential is saved there only after approval.
Rejected devices remain blocked and do not receive a credential.

The archive contains only contributor software. It contains no Firebase backend
code, administrator credentials, or server configuration. Email registration
requires the owner to deploy and configure the backend first.
"""


def main():
    output = ROOT / "dist" / "gridgpu-device-installer.zip"
    output.parent.mkdir(exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(ROOT / name, "gridgpu-device/" + name)
        archive.writestr("gridgpu-device/README.md", GUIDE)
    with ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Installer archive failed integrity verification")
    print(f"Created {output} ({output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
