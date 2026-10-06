import pytest
from pydantic import ValidationError

from app.config import Settings

SECRET = "x" * 32


def test_dev_auth_refused_in_production():
    with pytest.raises(ValidationError, match="DEV_AUTH"):
        Settings(env="production", dev_auth=True, dev_auth_secret=SECRET, _env_file=None)


def test_dev_auth_allowed_in_development_with_a_secret():
    assert Settings(env="development", dev_auth=True, dev_auth_secret=SECRET, _env_file=None).dev_auth is True


def test_dev_auth_needs_a_long_secret():
    with pytest.raises(ValidationError, match="DEV_AUTH_SECRET"):
        Settings(dev_auth=True, dev_auth_secret="short", _env_file=None)


@pytest.mark.parametrize("alg", ["HS256", "none"])
def test_symmetric_or_none_jwks_algorithms_refused(alg):
    with pytest.raises(ValidationError, match="asymmetric"):
        Settings(jwt_algorithms=[alg], _env_file=None)
