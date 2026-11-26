"""Prove the registration flow works end to end, locally, with no Firebase and no real email.

Runs the REAL service + REAL Flask routes against an in-memory store and a fake
mailer that just captures the 6-digit code (like an inbox would). Then it walks the
whole flow and shows what happens with a WRONG code versus the CORRECT code.

    python tools/try_it_live.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "functions"))

from registration.api import create_app
from registration.service import RegistrationService
from registration.store import MemoryStore

from gridgpu.gpu.inventory import inspect_hardware


class FakeInbox:
    """Stands in for the email provider: remembers the last code 'delivered'."""
    def __init__(self):
        self.code = None

    def send_token(self, recipient, token, expires_at):
        print(f"   [inbox] 6-digit code emailed to {recipient} (expires {expires_at.isoformat()})")
        self.code = token


def line(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main():
    admin_secret = "x" * 40
    issuer_secret = "y" * 40
    inbox = FakeInbox()
    service = RegistrationService(MemoryStore(), inbox, issuer_secret)
    client = create_app(service, admin_secret).test_client()

    # What the installer wizard would detect on THIS machine right now.
    hardware = inspect_hardware()
    gpu_names = [g.get("name") for g in hardware.get("gpus", [])] or ["(no GPU detected)"]
    device_id = "11111111-1111-1111-1111-111111111111"
    nonce = "n" * 40

    line("STEP 1  Donor fills in the wizard -> backend emails a 6-digit code")
    r = client.post("/requests", json={
        "email": "donor@example.com", "name": "Jordan Doe",
        "availability": "nights/weekends", "location": "DE",
    })
    print(f"   backend status {r.status_code}: {r.get_json()}")
    real_code = inbox.code
    print(f"   (the code the donor would read from email: {real_code})")

    line("STEP 2a  Donor types a WRONG code + wizard sends the GPU details")
    print(f"   GPU being offered: {gpu_names}")
    r = client.post("/devices/register", json={
        "token": "000000" if real_code != "000000" else "111111",
        "deviceId": device_id, "registrationNonce": nonce, "hardware": hardware,
    })
    print(f"   backend status {r.status_code}: {r.get_json()}")
    pending = service.devices(status="pending_approval")
    print(f"   devices now waiting for YOUR approval: {len(pending)}")
    assert r.status_code == 401 and len(pending) == 0
    print("   => WRONG code rejected. The GPU was NOT added. (good)")

    line("STEP 2b  Donor types the CORRECT code + wizard sends the GPU details")
    r = client.post("/devices/register", json={
        "token": real_code,
        "deviceId": device_id, "registrationNonce": nonce, "hardware": hardware,
    })
    print(f"   backend status {r.status_code}: {r.get_json()}")
    pending = service.devices(status="pending_approval")
    print(f"   devices now waiting for YOUR approval: {len(pending)}")
    assert r.status_code == 201 and len(pending) == 1
    d = pending[0]
    print("   => CORRECT code accepted. Entry submitted to you:")
    print(f"      name          : {d['name']}")
    print(f"      verified email: {d['email']}")
    print(f"      availability  : {d['availability']}")
    print(f"      location      : {d['location']}")
    print(f"      GPUs          : {[g.get('name') for g in d['hardware']['gpus']]}")
    print(f"      suggested tier: {d['suggestedTier']}")
    print(f"      status        : {d['status']}  (waiting for you to approve)")
    assert d["name"] == "Jordan Doe" and d["availability"] == "nights/weekends" and d["location"] == "DE"

    line("STEP 3  You (the owner) approve it in Firebase/admin")
    r = client.post(f"/admin/devices/{device_id}/approve",
                    headers={"Authorization": "Bearer " + admin_secret})
    print(f"   backend status {r.status_code}: {r.get_json()}")
    assert r.status_code == 200

    line("STEP 4  The wizard polls and receives its working credential")
    r = client.get(f"/devices/{device_id}/approval",
                   headers={"Authorization": "Bearer " + nonce})
    body = r.get_json()
    got_credential = "credential" in body
    print(f"   backend status {r.status_code}: status={body.get('status')} "
          f"enabled={body.get('enabled')} credential_delivered={got_credential}")
    assert r.status_code == 200 and got_credential
    print("   => Device is live and can start heartbeating.")

    line("RESULT")
    print("   Wrong code  -> GPU NOT registered.")
    print("   Right code  -> name/email/availability/location + GPU registered to you,")
    print("                  pending your approval, then live after you approve.")
    print("   The flow works end to end on this machine, no Firebase needed.")


if __name__ == "__main__":
    main()
