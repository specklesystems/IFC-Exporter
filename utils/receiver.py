# =============================================================================
# receiver.py
# Connects to Speckle and receives the root Base object for a given version.
# =============================================================================

from specklepy.api.client import SpeckleClient
from specklepy.api.credentials import get_default_account
from specklepy.api import operations
from specklepy.transports.server import ServerTransport
import utils.config as config


def get_client() -> SpeckleClient:
    """
    Create and authenticate a SpeckleClient.
    Uses a personal access token from config.py.
    To use your local Speckle Manager account instead, swap to get_default_account().
    """
    client = SpeckleClient(host=config.SPECKLE_HOST)

    if config.SPECKLE_TOKEN and config.SPECKLE_TOKEN != "YOUR_PERSONAL_ACCESS_TOKEN":
        client.authenticate_with_token(config.SPECKLE_TOKEN)
    else:
        # Fallback: use account from Speckle Manager desktop app
        account = get_default_account()
        if account is None:
            raise RuntimeError(
                "No Speckle account found. Either set SPECKLE_TOKEN in config.py "
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

    print(f"🔗 Connecting to {config.SPECKLE_HOST}...")
    print(f"📦 Receiving project={project_id}  version={version_id}")

    # Get version metadata to find the referenced object ID
    version = client.version.get(version_id,project_id)
    referenced_object_id = version.referenced_object

    # Download the full object graph
    transport = ServerTransport(stream_id=project_id, client=client)
    base = operations.receive(referenced_object_id, transport)

    # Read units from the root object
    units = getattr(base, "units", config.DEFAULT_UNITS) or config.DEFAULT_UNITS

    # IFC file is declared in MILLIMETRES — no conversion needed.
    # All geometry stays in source units (mm). scale=1.0 means "keep as-is".
    scale = 1.0

    print(f"✅ Received root object  units={units}  scale=1.0 (IFC declared as mm)")
    return base, scale
