from datetime import datetime
import hmac
import json
from pathlib import Path
import secrets

from flask import Flask, Response, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge, UnsupportedMediaType

from .service import ApiError


def create_app(service, admin_secret):
    if len(admin_secret) < 32:
        raise ValueError("Admin secret must contain at least 32 characters")
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

    def response(value, status=200):
        return Response(json.dumps(value, default=lambda v: v.isoformat() if isinstance(v, datetime) else str(v)),
                        status=status, content_type="application/json")

    def body(fields):
        value = request.get_json()
        if not isinstance(value, dict) or set(value) != set(fields):
            raise ApiError(400, "Request fields must be: " + ", ".join(fields))
        return value

    def bearer():
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer ") or len(header) > 512:
            raise ApiError(401, "Missing credential")
        return header[7:]

    @app.before_request
    def protect_admin():
        if request.path.startswith("/admin/"):
            if not hmac.compare_digest(bearer().encode(), admin_secret.encode()):
                raise ApiError(401, "Invalid admin credential")

    @app.after_request
    def private_response(result):
        result.headers["Cache-Control"] = "no-store"
        result.headers["X-Content-Type-Options"] = "nosniff"
        result.headers["Referrer-Policy"] = "no-referrer"
        return result

    @app.errorhandler(ApiError)
    def api_error(error):
        return response({"error": str(error)}, error.status)

    @app.errorhandler(BadRequest)
    def invalid_json(error):
        return response({"error": "Invalid JSON request"}, 400)

    @app.errorhandler(UnsupportedMediaType)
    def invalid_content_type(error):
        return response({"error": "Use application/json"}, 415)

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(error):
        return response({"error": "Request exceeds 64 KiB"}, 413)

    def page(name):
        nonce = secrets.token_urlsafe(16)
        html = (Path(__file__).parent / "pages" / name).read_text().replace("{{NONCE}}", nonce)
        result = Response(html, content_type="text/html")
        result.headers["Content-Security-Policy"] = (
            f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
            "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        return result

    @app.get("/admin")
    def admin_page():
        # Public shell contains no data. Every data/action endpoint requires the owner's secret.
        return page("admin.html")

    @app.get("/health")
    def health():
        return response({"status": "ok"})

    @app.post("/requests")
    def new_request():
        data = body(["email", "name", "availability", "location"])
        return response(service.request(data["email"], data["name"],
                                        data["availability"], data["location"]), 202)

    @app.post("/admin/devices/<device_id>/approve")
    def approve(device_id):
        return response(service.review(device_id, True))

    @app.post("/admin/devices/<device_id>/reject")
    def reject(device_id):
        return response(service.review(device_id, False))

    @app.get("/admin/devices")
    def devices():
        return response(service.devices(request.args.get("status")))

    @app.post("/admin/devices/<device_id>/disable")
    def disable(device_id):
        return response(service.set_enabled(device_id, False))

    @app.post("/admin/devices/<device_id>/enable")
    def enable(device_id):
        return response(service.set_enabled(device_id, True))

    @app.post("/devices/register")
    def register():
        data = body(["token", "deviceId", "registrationNonce", "hardware"])
        return response(service.register(data["token"], data["deviceId"], data["registrationNonce"], data["hardware"]), 201)

    @app.get("/devices/<device_id>/approval")
    def approval_status(device_id):
        return response(service.approval_status(device_id, bearer()))

    @app.post("/devices/<device_id>/heartbeat")
    def heartbeat(device_id):
        data = body(["telemetry"])
        return response(service.heartbeat(device_id, bearer(), data["telemetry"]))

    return app
