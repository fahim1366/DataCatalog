from __future__ import annotations

import hashlib
from typing import Union
import uuid


def calculate_schedule_jitter(
    connection_id: Union[uuid.UUID, str],
    max_jitter_seconds: int = 900,
) -> int:
    """
    Computes a deterministic, repeatable schedule jitter offset in seconds
    based on the SHA-256 hash of the connection UUID.

    Guarantees:
    - Strictly deterministic for any given UUID across all scheduler reboots.
    - Uniformly distributed in the half-open interval [0, max_jitter_seconds).
    - Prevents midnight 'thundering herd' spikes when scheduling large connector fleets.

    Args:
        connection_id: UUID or string UUID of the target DatabaseConnection.
        max_jitter_seconds: Maximum jitter window in seconds (default: 900 = 15 minutes).

    Returns:
        Integer offset in seconds within [0, max_jitter_seconds).

    Raises:
        ValueError: If max_jitter_seconds is not positive or if connection_id is invalid.
    """
    if max_jitter_seconds <= 0:
        raise ValueError(f"max_jitter_seconds must be a positive integer, got {max_jitter_seconds}")

    if isinstance(connection_id, str):
        connection_id = uuid.UUID(connection_id)

    digest = hashlib.sha256(connection_id.bytes).hexdigest()
    return int(digest, 16) % max_jitter_seconds
