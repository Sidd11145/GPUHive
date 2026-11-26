from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import uuid

import pytest

from functions.registration.api import create_app
from functions.registration.service import ApiError, RegistrationService, hash_secret
from functions.registration.store import MemoryStore
from gridgpu.registration.client import DeviceClient, RegistrationError

ADMIN = "test-admin-secret-" * 3
ISSUER = "test-issuer-secret-" * 3
NONCE = "private-installation-nonce-" * 2
HARDWARE = {"gpus": [{"gpuId": "GPU-test", "name": "RTX 2060", "vramTotalMiB": 6144,
                      "vramFreeMiB": 5000}], "os": "Windows", "simulated": False}


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 24, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class Mailer:
    def __init__(self):
        self.sent = []
        self.fail = False

    def send_token(self, email, token, expires):
        if self.fail:
            raise RuntimeError("Simulated mail failure")
        self.sent.append((email, token, expires))


@pytest.fixture
def env():
    store, mailer, clock = MemoryStore(), Mailer(), Clock()
    service = RegistrationService(store, mailer, ISSUER, clock=clock)
    return service, create_app(service, ADMIN).test_client(), mailer, store, clock


def emailed_token(env, email="participant@example.com"):
    service, _, mailer, _, _ = env
    service.request(email, "Jordan Doe", "nights/weekends", "DE")
    return mailer.sent[-1][1]


def enroll(service, token, device_id=None, nonce=NONCE, hardware=HARDWARE):
    return service.register(token, device_id or str(uuid.uuid4()), nonce, hardware)


def approved_device(env):
    service = env[0]
    device = enroll(service, emailed_token(env))
    service.review(device["deviceId"], True)
    return service.approval_status(device["deviceId"], NONCE)


def test_email_immediately_sends_token_without_admin_or_device_record(env):
    _, api, mailer, store, clock = env
    response = api.post("/requests", json={"email": "participant@example.com",
                        "name": "Jordan Doe", "availability": "nights", "location": "DE"})
    assert response.status_code == 202 and response.json["status"] == "token_sent"
    assert len(mailer.sent) == 1 and mailer.sent[0][0] == "participant@example.com"
    token = mailer.sent[0][1]
    assert token not in response.get_data(as_text=True)
    assert store.list("devices") == [] and store.list("registrationRequests") == []
    record = store.records[("registrationTokens", hash_secret(token))]
    assert record["emailStatus"] == "sent" and record["usedAt"] is None
    assert record["expiresAt"] == clock.now + timedelta(minutes=15)


def test_request_rate_limit_prevents_repeat_email(env):
    service, _, mailer, _, clock = env
    service.request("participant@example.com", "Jordan Doe")
    with pytest.raises(ApiError) as error:
        service.request("participant@example.com", "Jordan Doe")
    assert error.value.status == 429 and len(mailer.sent) == 1
    clock.advance(60)
    service.request("participant@example.com", "Jordan Doe")
    assert len(mailer.sent) == 2


def test_token_verification_creates_pending_device_with_no_credential(env):
    service, api, _, store, clock = env
    token, device_id = emailed_token(env), str(uuid.uuid4())
    response = api.post("/devices/register", json={"token": token, "deviceId": device_id,
                        "registrationNonce": NONCE, "hardware": HARDWARE})
    assert response.status_code == 201 and response.json["status"] == "pending_approval"
    assert "credential" not in response.json
    record = store.records[("devices", device_id)]
    assert record["credentialHash"] is None and record["credentialSalt"] is None
    assert record["enabled"] is False and record["lastSeenAt"] is None
    assert record["email"] == "participant@example.com" and record["emailVerifiedAt"] == clock.now
    assert record["name"] == "Jordan Doe" and record["availability"] == "nights/weekends" and record["location"] == "DE"
    assert record["hardware"] == HARDWARE and record["suggestedTier"] == "A"
    assert record["hardwareVerification"] == "self_reported"
    assert service.approval_status(device_id, NONCE)["status"] == "pending_approval"
    assert "credential" not in service.approval_status(device_id, NONCE)
    heartbeat = api.post(f"/devices/{device_id}/heartbeat", json={"telemetry": {}},
                         headers={"Authorization": "Bearer " + NONCE})
    assert heartbeat.status_code == 401
    with pytest.raises(ApiError):
        service.set_enabled(device_id, True)


def test_admin_reviews_device_hardware_and_approval_sends_no_email(env):
    service, api, mailer, _, _ = env
    device = enroll(service, emailed_token(env))
    device_id = device["deviceId"]
    assert api.get("/admin/devices?status=pending_approval").status_code == 401
    assert api.post(f"/admin/devices/{device_id}/approve", headers={"Authorization": "Bearer wrong"}).status_code == 401
    headers = {"Authorization": "Bearer " + ADMIN}
    queue = api.get("/admin/devices?status=pending_approval", headers=headers).json
    assert queue[0]["hardware"]["gpus"][0]["name"] == "RTX 2060"
    assert queue[0]["suggestedTier"] == "A"
    assert not {"credentialHash", "credentialSalt", "registrationNonceHash"} & queue[0].keys()
    response = api.post(f"/admin/devices/{device_id}/approve", headers=headers)
    assert response.status_code == 200 and response.json["status"] == "approved"
    assert "credential" not in response.json and len(mailer.sent) == 1
    assert ADMIN not in api.get("/admin").get_data(as_text=True)
    assert api.post(f"/admin/requests/{device_id}/approve", headers=headers).status_code == 404


def test_approval_creates_hash_and_nonce_poll_releases_stable_credential(env):
    service, api, _, store, _ = env
    device = enroll(service, emailed_token(env))
    device_id = device["deviceId"]
    headers = {"Authorization": "Bearer " + NONCE}
    path = f"/devices/{device_id}/approval"
    assert "credential" not in api.get(path, headers=headers).json
    service.review(device_id, True)
    response = api.get(path, headers=headers)
    credential = response.json["credential"]
    assert response.headers["Cache-Control"] == "no-store"
    assert store.records[("devices", device_id)]["credentialHash"] == hash_secret(credential)
    service.review(device_id, True)
    assert api.get(path, headers=headers).json["credential"] == credential
    assert service.devices("pending_approval") == []


def test_poll_requires_correct_nonce_for_that_device(env):
    service, api, _, _, _ = env
    device = approved_device(env)
    path = f"/devices/{device['deviceId']}/approval"
    assert api.get(path).status_code == 401
    assert api.get(path, headers={"Authorization": "Bearer " + "wrong-nonce-" * 4}).status_code == 401
    assert api.get(path, headers={"Authorization": "Bearer " + device["credential"]}).status_code == 401
    another = enroll(service, emailed_token(env, "another@example.com"), nonce="another-nonce-" * 4)
    assert api.get(f"/devices/{another['deviceId']}/approval", headers={"Authorization": "Bearer " + NONCE}).status_code == 401


def test_rejection_keeps_device_blocked_and_never_issues_credential(env):
    service, api, mailer, store, _ = env
    device = enroll(service, emailed_token(env))
    service.review(device["deviceId"], False)
    service.review(device["deviceId"], False)
    assert len(mailer.sent) == 1
    result = service.approval_status(device["deviceId"], NONCE)
    assert result["status"] == "rejected" and "credential" not in result
    assert store.records[("devices", device["deviceId"])]["credentialHash"] is None
    assert api.post(f"/devices/{device['deviceId']}/heartbeat", json={"telemetry": {}},
                    headers={"Authorization": "Bearer " + NONCE}).status_code == 401
    with pytest.raises(ApiError, match="already been reviewed"):
        service.review(device["deviceId"], True)
    with pytest.raises(ApiError):
        service.set_enabled(device["deviceId"], True)


def test_tokens_nonces_and_credentials_are_only_hashed_on_server(env):
    service, _, _, store, _ = env
    token = emailed_token(env)
    device = enroll(service, token)
    service.review(device["deviceId"], True)
    credential = service.approval_status(device["deviceId"], NONCE)["credential"]
    persisted = json.dumps(list(store.records.values()), default=str)
    assert token not in persisted and credential not in persisted and NONCE not in persisted
    assert store.records[("registrationTokens", hash_secret(token))]["usedAt"] is not None


def test_token_expiry_prevents_submission(env):
    service, _, _, store, clock = env
    token = emailed_token(env)
    clock.advance(900)
    with pytest.raises(ApiError, match="expired"):
        enroll(service, token)
    assert store.list("devices") == []
    assert store.records[("registrationTokens", hash_secret(token))]["usedAt"] is None


def test_concurrent_token_use_creates_exactly_one_pending_device(env):
    service, _, _, store, _ = env
    token = emailed_token(env)
    def attempt(_):
        try:
            return enroll(service, token)
        except ApiError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(attempt, range(8)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count(409) == 7 and len(store.list("devices")) == 1
    assert store.list("devices")[0][1]["credentialHash"] is None


def test_lost_submission_response_recovers_without_releasing_credential(env):
    service, _, _, store, clock = env
    token, device_id = emailed_token(env), str(uuid.uuid4())
    first = enroll(service, token, device_id)
    clock.advance(901)
    assert enroll(service, token, device_id) == first
    service.review(device_id, True)
    replay = enroll(service, token, device_id)
    assert replay["status"] == "approved" and "credential" not in replay
    assert len(store.list("devices")) == 1
    with pytest.raises(ApiError, match="already been used"):
        enroll(service, token, device_id, "different-private-nonce-" * 2)


def test_concurrent_approve_reject_has_one_final_decision(env):
    service, _, _, store, _ = env
    device = enroll(service, emailed_token(env))
    def review(approve):
        try:
            return service.review(device["deviceId"], approve)
        except ApiError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(review, [True, False]))
    assert sum(isinstance(result, dict) for result in results) == 1 and results.count(409) == 1
    record = store.records[("devices", device["deviceId"])]
    assert (record["credentialHash"] is not None) == (record["status"] == "approved")


def test_email_failure_revokes_token_and_permits_immediate_retry(env):
    service, _, mailer, store, _ = env
    mailer.fail = True
    with pytest.raises(ApiError, match="Email delivery failed"):
        service.request("participant@example.com", "Jordan Doe")
    assert all(doc["revoked"] for _, doc in store.list("registrationTokens"))
    assert store.list("devices") == []
    mailer.fail = False
    service.request("participant@example.com", "Jordan Doe")
    assert len(mailer.sent) == 1
    assert enroll(service, mailer.sent[0][1])["status"] == "pending_approval"


def test_approved_heartbeat_checks_credential_enabled_and_server_time(env):
    service, api, _, store, clock = env
    device = approved_device(env)
    route = f"/devices/{device['deviceId']}/heartbeat"
    auth = {"Authorization": "Bearer " + device["credential"]}
    assert api.post(route, json={"telemetry": {}}, headers={"Authorization": "Bearer incorrect"}).status_code == 401
    assert store.records[("devices", device["deviceId"])]["lastSeenAt"] is None
    assert api.post(route, json={"telemetry": {"lastSeenAt": "2099-01-01"}}, headers=auth).status_code == 400
    response = api.post(route, json={"telemetry": {"busy": False}}, headers=auth)
    assert response.status_code == 200 and response.json["lastSeenAt"] == clock.now.isoformat()
    assert service.devices()[0]["online"] is True
    clock.advance(900)
    assert service.devices()[0]["online"] is False
    service.set_enabled(device["deviceId"], False)
    assert api.post(route, json={"telemetry": {}}, headers=auth).status_code == 403
    assert "credential" not in service.approval_status(device["deviceId"], NONCE)
    service.set_enabled(device["deviceId"], True)
    assert api.post(route, json={"telemetry": {}}, headers=auth).status_code == 200


def test_service_restart_recovers_approval_credential(env):
    service, _, mailer, store, clock = env
    device = approved_device(env)
    restarted = RegistrationService(store, mailer, ISSUER, clock=clock)
    assert restarted.approval_status(device["deviceId"], NONCE)["credential"] == device["credential"]


def test_malformed_requests_do_not_consume_token(env):
    service, api, _, store, _ = env
    assert api.post("/requests", json={"email": "bad\naddress@example.com",
                    "name": "Jordan", "availability": "", "location": ""}).status_code == 400
    token = emailed_token(env)
    response = api.post("/devices/register", json={"token": token, "deviceId": str(uuid.uuid4()),
        "registrationNonce": NONCE, "hardware": {"gpus": [{"name": "fake", "vramTotalMiB": -1}]}})
    assert response.status_code == 400
    assert store.records[("registrationTokens", hash_secret(token))]["usedAt"] is None
    assert api.post("/requests", json={"email": "x" * 70000,
                    "name": "Jordan", "availability": "", "location": ""}).status_code == 413


class FlaskSession:
    def __init__(self, api):
        self.api, self.calls = api, []

    def request(self, method, url, json=None, headers=None, **kwargs):
        from urllib.parse import urlsplit
        path = urlsplit(url).path
        self.calls.append((method, path))
        response = self.api.open(path, method=method, json=json, headers=headers)
        class Adapter:
            status_code = response.status_code
            def json(self):
                return response.get_json()
        return Adapter()


def client(env, tmp_path, monkeypatch):
    monkeypatch.setattr("gridgpu.registration.client.inspect_hardware", lambda: HARDWARE)
    monkeypatch.setattr("gridgpu.registration.client.gpu_inventory", lambda: HARDWARE["gpus"])
    return DeviceClient(tmp_path, "https://backend.example", session=FlaskSession(env[1]))


def submit_client(env, device):
    device.request_registration("participant@example.com", "Jordan Doe", "nights", "DE")
    return device.register(env[2].sent[0][1])


def test_agent_pending_restart_approval_and_heartbeat(env, tmp_path, monkeypatch, capsys):
    service, api, mailer, store, _ = env
    device = client(env, tmp_path, monkeypatch)
    pending = submit_client(env, device)
    assert pending["status"] == "pending_approval" and len(mailer.sent) == 1
    assert "credential" not in device.state
    with pytest.raises(RegistrationError, match="approval"):
        device.heartbeat()
    device.run(once=True)
    assert not any(path.endswith("/heartbeat") for _, path in device.session.calls)
    restarted = DeviceClient(tmp_path, session=FlaskSession(api))
    assert restarted.state["registrationNonce"] == device.state["registrationNonce"]
    assert restarted.state["registrationStatus"] == "pending_approval"
    service.review(device.state["deviceId"], True)
    restarted.run(once=True)
    assert restarted.state["credential"] and restarted.state["registrationStatus"] == "approved"
    assert store.records[("devices", device.state["deviceId"])]["lastSeenAt"] is not None
    output = capsys.readouterr().out
    assert restarted.state["credential"] not in output and mailer.sent[0][1] not in output
    assert (tmp_path / "registration.json").stat().st_mode & 0o077 == 0
    assert len(store.list("devices")) == 1


def test_agent_waits_until_approval_before_saving_credential(env, tmp_path, monkeypatch):
    service, _, _, store, _ = env
    device = client(env, tmp_path, monkeypatch)
    submit_client(env, device)
    waits = []
    def owner_approves(seconds):
        waits.append(seconds)
        assert "credential" not in device.state
        assert store.records[("devices", device.state["deviceId"])]["lastSeenAt"] is None
        service.review(device.state["deviceId"], True)
    monkeypatch.setattr("gridgpu.registration.client.time.sleep", owner_approves)
    assert device.wait_for_approval() is True and len(waits) == 1
    assert 25.5 <= waits[0] <= 34.5
    assert "credential" in device.state


def test_rejected_agent_stops_without_heartbeats(env, tmp_path, monkeypatch):
    device = client(env, tmp_path, monkeypatch)
    submit_client(env, device)
    env[0].review(device.state["deviceId"], False)
    with pytest.raises(RegistrationError, match="rejected"):
        device.run(once=True)
    assert "credential" not in device.state and device.state["registrationStatus"] == "rejected"
    assert not any(path.endswith("/heartbeat") for _, path in device.session.calls)


def test_agent_recovers_credential_after_lost_poll_response(env, tmp_path, monkeypatch):
    device = client(env, tmp_path, monkeypatch)
    submit_client(env, device)
    env[0].review(device.state["deviceId"], True)
    first = device.poll_approval()
    assert "credential" not in first
    credential = device.state.pop("credential")
    device.save()
    restarted = DeviceClient(tmp_path, session=FlaskSession(env[1]))
    restarted.run(once=True)
    assert restarted.state["credential"] == credential


def test_pending_agent_does_not_send_nonce_to_different_backend(tmp_path):
    with pytest.raises(RegistrationError, match="HTTPS"):
        DeviceClient(tmp_path, "http://public.example")
    device = DeviceClient(tmp_path, "https://original.example")
    device.state["registrationStatus"] = "pending_approval"
    device.save()
    with pytest.raises(RegistrationError, match="cannot change"):
        DeviceClient(tmp_path, "https://different.example")


def test_generic_sender_and_participant_recipient_are_separate(monkeypatch):
    from functions.registration.mailer import SmtpMailer
    captured = []
    class Smtp:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def starttls(self, **kwargs):
            pass
        def login(self, username, password):
            pass
        def send_message(self, message):
            captured.append(message)
    monkeypatch.setattr("functions.registration.mailer.smtplib.SMTP", Smtp)
    mailer = SmtpMailer("smtp.example.com", 587, "service", "test-password", "GridGPU <noreply@gridgpu.example>")
    mailer.send_token("participant@example.com", "test-emailed-token", Clock().now)
    message = captured[0]
    assert message["From"] == "GridGPU <noreply@gridgpu.example>"
    assert message["To"] == "participant@example.com"
    assert "test-emailed-token" in message.get_content()
    assert "has been approved" not in message.get_content()


def test_cli_submits_pending_device_then_resumes_after_approval(env, tmp_path, monkeypatch, capsys):
    from gridgpu import __main__ as cli
    service, api, mailer, store, _ = env
    token = emailed_token(env)
    monkeypatch.setattr("gridgpu.registration.client.inspect_hardware", lambda: HARDWARE)
    monkeypatch.setattr("gridgpu.registration.client.gpu_inventory", lambda: HARDWARE["gpus"])
    session = FlaskSession(api)
    monkeypatch.setattr(cli, "DeviceClient", lambda state_dir, api_url, allow_local_http:
        DeviceClient(state_dir, api_url, allow_local_http, session=session))
    monkeypatch.setattr("builtins.input", lambda prompt="": token)
    assert cli.main(["register", "--state-dir", str(tmp_path), "--api-url", "https://backend.example", "--once"]) == 0
    device_id, record = store.list("devices")[0]
    assert record["status"] == "pending_approval" and record["lastSeenAt"] is None
    assert not any(path.endswith("/heartbeat") for _, path in session.calls)
    service.review(device_id, True)
    assert cli.main(["run", "--state-dir", str(tmp_path), "--once"]) == 0
    assert store.records[("devices", device_id)]["lastSeenAt"] is not None
    credential = json.loads((tmp_path / "registration.json").read_text())["credential"]
    output = capsys.readouterr().out
    assert token not in output and credential not in output
    assert len(mailer.sent) == 1
