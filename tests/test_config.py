import pytest
from pydantic import ValidationError

from app.config import Settings


def test_dev_auth_refused_in_production():
    with pytest.raises(ValidationError, match="DEV_AUTH"):
        Settings(env="production", dev_auth=True, _env_file=None)


def test_dev_auth_allowed_in_development():
    assert Settings(env="development", dev_auth=True, _env_file=None).dev_auth is True
