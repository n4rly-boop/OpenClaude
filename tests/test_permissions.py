"""Tests for security rules and environment building."""

import os
import pytest

from bot.permissions import build_env, _SAFE_ENV_KEYS


class TestBuildEnv:
    def test_admin_gets_full_env(self, tmp_dir):
        env = build_env(is_admin=True, cwd=str(tmp_dir), thread_id=0)
        # Admin should get PATH and other real env vars
        assert "PATH" in env
        assert env["OPENCLAUDE_IS_ADMIN"] == "1"

    def test_nonadmin_gets_safe_keys_only(self, tmp_dir, monkeypatch):
        monkeypatch.setenv("SECRET_API_KEY", "should_not_appear")
        env = build_env(is_admin=False, cwd=str(tmp_dir), thread_id=0)
        assert "SECRET_API_KEY" not in env
        assert env["OPENCLAUDE_IS_ADMIN"] == "0"
        # Only safe keys + injected keys
        for key in env:
            assert key in _SAFE_ENV_KEYS or key.startswith("OPENCLAUDE_") or key in (
                "PATH", "IS_SANDBOX"
            ), f"Unexpected env key for non-admin: {key}"

    def test_workspace_set(self, tmp_dir):
        env = build_env(is_admin=False, cwd=str(tmp_dir), thread_id=42)
        assert env["OPENCLAUDE_WORKSPACE"] == str(tmp_dir)
        assert env["OPENCLAUDE_THREAD_ID"] == "42"


class TestBlockedBashPatterns:
    """Test that the permission handler blocks dangerous commands."""

    @pytest.fixture
    def handler(self, tmp_dir):
        from bot.permissions import make_permission_handler
        return make_permission_handler(is_admin=False, workspace=str(tmp_dir))

    @pytest.mark.parametrize("cmd", [
        "systemctl stop foo",
        "kill -9 1234",
        "iptables -F",
        "vim /etc/ssh/sshd_config",
    ])
    @pytest.mark.asyncio
    async def test_blocked_commands(self, handler, cmd):
        result = await handler("Bash", {"command": cmd}, {})
        assert hasattr(result, "message"), f"Expected deny for: {cmd}"

    @pytest.mark.parametrize("cmd", [
        "ls -la",
        "grep -r foo .",
        "cat README.md",
        "python3 --version",
    ])
    @pytest.mark.asyncio
    async def test_allowed_commands(self, handler, cmd):
        result = await handler("Bash", {"command": cmd}, {})
        assert hasattr(result, "updated_input"), f"Expected allow for: {cmd}"


class TestWriteProtection:
    @pytest.fixture
    def handler(self, tmp_dir):
        from bot.permissions import make_permission_handler
        return make_permission_handler(is_admin=False, workspace=str(tmp_dir))

    @pytest.mark.asyncio
    async def test_guard_script_blocked(self, handler):
        result = await handler("Write", {"file_path": "/root/OpenClaude/guard/guard.sh"}, {})
        assert hasattr(result, "message")

    @pytest.mark.asyncio
    async def test_etc_ssh_blocked(self, handler):
        result = await handler("Write", {"file_path": "/etc/ssh/sshd_config"}, {})
        assert hasattr(result, "message")

    @pytest.mark.asyncio
    async def test_workspace_write_allowed(self, handler, tmp_dir):
        filepath = str(tmp_dir / "test.txt")
        result = await handler("Write", {"file_path": filepath}, {})
        assert hasattr(result, "updated_input")

    @pytest.mark.asyncio
    async def test_outside_workspace_blocked(self, handler):
        result = await handler("Write", {"file_path": "/tmp/outside.txt"}, {})
        assert hasattr(result, "message")
