"""Host/CORS/强制鉴权的启动与请求边界。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def test_unknown_host_is_rejected(client):
    response = client.get("/api/health", headers={"Host": "attacker.example"})
    assert response.status_code == 400
    assert "Invalid host" in response.text


def test_token_protects_all_sensitive_endpoints(client):
    old = settings.api_token
    object.__setattr__(settings, "api_token", "test-secret")
    try:
        assert client.get("/api/health").status_code == 200
        for path in (
            "/api/meta",
            "/api/samples",
            "/api/reviews",
            "/api/metrics",
            "/api/reviews/not-a-real-job",
            "/api/reviews/not-a-real-job/report?format=md",
        ):
            assert client.get(path).status_code == 401, path
    finally:
        object.__setattr__(settings, "api_token", old)


def test_require_auth_without_token_refuses_startup():
    old = settings.require_auth
    old_token = settings.api_token
    object.__setattr__(settings, "require_auth", True)
    object.__setattr__(settings, "api_token", "")
    try:
        with pytest.raises(RuntimeError, match="REQUIRE_AUTH"):
            with TestClient(app):
                pass
    finally:
        object.__setattr__(settings, "require_auth", old)
        object.__setattr__(settings, "api_token", old_token)


def test_non_local_host_without_token_refuses_startup():
    old_hosts = settings.allowed_hosts
    old_token = settings.api_token
    object.__setattr__(settings, "allowed_hosts", "192.168.1.10")
    object.__setattr__(settings, "api_token", "")
    try:
        with pytest.raises(RuntimeError, match="非本机"):
            with TestClient(app):
                pass
    finally:
        object.__setattr__(settings, "allowed_hosts", old_hosts)
        object.__setattr__(settings, "api_token", old_token)


def test_wildcard_cors_refuses_startup():
    old = settings.cors_origin
    object.__setattr__(settings, "cors_origin", "*")
    try:
        with pytest.raises(RuntimeError, match="CORS_ORIGIN"):
            with TestClient(app):
                pass
    finally:
        object.__setattr__(settings, "cors_origin", old)
