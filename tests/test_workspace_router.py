"""Tests for the workspace router."""

import pytest
from unittest.mock import patch, MagicMock

from web.workspace_router import (
    Workspace,
    WORKSPACE_LABELS,
    WORKSPACE_DESCRIPTIONS,
    WORKSPACE_BY_NUMBER,
    META_COMMANDS,
    is_meta_command,
    is_workspace_switch,
    get_workspace,
    set_workspace,
    dispatch,
)


class TestWorkspaceEnum:
    """Test workspace enum and constants."""

    def test_all_workspaces_have_labels(self):
        for ws in Workspace:
            assert ws in WORKSPACE_LABELS

    def test_all_workspaces_have_descriptions(self):
        for ws in Workspace:
            assert ws in WORKSPACE_DESCRIPTIONS

    def test_number_mapping_covers_all_workspaces(self):
        assert len(WORKSPACE_BY_NUMBER) == len(Workspace)
        for num, ws in WORKSPACE_BY_NUMBER.items():
            assert ws in Workspace


class TestMetaCommands:
    """Test meta command detection."""

    def test_back_is_meta(self):
        assert is_meta_command("back") is True

    def test_menu_is_meta(self):
        assert is_meta_command("menu") is True

    def test_home_is_meta(self):
        assert is_meta_command("home") is True

    def test_slash_commands_are_meta(self):
        assert is_meta_command("/menu") is True
        assert is_meta_command("/back") is True
        assert is_meta_command("/home") is True

    def test_case_insensitive(self):
        assert is_meta_command("BACK") is True
        assert is_meta_command("Menu") is True

    def test_whitespace_handled(self):
        assert is_meta_command("  back  ") is True

    def test_random_text_not_meta(self):
        assert is_meta_command("hello") is False
        assert is_meta_command("back to basics") is False


class TestWorkspaceSwitch:
    """Test workspace switch detection."""

    def test_number_switch(self):
        assert is_workspace_switch("1") == Workspace.FEED
        assert is_workspace_switch("2") == Workspace.BUILD
        assert is_workspace_switch("3") == Workspace.AUTO
        # Only 3 workspaces remain; the old 4-7 are no longer switch targets.
        assert is_workspace_switch("4") is None

    def test_name_switch(self):
        assert is_workspace_switch("feed") == Workspace.FEED
        assert is_workspace_switch("build") == Workspace.BUILD
        assert is_workspace_switch("auto") == Workspace.AUTO

    def test_legacy_names_route_to_auto(self):
        """Inspect/Challenge/Validate/Export were folded into Auto & Ask, but their
        names still route there rather than being treated as chat messages."""
        for name in ("inspect", "challenge", "validate", "export"):
            assert is_workspace_switch(name) == Workspace.AUTO

    def test_label_switch(self):
        assert is_workspace_switch("Feed Data") == Workspace.FEED
        assert is_workspace_switch("Build Plan") == Workspace.BUILD

    def test_case_insensitive(self):
        assert is_workspace_switch("FEED") == Workspace.FEED
        assert is_workspace_switch("Build plan") == Workspace.BUILD

    def test_invalid_returns_none(self):
        assert is_workspace_switch("hello") is None
        assert is_workspace_switch("8") is None
        assert is_workspace_switch("") is None


class TestGetSetWorkspace:
    """Test workspace state management via Redis."""

    @patch("web.workspace_router._get_redis_client")
    def test_get_workspace_default_is_auto(self, mock_redis):
        mock_r = MagicMock()
        mock_r.get.return_value = None
        mock_redis.return_value = mock_r

        result = get_workspace("session_123")
        assert result == Workspace.AUTO

    @patch("web.workspace_router._get_redis_client")
    def test_get_workspace_returns_stored(self, mock_redis):
        mock_r = MagicMock()
        mock_r.get.return_value = "feed"
        mock_redis.return_value = mock_r

        result = get_workspace("session_123")
        assert result == Workspace.FEED

    @patch("web.workspace_router._get_redis_client")
    def test_set_workspace_calls_redis(self, mock_redis):
        mock_r = MagicMock()
        mock_redis.return_value = mock_r

        set_workspace("session_123", Workspace.BUILD)
        mock_r.set.assert_called_once_with(
            "workspace:session_123", "build", ex=86400
        )

    @patch("web.workspace_router._get_redis_client")
    def test_get_workspace_invalid_value_defaults_auto(self, mock_redis):
        mock_r = MagicMock()
        mock_r.get.return_value = "invalid_workspace"
        mock_redis.return_value = mock_r

        result = get_workspace("session_123")
        assert result == Workspace.AUTO

    @patch("web.workspace_router._get_redis_client")
    def test_get_workspace_redis_error_defaults_auto(self, mock_redis):
        mock_redis.side_effect = Exception("Connection refused")

        result = get_workspace("session_123")
        assert result == Workspace.AUTO


class TestDispatch:
    """Test the main dispatch function."""

    @patch("web.workspace_router.set_workspace")
    @patch("web.workspace_router.get_workspace")
    def test_meta_command_shows_menu(self, mock_get, mock_set):
        result = dispatch("session_123", "back")
        assert result["action"] == "show_menu"
        assert result["workspace"] == Workspace.AUTO
        mock_set.assert_called_once_with("session_123", Workspace.AUTO)

    @patch("web.workspace_router.set_workspace")
    @patch("web.workspace_router.get_workspace")
    def test_number_switches_workspace(self, mock_get, mock_set):
        result = dispatch("session_123", "1")
        assert result["action"] == "switch_workspace"
        assert result["workspace"] == Workspace.FEED
        mock_set.assert_called_once_with("session_123", Workspace.FEED)

    @patch("web.workspace_router.set_workspace")
    @patch("web.workspace_router.get_workspace")
    def test_regular_message_dispatches_to_handler(self, mock_get, mock_set):
        mock_get.return_value = Workspace.FEED
        result = dispatch("session_123", "Here's my pricing data")
        assert result["action"] == "handle_message"
        assert result["workspace"] == Workspace.FEED
        assert result["data"]["message"] == "Here's my pricing data"
        mock_set.assert_not_called()
