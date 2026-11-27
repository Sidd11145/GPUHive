import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import secrets
import uuid


class ApiError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def utcnow():
    return datetime.now(timezone.utc)


def hash_secret(value):
    return hashlib.sha256(value.encode()).hexdigest()


def text(value, name, maximum=300):
    if not isinstance(value, str) or not 0 < len(value) <= maximum:
        raise ApiError(400, f"Invalid {name}")
    return value


def optional_text(value, name, maximum=300):
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > maximum:
        raise ApiError(400, f"Invalid {name}")
    return value.strip()


def validate_hardware(hardware):
    if not isinstance(hardware, dict):
        raise ApiError(400, "Hardware must be an object")
    try:
        encoded = json.dumps(hardware, allow_nan=False)
    except (ValueError, TypeError):
        raise ApiError(400, "Invalid hardware data") from None
    if len(encoded) > 32768:
        raise ApiError(400, "Hardware report exceeds 32 KiB")
    gpus = hardware.get("gpus")
    if not isinstance(gpus, list) or len(gpus) > 16:
        raise ApiError(400, "Hardware must contain at most 16 GPUs")
    for gpu in gpus:
        if not isinstance(gpu, dict):
            raise ApiError(400, "Invalid GPU report")
        text(gpu.get("name"), "GPU name")
        for field in ("vramTotalMiB", "vramFreeMiB"):
            value = gpu.get(field)
            if value is not None and (type(value) is not int or not 0 <= value <= 1048576):
                raise ApiError(400, f"Invalid {field}")
        total, free = gpu.get("vramTotalMiB"), gpu.get("vramFreeMiB")
        if total is not None and free is not None and free > total:
            raise ApiError(400, "Free VRAM cannot exceed total VRAM")
    if type(hardware.get("simulated", False)) is not bool:
        raise ApiError(400, "Invalid simulation flag")
    return json.loads(encoded)


@dataclass(frozen=True)
class Settings:
    token_ttl_seconds: int = 900
    heartbeat_interval_seconds: int = 300
    offline_after_seconds: int = 900
    request_cooldown_seconds: int = 60
    approval_poll_interval_seconds: int = 30
    tier_a_mib: int = 4096
    tier_b_mib: int = 8192
    tier_c_mib: int = 12288

    def __post_init__(self):
        if not 0 < self.tier_a_mib < self.tier_b_mib < self.tier_c_mib:
            raise ValueError("Tier thresholds must increase")
        if self.token_ttl_seconds <= 0 or self.heartbeat_interval_seconds <= 0:
            raise ValueError("Token and heartbeat intervals must be positive")
        if self.offline_after_seconds < self.heartbeat_interval_seconds * 2:
            raise ValueError("Offline timeout must allow at least two heartbeats")
        if not 5 <= self.approval_poll_interval_seconds <= 300:
            raise ValueError("Approval polling interval must be between 5 and 300 seconds")

    def tier(self, hardware):
        # A per-device summary based on its largest GPU, never summed VRAM.
        largest = max((g.get("vramTotalMiB") or 0 for g in hardware["gpus"]), default=0)
        for tier, threshold in (("C", self.tier_c_mib), ("B", self.tier_b_mib), ("A", self.tier_a_mib)):
            if largest >= threshold:
                return tier
        return None


class RegistrationService:
    def __init__(self, store, mailer, issuer_secret, settings=None, clock=utcnow):
        if len(issuer_secret) < 32:
            raise ValueError("Device issuer secret must contain at least 32 characters")
        self.store, self.mailer = store, mailer
        self.issuer_secret = issuer_secret.encode()
        self.settings = settings or Settings()
        self.clock = clock

    def request(self, email, name, availability=None, location=None):
        email = text(email, "email", 254).strip()
        if not re.fullmatch(r"[^\s@<>]{1,64}@[^\s@<>]+\.[^\s@<>]+", email):
            raise ApiError(400, "Enter a valid email address")
        name = text(name, "name", 120).strip()
        availability = optional_text(availability, "availability", 200)
        location = optional_text(location, "location", 120)
        now, token = self.clock(), f"{secrets.randbelow(10 ** 6):06d}"
        token_hash, limiter = hash_secret(token), hash_secret(email.casefold())
        expires = now + timedelta(seconds=self.settings.token_ttl_seconds)
        def create(tx):
            last = tx.get("registrationRequestLimits", limiter)
            if last and (now - last["lastRequestedAt"]).total_seconds() < self.settings.request_cooldown_seconds:
                raise ApiError(429, "Wait before requesting another registration code")
            tx.set("registrationTokens", token_hash, {
                "email": email, "name": name, "availability": availability, "location": location,
                "createdAt": now, "expiresAt": expires, "usedAt": None,
                "revoked": False, "emailStatus": "sending", "deviceId": None, "registrationNonceHash": None,
            })
            tx.set("registrationRequestLimits", limiter, {"lastRequestedAt": now, "tokenHash": token_hash})
        self.store.transaction(create)
        try:
            self.mailer.send_token(email, token, expires)
        except Exception:
            def failed(tx):
                doc = tx.get("registrationTokens", token_hash)
                last = tx.get("registrationRequestLimits", limiter)
                doc.update(revoked=True, emailStatus="failed")
                tx.set("registrationTokens", token_hash, doc)
                if last and last["tokenHash"] == token_hash:
                    last["lastRequestedAt"] = now - timedelta(seconds=self.settings.request_cooldown_seconds)
                    tx.set("registrationRequestLimits", limiter, last)
            self.store.transaction(failed)
            raise ApiError(502, "Email delivery failed; request a new code") from None
        self.store.transaction(lambda tx: tx.update("registrationTokens", token_hash, {"emailStatus": "sent"}))
        return {"status": "token_sent", "expiresAt": expires.isoformat()}

    @staticmethod
    def _id(value):
        try:
            return str(uuid.UUID(value))
        except (ValueError, TypeError, AttributeError):
            raise ApiError(400, "Invalid ID") from None

    @staticmethod
    def _nonce(value):
        value = text(value, "registration nonce", 256)
        if len(value) < 32:
            raise ApiError(400, "Registration nonce is too short")
        return value

    def register(self, token, device_id, nonce, hardware):
        token = text(token, "registration token", 256).strip()
        device_id, nonce = self._id(device_id), self._nonce(nonce)
        hardware = validate_hardware(hardware)
        token_hash, nonce_hash, now = hash_secret(token), hash_secret(nonce), self.clock()
        def enroll(tx):
            token_doc = tx.get("registrationTokens", token_hash)
            device = tx.get("devices", device_id)
            if token_doc is None or token_doc["revoked"]:
                raise ApiError(401, "Invalid registration token")
            if token_doc["emailStatus"] != "sent":
                raise ApiError(409, "Token email has not completed delivery")
            if token_doc["usedAt"] is not None:
                if (token_doc["deviceId"] == device_id and device
                        and hmac.compare_digest(token_doc["registrationNonceHash"], nonce_hash)
                        and hmac.compare_digest(device["registrationNonceHash"], nonce_hash)):
                    return device
                raise ApiError(409, "Registration token has already been used")
            if token_doc["expiresAt"] <= now:
                raise ApiError(401, "Registration token has expired")
            if device is not None:
                raise ApiError(409, "Device ID is already registered")
            device = {
                "email": token_doc["email"], "emailVerifiedAt": now,
                "name": token_doc.get("name", ""), "availability": token_doc.get("availability", ""),
                "location": token_doc.get("location", ""),
                "hardware": hardware, "suggestedTier": self.settings.tier(hardware),
                "hardwareVerification": "self_reported", "speedScore": None,
                "status": "pending_approval", "registrationNonceHash": nonce_hash,
                "credentialHash": None, "credentialSalt": None, "enabled": False,
                "registeredAt": now, "reviewedAt": None, "lastSeenAt": None, "telemetry": {},
            }
            token_doc.update(usedAt=now, deviceId=device_id, registrationNonceHash=nonce_hash)
            tx.set("devices", device_id, device)
            tx.set("registrationTokens", token_hash, token_doc)
            return device
        device = self.store.transaction(enroll)
        return {"deviceId": device_id, "status": device["status"],
                "approvalPollIntervalSeconds": self.settings.approval_poll_interval_seconds}

    def enroll_direct(self, name, email, availability, location, device_id, nonce, hardware):
        # Simple no-OTP, no-approval path: create the device already approved and
        # enabled, returning its credential so it can heartbeat immediately.
        name = text(name, "name", 120).strip()
        email = optional_text(email, "email", 254)
        if email and not re.fullmatch(r"[^\s@<>]{1,64}@[^\s@<>]+\.[^\s@<>]+", email):
            raise ApiError(400, "Enter a valid email address")
        availability = optional_text(availability, "availability", 200)
        location = optional_text(location, "location", 120)
        device_id, nonce = self._id(device_id), self._nonce(nonce)
        hardware = validate_hardware(hardware)
        nonce_hash, now = hash_secret(nonce), self.clock()
        salt = secrets.token_urlsafe(32)
        def enroll(tx):
            existing = tx.get("devices", device_id)
            if existing is not None:
                if hmac.compare_digest(existing["registrationNonceHash"], nonce_hash):
                    return existing
                raise ApiError(409, "Device ID is already registered")
            device = {
                "email": email, "emailVerifiedAt": None,
                "name": name, "availability": availability, "location": location,
                "hardware": hardware, "suggestedTier": self.settings.tier(hardware),
                "hardwareVerification": "self_reported", "speedScore": None,
                "status": "approved", "registrationNonceHash": nonce_hash,
                "credentialHash": None, "credentialSalt": salt, "enabled": True,
                "registeredAt": now, "reviewedAt": now, "lastSeenAt": None, "telemetry": {},
            }
            device["credentialHash"] = hash_secret(self._credential(device_id, device))
            tx.set("devices", device_id, device)
            return device
        device = self.store.transaction(enroll)
        credential = self._credential(device_id, device)
        return {"deviceId": device_id, "status": device["status"],
                "credential": credential,
                "heartbeatIntervalSeconds": self.settings.heartbeat_interval_seconds}

    def _credential(self, device_id, device):
        message = json.dumps(["gridgpu-device-v2", device_id, device["registrationNonceHash"],
                              device["credentialSalt"]], separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(hmac.digest(self.issuer_secret, message, "sha256")).decode().rstrip("=")

    def review(self, device_id, approve):
        device_id, now = self._id(device_id), self.clock()
        target, salt = ("approved", secrets.token_urlsafe(32)) if approve else ("rejected", None)
        def review(tx):
            device = tx.get("devices", device_id)
            if device is None:
                raise ApiError(404, "Unknown device")
            if device["status"] == target:
                return
            if device["status"] != "pending_approval":
                raise ApiError(409, "Device has already been reviewed")
            device.update(status=target, reviewedAt=now, enabled=approve)
            if approve:
                device["credentialSalt"] = salt
                device["credentialHash"] = hash_secret(self._credential(device_id, device))
            tx.set("devices", device_id, device)
        self.store.transaction(review)
        return {"deviceId": device_id, "status": target}

    def approval_status(self, device_id, nonce):
        device_id, nonce = self._id(device_id), self._nonce(nonce)
        device = self.store.transaction(lambda tx: tx.get("devices", device_id))
        if device is None or not hmac.compare_digest(device["registrationNonceHash"], hash_secret(nonce)):
            raise ApiError(401, "Invalid installation identity")
        result = {"deviceId": device_id, "status": device["status"], "enabled": device["enabled"],
                  "approvalPollIntervalSeconds": self.settings.approval_poll_interval_seconds}
        if device["status"] == "approved" and device["enabled"]:
            credential = self._credential(device_id, device)
            if not device["credentialHash"] or not hmac.compare_digest(device["credentialHash"], hash_secret(credential)):
                raise ApiError(503, "Device credential unavailable; contact the owner")
            result.update(credential=credential, heartbeatIntervalSeconds=self.settings.heartbeat_interval_seconds)
        return result

    def heartbeat(self, device_id, credential, telemetry):
        device_id = self._id(device_id)
        credential = text(credential, "device credential", 256)
        if not isinstance(telemetry, dict) or set(telemetry) - {"gpus", "busy"}:
            raise ApiError(400, "Unsupported telemetry fields")
        telemetry = dict(telemetry)
        if "gpus" in telemetry:
            validate_hardware({"gpus": telemetry["gpus"]})
        if "busy" in telemetry and type(telemetry["busy"]) is not bool:
            raise ApiError(400, "Invalid busy flag")
        now = self.clock()
        def update(tx):
            device = tx.get("devices", device_id)
            if (device is None or device.get("status") != "approved" or not device.get("credentialHash")
                    or not hmac.compare_digest(device["credentialHash"], hash_secret(credential))):
                raise ApiError(401, "An approved device credential is required")
            if not device["enabled"]:
                raise ApiError(403, "Device is disabled")
            tx.update("devices", device_id, {"lastSeenAt": now, "telemetry": telemetry})
        self.store.transaction(update)
        return {"deviceId": device_id, "lastSeenAt": now.isoformat(),
                "heartbeatIntervalSeconds": self.settings.heartbeat_interval_seconds}

    def devices(self, status=None):
        if status is not None and status not in ("pending_approval", "approved", "rejected"):
            raise ApiError(400, "Invalid device status")
        now, result = self.clock(), []
        private = {"credentialHash", "credentialSalt", "registrationNonceHash"}
        for key, doc in self.store.list("devices", "status" if status else None, status):
            public = {k: v for k, v in doc.items() if k not in private}
            seen = doc["lastSeenAt"]
            public["online"] = bool(doc["status"] == "approved" and doc["enabled"] and seen and
                (now - seen).total_seconds() < self.settings.offline_after_seconds)
            result.append({"deviceId": key, **public})
        return result

    def set_enabled(self, device_id, enabled):
        device_id = self._id(device_id)
        def update(tx):
            device = tx.get("devices", device_id)
            if device is None:
                raise ApiError(404, "Unknown device")
            if enabled and device["status"] != "approved":
                raise ApiError(409, "Approve the pending device before enabling it")
            tx.update("devices", device_id, {"enabled": enabled})
        self.store.transaction(update)
        return {"deviceId": device_id, "enabled": enabled}
