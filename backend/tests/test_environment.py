import sys
import pytest

def test_python_and_core_dependencies():
    """Verify runtime environment and library availability."""
    assert sys.version_info >= (3, 12), "Requires Python 3.12+"
    
    import fastapi
    import sqlalchemy
    import alembic
    import asyncpg
    import pydantic
    import cryptography
    import argon2
    import jwt
    import greenlet

    assert fastapi.__version__
    assert sqlalchemy.__version__
    assert alembic.__version__

