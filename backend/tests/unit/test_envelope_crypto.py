import time
import pytest
from cryptography.exceptions import InvalidTag

from backend.src.services.crypto.envelope import EnvelopeEncryptionService


def test_master_kek_validation():
    """Verify Master KEK must be exactly 256 bits (32 bytes / 64 hex characters)."""
    # Valid 64-hex string (32 bytes)
    valid_kek = "a" * 64
    service = EnvelopeEncryptionService(master_kek_hex=valid_kek)
    assert service.master_kek == bytes.fromhex(valid_kek)

    # Invalid: Too short
    with pytest.raises(ValueError, match="must be exactly 256 bits"):
        EnvelopeEncryptionService(master_kek_hex="a" * 32)

    # Invalid: Too long
    with pytest.raises(ValueError, match="must be exactly 256 bits"):
        EnvelopeEncryptionService(master_kek_hex="a" * 128)

    # Invalid: Non-hex characters
    with pytest.raises(ValueError):
        EnvelopeEncryptionService(master_kek_hex="z" * 64)


def test_encrypt_decrypt_round_trip():
    """Verify encrypt and decrypt round-trip correctly preserves arbitrary credential dictionaries."""
    service = EnvelopeEncryptionService()
    sample_payload = {
        "username": "reporting_user",
        "password": "SuperSecretPassword!#123",
        "ssl_cert": "-----BEGIN CERTIFICATE-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA...\n-----END CERTIFICATE-----",
        "custom_options": {"connect_timeout": 10, "application_name": "DataCatalog"},
    }

    encrypted_dek, encrypted_credentials = service.encrypt_credentials(sample_payload)

    # Assert outputs are bytes
    assert isinstance(encrypted_dek, bytes)
    assert isinstance(encrypted_credentials, bytes)

    # Assert minimum lengths (12 bytes IV + ciphertext + 16 bytes tag)
    # DEK is 32 bytes, so encrypted_dek should be 12 + 32 + 16 = 60 bytes
    assert len(encrypted_dek) == 60
    assert len(encrypted_credentials) > 28

    # Decrypt and compare
    decrypted = service.decrypt_credentials(encrypted_dek, encrypted_credentials)
    assert decrypted == sample_payload


def test_ephemeral_dek_and_iv_randomness():
    """Verify each encryption operation generates unique ephemeral DEK and random 96-bit IVs."""
    service = EnvelopeEncryptionService()
    payload = {"host": "10.0.0.1", "password": "StaticPassword123"}

    # Encrypt the same payload multiple times
    enc_results = [service.encrypt_credentials(payload) for _ in range(10)]

    encrypted_deks = [r[0] for r in enc_results]
    encrypted_payloads = [r[1] for r in enc_results]

    # Every encrypted DEK must be unique
    assert len(set(encrypted_deks)) == 10

    # Every encrypted credentials payload must be unique
    assert len(set(encrypted_payloads)) == 10

    # Verify IVs (first 12 bytes) are distinct
    dek_ivs = [dek[:12] for dek in encrypted_deks]
    data_ivs = [data[:12] for data in encrypted_payloads]
    assert len(set(dek_ivs)) == 10
    assert len(set(data_ivs)) == 10


def test_tamper_detection_and_tag_validation():
    """Verify that tampering with IV, ciphertext, or auth tag raises cryptographic error."""
    service = EnvelopeEncryptionService()
    payload = {"secret": "confidential_api_token"}

    encrypted_dek, encrypted_credentials = service.encrypt_credentials(payload)

    # 1. Tamper with encrypted DEK
    tampered_dek = bytearray(encrypted_dek)
    tampered_dek[15] ^= 0xFF
    with pytest.raises(InvalidTag):
        service.decrypt_credentials(bytes(tampered_dek), encrypted_credentials)

    # 2. Tamper with encrypted credentials payload
    tampered_creds = bytearray(encrypted_credentials)
    tampered_creds[-1] ^= 0xFF  # Flip bit in auth tag
    with pytest.raises(InvalidTag):
        service.decrypt_credentials(encrypted_dek, bytes(tampered_creds))

    # 3. Tamper with IV
    tampered_iv_creds = bytearray(encrypted_credentials)
    tampered_iv_creds[0] ^= 0xFF
    with pytest.raises(InvalidTag):
        service.decrypt_credentials(encrypted_dek, bytes(tampered_iv_creds))


def test_wrong_master_kek_fails():
    """Verify decryption fails when attempted with a different master KEK."""
    service1 = EnvelopeEncryptionService(master_kek_hex="1" * 64)
    service2 = EnvelopeEncryptionService(master_kek_hex="2" * 64)

    payload = {"token": "secret-123"}
    encrypted_dek, encrypted_credentials = service1.encrypt_credentials(payload)

    with pytest.raises(InvalidTag):
        service2.decrypt_credentials(encrypted_dek, encrypted_credentials)


def test_envelope_encryption_performance():
    """Verify encryption and decryption round-trip executes in < 5ms per operation."""
    service = EnvelopeEncryptionService()
    payload = {
        "host": "prod-db-12.internal.company.com",
        "port": 5432,
        "username": "etl_pipeline",
        "password": "VeryComplexAndLongPasswordString123!@#$%",
        "database": "sales_warehouse",
    }

    iterations = 50
    start_time = time.perf_counter()
    for _ in range(iterations):
        enc_dek, enc_creds = service.encrypt_credentials(payload)
        decrypted = service.decrypt_credentials(enc_dek, enc_creds)
        assert decrypted["database"] == "sales_warehouse"

    total_duration = time.perf_counter() - start_time
    avg_duration_ms = (total_duration / iterations) * 1000

    # Ensure average round-trip takes well under 5ms (typically < 0.2ms with AES-NI)
    assert avg_duration_ms < 5.0, f"Average crypto round-trip took {avg_duration_ms:.2f}ms, expected < 5.0ms"

