import json
import os
from pathlib import Path
import random
import secrets
import tempfile
import time
from urllib.parse import urlsplit
import uuid

import requests

from gridgpu.gpu.inventory import gpu_inventory, inspect_hardware


class RegistrationError(Exception):
    def __init__(self, message, status=None):
        self.status = status
        super().__init__(message)


def private_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".gridgpu-")
    try:
        with os.fdopen(handle, "w") as stream:
            json.dump(data, stream, indent=2)
        os.replace(temp, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class DeviceClient:
    def __init__(self, state_dir, api_url=None, allow_local_http=False, session=None):
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / "registration.json"
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {
            "deviceId": str(uuid.uuid4()), "registrationNonce": secrets.token_urlsafe(32),
        }
        previous_url = self.state.get("apiUrl")
        api_url = api_url or previous_url
        if api_url:
            parsed = urlsplit(api_url)
            local = parsed.hostname in ("127.0.0.1", "localhost", "::1")
            if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or (parsed.scheme != "https" and not (allow_local_http and parsed.scheme == "http" and local))):
                raise RegistrationError("Use HTTPS for the backend; local HTTP requires --allow-local-http")
            parsed.port
            api_url = api_url.rstrip("/")
            if (self.state.get("credential") or self.state.get("registrationStatus")) and previous_url != api_url:
                raise RegistrationError("A registered device cannot change its backend URL")
            self.state["apiUrl"] = api_url
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.save()

    def save(self):
        private_json(self.path, self.state)

    def call(self, path, method="POST", data=None, credential=None):
        if not self.state.get("apiUrl"):
            raise RegistrationError("Provide the registration backend URL")
        headers = {"Authorization": "Bearer " + credential} if credential else {}
        try:
            response = self.session.request(method, self.state["apiUrl"] + path, json=data, headers=headers,
                                            timeout=30, allow_redirects=False)
        except requests.RequestException:
            raise RegistrationError("Cannot reach the registration backend") from None
        try:
            value = response.json()
        except ValueError:
            raise RegistrationError("Backend returned an invalid response", response.status_code) from None
        if not 200 <= response.status_code < 300:
            raise RegistrationError(value.get("error", "Backend request failed"), response.status_code)
        return value

    def inspect(self):
        hardware = inspect_hardware()
        private_json(self.state_dir / "hardware.json", hardware)
        return hardware

    def request_registration(self, email, name, availability="", location=""):
        if self.state.get("credential") or self.state.get("registrationStatus"):
            raise RegistrationError("This installation has already submitted a device")
        value = self.call("/requests", data={"email": email, "name": name,
                                             "availability": availability, "location": location})
        if value.get("status") != "token_sent":
            raise RegistrationError("Backend returned an invalid email-token response")
        self.state.update(emailStatus="token_sent", tokenExpiresAt=value["expiresAt"])
        self.save()
        return value

    def register(self, token):
        if self.state.get("registrationStatus") or self.state.get("credential"):
            return {"deviceId": self.state["deviceId"],
                    "status": self.state.get("registrationStatus", "approved")}
        value = self.call("/devices/register", data={
            "token": token.strip(), "deviceId": self.state["deviceId"],
            "registrationNonce": self.state["registrationNonce"], "hardware": self.inspect(),
        })
        if (value.get("deviceId") != self.state["deviceId"]
                or value.get("status") not in ("pending_approval", "approved", "rejected")
                or "credential" in value):
            raise RegistrationError("Backend returned an invalid pending-device response")
        interval = self._interval(value.get("approvalPollIntervalSeconds"), 5, 300)
        self.state.update(registrationStatus=value["status"], approvalPollIntervalSeconds=interval)
        self.save()
        return {"deviceId": value["deviceId"], "status": value["status"]}

    @staticmethod
    def _interval(value, minimum, maximum):
        if type(value) is not int or not minimum <= value <= maximum:
            raise RegistrationError("Invalid interval from backend", 400)
        return value

    def poll_approval(self):
        value = self.call(f"/devices/{self.state['deviceId']}/approval", method="GET",
                          credential=self.state["registrationNonce"])
        if (value.get("deviceId") != self.state["deviceId"]
                or value.get("status") not in ("pending_approval", "approved", "rejected")):
            raise RegistrationError("Invalid device approval response", 400)
        interval = self._interval(value.get("approvalPollIntervalSeconds"), 5, 300)
        if value["status"] == "approved":
            if not value.get("enabled"):
                raise RegistrationError("Device is disabled", 403)
            credential = value.get("credential")
            if not isinstance(credential, str) or not 32 <= len(credential) <= 256:
                raise RegistrationError("Backend returned invalid device credentials", 400)
            heartbeat_interval = self._interval(value.get("heartbeatIntervalSeconds"), 10, 3600)
            self.state.update(credential=credential, heartbeatIntervalSeconds=heartbeat_interval)
        else:
            if "credential" in value:
                raise RegistrationError("Unapproved device received a credential", 400)
            self.state.pop("credential", None)
        self.state.update(registrationStatus=value["status"], approvalPollIntervalSeconds=interval)
        self.save()
        # Never print or return the usable credential to the CLI.
        return {key: item for key, item in value.items() if key != "credential"}

    def wait_for_approval(self, once=False):
        while True:
            try:
                value = self.poll_approval()
                if value["status"] == "approved":
                    print("Device approved. Credential saved; starting heartbeats.")
                    return True
                if value["status"] == "rejected":
                    raise RegistrationError("The owner rejected this device", 403)
                print("Device submitted; awaiting owner approval.")
                interval = self.state["approvalPollIntervalSeconds"]
            except RegistrationError as error:
                if once or error.status in (400, 401, 403, 404, 409):
                    raise
                interval = self.state.get("approvalPollIntervalSeconds", 30)
                print(f"{error}; will check approval again shortly")
            if once:
                return False
            time.sleep(interval * random.uniform(0.85, 1.15))

    def heartbeat(self):
        credential = self.state.get("credential")
        if not credential:
            raise RegistrationError("Device approval and a credential are required before heartbeats")
        return self.call(f"/devices/{self.state['deviceId']}/heartbeat",
                         data={"telemetry": {"gpus": gpu_inventory(), "busy": False}}, credential=credential)

    def run(self, once=False):
        if not self.state.get("credential"):
            if not self.state.get("registrationStatus"):
                raise RegistrationError("Verify the email token and submit this device first")
            if not self.wait_for_approval(once):
                return
        failures = 0
        while True:
            try:
                value = self.heartbeat()
                interval = value["heartbeatIntervalSeconds"]
                if type(interval) is not int or not 10 <= interval <= 3600:
                    raise RegistrationError("Invalid heartbeat interval from backend")
                failures = 0
                print(f"Heartbeat accepted at {value['lastSeenAt']}")
            except RegistrationError as error:
                if once or error.status in (401, 403) or not self.state.get("credential"):
                    raise
                failures += 1
                interval = min(300, 10 * 2 ** min(failures, 5))
                print(f"{error}; retrying in approximately {interval} seconds")
            if once:
                return
            time.sleep(interval * random.uniform(0.85, 1.15))
