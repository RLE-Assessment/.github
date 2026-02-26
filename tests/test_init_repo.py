import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

# Add scripts/ to path so we can import init_repo as a module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from init_repo import (
    app,
    check_prerequisites,
    run_command,
    setup_github,
    setup_secrets,
    _setup_gcp_own,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helper: build a fake CompletedProcess
# ---------------------------------------------------------------------------

def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode=0, stdout=stdout, stderr=stderr)


def _fail(stderr: str = "error") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode=1, stdout="", stderr=stderr)


# ---------------------------------------------------------------------------
# CLI help tests
# ---------------------------------------------------------------------------

class TestCLIHelp:
    def test_main_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Initialize a new RLE assessment repository" in result.stdout

    def test_all_help(self):
        result = runner.invoke(app, ["all", "--help"])
        assert result.exit_code == 0
        assert "Run all initialization steps" in result.stdout

    def test_github_help(self):
        result = runner.invoke(app, ["github", "--help"])
        assert result.exit_code == 0
        assert "Create the GitHub repository" in result.stdout

    def test_gcp_help(self):
        result = runner.invoke(app, ["gcp", "--help"])
        assert result.exit_code == 0
        assert "GCP project" in result.stdout

    def test_secrets_help(self):
        result = runner.invoke(app, ["secrets", "--help"])
        assert result.exit_code == 0
        assert "GitHub repository secrets" in result.stdout


# ---------------------------------------------------------------------------
# check_prerequisites
# ---------------------------------------------------------------------------

class TestCheckPrerequisites:
    @patch("init_repo.shutil.which", return_value=None)
    def test_gh_not_installed(self, _mock_which):
        with pytest.raises(typer.Exit):
            check_prerequisites(need_gh=True, need_gcloud=False)

    @patch("init_repo.subprocess.run", return_value=_fail())
    @patch("init_repo.shutil.which", return_value="/usr/bin/gh")
    def test_gh_not_authenticated(self, _mock_which, _mock_run):
        with pytest.raises(typer.Exit):
            check_prerequisites(need_gh=True, need_gcloud=False)

    @patch("init_repo.subprocess.run", return_value=_ok())
    @patch("init_repo.shutil.which", return_value="/usr/bin/gh")
    def test_gh_authenticated(self, _mock_which, _mock_run):
        check_prerequisites(need_gh=True, need_gcloud=False)

    @patch("init_repo.shutil.which", return_value=None)
    def test_gcloud_not_installed(self, _mock_which):
        with pytest.raises(typer.Exit):
            check_prerequisites(need_gh=False, need_gcloud=True)

    @patch("init_repo.subprocess.run", return_value=_ok(stdout="user@example.com"))
    @patch("init_repo.shutil.which", return_value="/usr/bin/gcloud")
    def test_gcloud_authenticated(self, _mock_which, _mock_run):
        check_prerequisites(need_gh=False, need_gcloud=True)

    @patch("init_repo.subprocess.run", return_value=_ok(stdout=""))
    @patch("init_repo.shutil.which", return_value="/usr/bin/gcloud")
    def test_gcloud_not_authenticated(self, _mock_which, _mock_run):
        with pytest.raises(typer.Exit):
            check_prerequisites(need_gh=False, need_gcloud=True)


# ---------------------------------------------------------------------------
# run_command
# ---------------------------------------------------------------------------

class TestRunCommand:
    @patch("init_repo.subprocess.run", return_value=_ok())
    def test_successful_command(self, mock_run):
        result = run_command(
            ["echo", "hello"],
            step=1, total=1,
            title="Test", description="A test command",
        )
        assert result.returncode == 0
        mock_run.assert_called_once()

    @patch("init_repo.subprocess.run", return_value=_fail("something went wrong"))
    def test_failed_command_exits(self, _mock_run):
        with pytest.raises(typer.Exit):
            run_command(
                ["false"],
                step=1, total=1,
                title="Fail", description="Should fail",
            )

    @patch("init_repo.subprocess.run", return_value=_ok(stdout="42\n"))
    def test_capture_output(self, _mock_run):
        result = run_command(
            ["echo", "42"],
            step=1, total=1,
            title="Capture", description="Captures output",
            capture=True,
        )
        assert result.stdout.strip() == "42"

    @patch("init_repo.subprocess.run", return_value=_ok())
    def test_input_data_passed(self, mock_run):
        run_command(
            ["cat"],
            step=1, total=1,
            title="Stdin", description="Passes stdin",
            input_data='{"key": "value"}',
        )
        _, kwargs = mock_run.call_args
        assert kwargs["input"] == '{"key": "value"}'

    @patch("init_repo.subprocess.run", return_value=_fail("ALREADY_EXISTS: entity exists"))
    def test_skip_if_exists_already_exists_keyword(self, _mock_run):
        result = run_command(
            ["gcloud", "create", "thing"],
            step=1, total=1,
            title="Create", description="Creates a thing",
            skip_if_exists=True,
        )
        assert result.returncode == 1

    @patch("init_repo.subprocess.run", return_value=_fail(
        "Resource in projects is the subject of a conflict: "
        "Service account github-actions already exists within project"
    ))
    def test_skip_if_exists_conflict_message(self, _mock_run):
        result = run_command(
            ["gcloud", "create", "thing"],
            step=1, total=1,
            title="Create", description="Creates a thing",
            skip_if_exists=True,
        )
        assert result.returncode == 1

    @patch("init_repo.subprocess.run", return_value=_fail("PERMISSION_DENIED"))
    def test_skip_if_exists_still_raises_on_other_errors(self, _mock_run):
        with pytest.raises(typer.Exit):
            run_command(
                ["gcloud", "create", "thing"],
                step=1, total=1,
                title="Create", description="Creates a thing",
                skip_if_exists=True,
            )


# ---------------------------------------------------------------------------
# Phase function smoke tests (all subprocess calls mocked)
# ---------------------------------------------------------------------------

class TestSetupGithub:
    @patch("init_repo.subprocess.run")
    def test_creates_repo_when_not_exists(self, mock_run):
        def side_effect(cmd, **kwargs):
            if "repo" in cmd and "view" in cmd:
                return _fail("not found")
            return _ok()

        mock_run.side_effect = side_effect
        setup_github("owner", "repo", "Ruritania")
        # view check + create + pages env + deployment branch = 4
        assert mock_run.call_count == 4
        create_call_args = mock_run.call_args_list[1][0][0]
        assert any("TEMPLATE-rle-assessment" in arg for arg in create_call_args)

    @patch("init_repo.subprocess.run", return_value=_ok())
    def test_skips_creation_when_exists(self, mock_run):
        setup_github("owner", "repo", "Ruritania")
        # view check + pages env + deployment branch = 3 (no create)
        assert mock_run.call_count == 3
        for call in mock_run.call_args_list[1:]:
            call_args = call[0][0]
            assert "repo" not in call_args or "create" not in call_args


class TestSetupGcpOwn:
    @patch("init_repo.time.sleep")
    @patch("init_repo.subprocess.run")
    def test_creates_project_and_returns_number(self, mock_run, _mock_sleep):
        def side_effect(cmd, **kwargs):
            if "describe" in cmd:
                return _ok(stdout="123456789\n")
            return _ok()

        mock_run.side_effect = side_effect
        number = _setup_gcp_own("proj-id", "Proj Name", "owner", "repo")
        assert number == "123456789"

    @patch("init_repo.time.sleep")
    @patch("init_repo.subprocess.run")
    def test_skips_creation_when_project_exists(self, mock_run, _mock_sleep):
        def side_effect(cmd, **kwargs):
            if "describe" in cmd and "--format=value(projectId)" in cmd:
                return _ok(stdout="proj-id\n")
            if "describe" in cmd and "--format=value(projectNumber)" in cmd:
                return _ok(stdout="123456789\n")
            return _ok()

        mock_run.side_effect = side_effect
        number = _setup_gcp_own("proj-id", "Proj Name", "owner", "repo")
        assert number == "123456789"
        # Should NOT have a "projects create" call
        for call in mock_run.call_args_list:
            call_args = call[0][0]
            assert "create" not in call_args or "projects" not in call_args


class TestSetupSecrets:
    @patch("init_repo.subprocess.run", return_value=_ok())
    def test_sets_two_secrets(self, mock_run):
        setup_secrets("owner", "repo", "proj-id", "123")
        assert mock_run.call_count == 2
