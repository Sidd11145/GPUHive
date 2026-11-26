"""Firebase entry point: one HTTPS function routes the private registration API."""
from functools import lru_cache
import os

import firebase_admin
from firebase_admin import firestore
from firebase_functions import https_fn
from firebase_functions.params import SecretParam

from registration.api import create_app
from registration.mailer import SmtpMailer
from registration.service import RegistrationService, Settings
from registration.store import FirestoreStore

ADMIN_SECRET = SecretParam("GRIDGPU_ADMIN_SECRET")
ISSUER_SECRET = SecretParam("GRIDGPU_DEVICE_ISSUER_SECRET")
SMTP_PASSWORD = SecretParam("GRIDGPU_SMTP_PASSWORD")


@lru_cache(maxsize=1)
def application():
    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app()
    settings = Settings(
        approval_poll_interval_seconds=int(os.getenv("APPROVAL_POLL_INTERVAL_SECONDS", "30")),
        heartbeat_interval_seconds=int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "300")),
        offline_after_seconds=int(os.getenv("OFFLINE_AFTER_SECONDS", "900")),
        tier_a_mib=int(os.getenv("TIER_A_MIB", "4096")),
        tier_b_mib=int(os.getenv("TIER_B_MIB", "8192")),
        tier_c_mib=int(os.getenv("TIER_C_MIB", "12288")),
    )
    mailer = SmtpMailer(os.environ["SMTP_HOST"], os.getenv("SMTP_PORT", "587"),
                        os.environ["SMTP_USER"], SMTP_PASSWORD.value(), os.environ["SMTP_FROM"])
    service = RegistrationService(FirestoreStore(firestore.client()), mailer, ISSUER_SECRET.value(), settings)
    return create_app(service, ADMIN_SECRET.value())


@https_fn.on_request(secrets=[ADMIN_SECRET, ISSUER_SECRET, SMTP_PASSWORD],
                     region="europe-west1", max_instances=10, timeout_sec=60)
def registration_api(req: https_fn.Request) -> https_fn.Response:
    app = application()
    with app.request_context(req.environ):
        return app.full_dispatch_request()
