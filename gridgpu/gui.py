"""Tkinter registration window for GridGPU.

Double-clicking the app opens this. It auto-detects the GPU (read-only),
collects name/email/availability/location and the backend URL, sends the
6-digit email code request, accepts the typed code, submits the device, then
polls for the owner's approval decision -- all without a terminal.
"""
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

from gridgpu.registration.client import DeviceClient

# Optional: bake in your deployed registration_api URL here so users don't type it.
# e.g. DEFAULT_API_URL = "https://europe-west1-yourproject.cloudfunctions.net/registration_api"
DEFAULT_API_URL = ""


def _hardware_summary(hardware):
    lines = [f"{hardware['os']}  |  {hardware['cpu']}  |  {hardware['ramTotalMiB']} MiB RAM"]
    for gpu in hardware["gpus"]:
        total = gpu["vramTotalMiB"]
        memory = f"{total} MiB VRAM" if total is not None else "VRAM unknown"
        tier = gpu["suggestedTier"] or "unclassified"
        lines.append(f"{gpu['name']}  ({memory}, tier {tier})")
    if not hardware["gpus"]:
        lines.append("No GPU detected -- this machine will not claim GPU capacity.")
    return "\n".join(lines)


class RegistrationWindow:
    def __init__(self, state_dir=None):
        self.state_dir = Path(state_dir) if state_dir else Path.home() / ".gridgpu"
        self.client = None
        self.hardware = None
        self._results = queue.Queue()

        self.root = tk.Tk()
        self.root.title("GPUHive - Share your GPU")
        self.root.geometry("520x560")
        self.root.resizable(False, False)

        self._build_form()
        self._detect_hardware()
        self.root.after(100, self._drain_results)

    # ---------- layout ----------
    def _build_form(self):
        pad = {"padx": 16, "pady": 4}
        frm = ttk.Frame(self.root, padding=16)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Join GPUHive", font=("", 16, "bold")).pack(anchor="w")
        ttk.Label(frm, text="Share this computer's GPU. Your hardware is detected automatically.",
                  wraplength=480, foreground="#555").pack(anchor="w", pady=(0, 8))

        box = ttk.LabelFrame(frm, text="Detected hardware", padding=10)
        box.pack(fill="x", pady=6)
        self.hw_label = ttk.Label(box, text="Detecting...", justify="left", wraplength=460)
        self.hw_label.pack(anchor="w")

        self.vars = {k: tk.StringVar() for k in ("url", "name", "email", "availability", "location")}
        self.vars["url"].set(DEFAULT_API_URL)

        fields = [
            ("Backend URL", "url", "https://...cloudfunctions.net/registration_api"),
            ("Your name", "name", ""),
            ("Email address", "email", ""),
            ("Availability (optional)", "availability", "e.g. nights/weekends"),
            ("Location (optional)", "location", "e.g. DE"),
        ]
        for label, key, hint in fields:
            ttk.Label(frm, text=label).pack(anchor="w", **pad)
            entry = ttk.Entry(frm, textvariable=self.vars[key], width=56)
            entry.pack(anchor="w", padx=16)
            if hint:
                ttk.Label(frm, text=hint, foreground="#888", font=("", 8)).pack(anchor="w", padx=16)

        self.register_btn = ttk.Button(frm, text="Register", command=self._on_register)
        self.register_btn.pack(pady=14)

        self.status = ttk.Label(frm, text="", wraplength=480, foreground="#06c")
        self.status.pack(anchor="w")

    # ---------- background helpers ----------
    def _run_async(self, fn, on_done):
        def worker():
            try:
                self._results.put((on_done, fn(), None))
            except Exception as error:  # surfaced on the UI thread
                self._results.put((on_done, None, error))
        threading.Thread(target=worker, daemon=True).start()

    def _drain_results(self):
        try:
            while True:
                on_done, value, error = self._results.get_nowait()
                on_done(value, error)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_results)

    def _set_status(self, text, color="#06c"):
        self.status.config(text=text, foreground=color)

    # ---------- flow ----------
    def _detect_hardware(self):
        def build():
            client = DeviceClient(self.state_dir, None)
            return client, client.inspect()

        def done(value, error):
            if error:
                self.hw_label.config(text=f"Hardware detection failed: {error}")
                return
            self.client, self.hardware = value
            self.hw_label.config(text=_hardware_summary(self.hardware))

        self._run_async(build, done)

    def _on_register(self):
        url = self.vars["url"].get().strip()
        name = self.vars["name"].get().strip()
        if not url:
            messagebox.showwarning("Backend URL required", "Enter your GPUHive backend URL.")
            return
        if not name:
            messagebox.showwarning("Missing details", "Name is required.")
            return

        self.register_btn.config(state="disabled")
        self._set_status("Registering this GPU...")

        def enroll():
            client = DeviceClient(self.state_dir, url)
            client.enroll(name,
                          self.vars["email"].get().strip(),
                          self.vars["availability"].get().strip(),
                          self.vars["location"].get().strip())
            return client

        def done(client, error):
            if error:
                self.register_btn.config(state="normal")
                self._set_status(f"Registration failed: {error}", "#c00")
                return
            self.client = client
            self._set_status("Registered! This GPU is active and sending heartbeats. "
                             "Keep this window open while sharing.", "#060")
            self._start_heartbeat()

        self._run_async(enroll, done)

    def _start_heartbeat(self):
        def loop():
            try:
                self.client.run()
            except Exception:
                pass
        threading.Thread(target=loop, daemon=True).start()

    def run(self):
        self.root.mainloop()


def main(state_dir=None):
    RegistrationWindow(state_dir).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
