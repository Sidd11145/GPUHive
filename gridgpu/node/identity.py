import uuid
import socket
import json
from pathlib import Path

IDENTITY_FILE = Path.home() / ".gridgpu_identity.json"


def get_or_create_identity(path: Path | None = None) -> dict:
    """
    Returns this peer's stable identity.
    Creates and saves a new one on first run,
    then reloads it on every subsequent run.
    """
    path = Path(path) if path is not None else IDENTITY_FILE
    if path.exists():
        with path.open() as f:
            return json.load(f)

    identity = {
        "peer_id":  str(uuid.uuid4()),
        "hostname": socket.gethostname(),
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as f:
        json.dump(identity, f, indent=2)

    return identity
