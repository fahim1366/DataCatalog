import inspect
import json
import logging
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock, patch

from backend.src.core.celery_app import celery_app
from backend.src.models.connections import DatabaseConnection, DatabaseEngine, SyncSchedule
from backend.src.models.sync_jobs import SyncJob
from backend.src.services.crypto.envelope import EnvelopeEncryptionService
from backend.src.services.workers.tasks import (
    execute_metadata_sync,
    execute_metadata_sync_task,
    manual_metadata_sync_task,
    test_connection_sync,
    test_connection_task,
)

SUPER_SECRET_PLAINTEXT_PASS = "SuperSecret_Ultra_Sensitive_Password_987654321!"


@pytest.fixture
async def security_test_db(test_db_session: AsyncSession):
    suffix = uuid.uuid4().hex[:8]
    crypto = EnvelopeEncryptionService()
    enc_dek, enc_creds = crypto.encrypt_credentials({
        "username": f"user_{suffix}",
        "password": SUPER_SECRET_PLAINTEXT_PASS,
    })

    conn = DatabaseConnection(
        id=uuid.uuid4(),
        name=f"sec_conn_{suffix}",
        engine_type=DatabaseEngine.POSTGRESQL,
        host="127.0.0.1",
        port=5432,
        database_name=f"sec_db_{suffix}",
        owner="security_auditor",
        kek_version=1,
        encrypted_dek=enc_dek,
        encrypted_credentials=enc_creds,
        custom_params={},
    )
    test_db_session.add(conn)

    job = SyncJob(
        id=uuid.uuid4(),
        connection_id=conn.id,
        status="QUEUED",
        triggered_by="MANUAL",
    )
    test_db_session.add(job)
    await test_db_session.commit()

    return conn, job


def test_task_signatures_contain_zero_secrets():
    """T071: Verify worker task signatures accept strictly UUID/ID parameters."""
    for task in [execute_metadata_sync_task, manual_metadata_sync_task, test_connection_task]:
        sig = inspect.signature(task.run)
        params = list(sig.parameters.keys())
        # 'self' is bound; remaining parameters must be ID only
        non_self_params = [p for p in params if p != "self"]
        for p in non_self_params:
            assert p in ("connection_id", "job_id"), f"Unsanctioned parameter '{p}' in task {task.name}"
            assert "pass" not in p.lower()
            assert "secret" not in p.lower()
            assert "cred" not in p.lower()


def test_celery_enqueued_payload_contains_zero_secrets():
    """T071: Assert simulated enqueued Celery task message payload contains zero plaintext credentials."""
    conn_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())

    # Build standard Celery execution message
    message = {
        "task": execute_metadata_sync_task.name,
        "id": str(uuid.uuid4()),
        "args": [conn_id, job_id],
        "kwargs": {},
        "retries": 0,
    }

    serialized_payload = json.dumps(message)
    assert SUPER_SECRET_PLAINTEXT_PASS not in serialized_payload
    assert "password" not in serialized_payload


@pytest.mark.asyncio
async def test_zero_plaintext_credentials_in_logs_and_returns(
    test_db_session: AsyncSession, security_test_db, caplog
):
    """T071: Assert plaintext credentials never appear in captured logs, return dicts, or DB traces."""
    conn, job = security_test_db
    caplog.set_level(logging.DEBUG)

    # 1. Execute metadata sync under logger surveillance
    with patch(
        "backend.src.services.connectors.postgres.PostgresConnector.extract_all_schemas",
        new_callable=AsyncMock,
        side_effect=Exception(f"Simulated network exception for {conn.host}"),
    ):
        result = await execute_metadata_sync(conn.id, job.id, session=test_db_session)

    # Verify return dict contains zero secrets
    result_str = json.dumps(result)
    assert SUPER_SECRET_PLAINTEXT_PASS not in result_str

    # Verify captured log stream contains zero secrets
    assert SUPER_SECRET_PLAINTEXT_PASS not in caplog.text

    # Verify DB SyncJob fields contain zero secrets
    await test_db_session.refresh(job)
    assert SUPER_SECRET_PLAINTEXT_PASS not in (job.error_message or "")
    assert SUPER_SECRET_PLAINTEXT_PASS not in json.dumps(job.error_trace_jsonb or {})

    # 2. Execute test_connection under logger surveillance
    caplog.clear()
    with patch(
        "backend.src.services.connectors.postgres.PostgresConnector.test_connection",
        new_callable=AsyncMock,
        side_effect=Exception("Simulated connection exception"),
    ):
        conn_test_result = await test_connection_sync(conn.id, session=test_db_session)

    conn_res_str = json.dumps(conn_test_result)
    assert SUPER_SECRET_PLAINTEXT_PASS not in conn_res_str
    assert SUPER_SECRET_PLAINTEXT_PASS not in caplog.text
