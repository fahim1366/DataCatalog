from __future__ import annotations

import json
import os
from typing import Any, Dict, Tuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.src.core.config import settings


class EnvelopeEncryptionService:
    """
    AES-256-GCM Envelope Encryption Service for database credentials.

    Architecture:
    - Master KEK (Key Encryption Key): 256-bit static secret loaded securely from configuration/KMS.
    - Ephemeral DEK (Data Encryption Key): 256-bit cryptographically secure random key per connection.
    - Associated Data (AEAD AAD): Enforces cryptographic context bindings:
      - DEK Wrapping: b"DEK_WRAP"
      - Credentials Encryption: b"DB_CREDS"
    - Output Format:
      - encrypted_dek: 12-byte IV + 32-byte encrypted DEK ciphertext + 16-byte auth tag (60 bytes)
      - encrypted_credentials: 12-byte IV + variable ciphertext + 16-byte auth tag
    """

    DEK_WRAP_AAD: bytes = b"DEK_WRAP"
    CREDS_ENCRYPT_AAD: bytes = b"DB_CREDS"
    IV_LENGTH: int = 12
    TAG_LENGTH: int = 16
    KEY_LENGTH_BYTES: int = 32

    def __init__(self, master_kek_hex: str = settings.CATALOG_MASTER_KEK) -> None:
        try:
            self.master_kek = bytes.fromhex(master_kek_hex)
        except ValueError as exc:
            raise ValueError(
                "CATALOG_MASTER_KEK must be a valid hex string of exactly 256 bits (32 bytes / 64 hex characters)"
            ) from exc

        if len(self.master_kek) != self.KEY_LENGTH_BYTES:
            raise ValueError(
                f"CATALOG_MASTER_KEK must be exactly 256 bits (32 bytes / 64 hex characters), got {len(self.master_kek)} bytes"
            )

    def encrypt_credentials(
        self, credentials_payload: Dict[str, Any]
    ) -> Tuple[bytes, bytes]:
        """
        Generates an ephemeral 256-bit DEK, encrypts credentials JSON with DEK,
        and wraps the DEK with the master KEK using AES-256-GCM.

        Returns:
            Tuple[bytes, bytes]: (encrypted_dek, encrypted_credentials)
        """
        # 1. Generate ephemeral 256-bit DEK
        dek = os.urandom(self.KEY_LENGTH_BYTES)

        # 2. Encrypt DEK with KEK (12-byte IV + 32-byte ciphertext + 16-byte tag = 60 bytes)
        kek_aesgcm = AESGCM(self.master_kek)
        dek_iv = os.urandom(self.IV_LENGTH)
        encrypted_dek = dek_iv + kek_aesgcm.encrypt(
            dek_iv, dek, associated_data=self.DEK_WRAP_AAD
        )

        # 3. Encrypt credentials payload with DEK
        payload_bytes = json.dumps(credentials_payload, separators=(",", ":")).encode("utf-8")
        dek_aesgcm = AESGCM(dek)
        data_iv = os.urandom(self.IV_LENGTH)
        encrypted_credentials = data_iv + dek_aesgcm.encrypt(
            data_iv, payload_bytes, associated_data=self.CREDS_ENCRYPT_AAD
        )

        return encrypted_dek, encrypted_credentials

    def decrypt_credentials(
        self, encrypted_dek: bytes, encrypted_credentials: bytes
    ) -> Dict[str, Any]:
        """
        Unwraps the DEK using the master KEK, then decrypts the credentials JSON payload.
        Zero plaintext secrets are retained or logged.

        Raises:
            InvalidTag: If any ciphertext, IV, or auth tag is corrupted or tampered with.
        """
        min_length = self.IV_LENGTH + self.TAG_LENGTH
        if len(encrypted_dek) < min_length:
            raise InvalidTag("Encrypted DEK is too short to contain valid IV and tag")
        if len(encrypted_credentials) < min_length:
            raise InvalidTag("Encrypted credentials payload is too short to contain valid IV and tag")

        # 1. Unwrap DEK with KEK
        dek_iv = encrypted_dek[: self.IV_LENGTH]
        dek_ciphertext = encrypted_dek[self.IV_LENGTH :]
        kek_aesgcm = AESGCM(self.master_kek)
        dek = kek_aesgcm.decrypt(
            dek_iv, dek_ciphertext, associated_data=self.DEK_WRAP_AAD
        )

        # 2. Decrypt credentials payload with unwrapped DEK
        data_iv = encrypted_credentials[: self.IV_LENGTH]
        data_ciphertext = encrypted_credentials[self.IV_LENGTH :]
        dek_aesgcm = AESGCM(dek)
        decrypted_bytes = dek_aesgcm.decrypt(
            data_iv, data_ciphertext, associated_data=self.CREDS_ENCRYPT_AAD
        )

        return json.loads(decrypted_bytes.decode("utf-8"))

