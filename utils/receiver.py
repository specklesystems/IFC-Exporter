# =============================================================================
# receiver.py
# Connects to Speckle and receives the root Base object for a given version.
# =============================================================================

import os
from dotenv import load_dotenv
from specklepy.api.client import SpeckleClient
from specklepy.api.credentials import get_default_account
from specklepy.api import operations
from specklepy.transports.server import ServerTransport

load_dotenv()

SPECKLE_HOST = os.getenv("SPECKLE_SERVER_URL", "https://app.speckle.systems")
SPECKLE_TOKEN = os.getenv("SPECKLE_TOKEN", "")
DEFAULT_UNITS = "mm"


def get_client() -> SpeckleClient:
    """
    Create and authenticate a SpeckleClient.
    Uses a personal access token from the .env file.
    To use your local Speckle Manager account instead, swap to get_default_account().
    """
    client = SpeckleClient(host=SPECKLE_HOST)

    if SPECKLE_TOKEN and SPECKLE_TOKEN != "YOUR_PERSONAL_ACCESS_TOKEN":
        client.authenticate_with_token(SPECKLE_TOKEN)
    else:
        # Fallback: use account from Speckle Manager desktop app
        account = get_default_account()
        if account is None:
            raise RuntimeError(
                "No Speckle account found. Either set SPECKLE_TOKEN in .env "
                "or log in via Speckle Manager."
            )
        client.authenticate_with_account(account)

    return client


def receive_version(project_id: str, version_id: str):
    """
    Receive the root Base object from a Speckle version.

    Args:
        project_id: The Speckle project (stream) ID.
        version_id: The version (commit) ID to receive.

    Returns:
        A specklepy Base object — the root of the object graph.
    """
    client = get_client()

    print(f"🔗 Connecting to {SPECKLE_HOST}...")
    print(f"📦 Receiving project={project_id}  version={version_id}")

    # Get version metadata to find the referenced object ID
    version = client.version.get(version_id, project_id)
    referenced_object_id = version.referenced_object

    # Download the full object graph
    transport = ServerTransport(stream_id=project_id, client=client)
    base = operations.receive(referenced_object_id, transport)

    # Read units from the root object
    units = getattr(base, "units", DEFAULT_UNITS) or DEFAULT_UNITS

    # IFC file is declared in MILLIMETRES — no conversion needed.
    # All geometry stays in source units (mm). scale=1.0 means "keep as-is".
    scale = 1.0

    print(f"✅ Received root object  units={units}  scale=1.0 (IFC declared as mm)")
    return base, scale
