import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

# Add scripts/ to path so we can import init_repo as a module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import init_repo
from init_repo import (
    _get_gh_username,
    _setup_gcp_own,
    app,
    check_prerequisites,
    run_command,
    setup_github,
    setup_secrets,
)

runner = CliRunner()


@pytest.fixture(autouse=True)
def auto_confirm():
    """Skip confirmation prompts in all tests."""
    init_repo.AUTO_CONFIRM = True
    yield
    init_repo.AUTO_CONFIRM = False


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
# _get_gh_username
# ---------------------------------------------------------------------------

class TestGetGhUsername:
    @patch("init_repo.subprocess.run", return_value=_ok(stdout="tylere\n"))
    def test_returns_username(self, _mock_run):
        assert _get_gh_username() == "tylere"

    @patch("init_repo.subprocess.run", return_value=_fail())
    def test_exits_on_failure(self, _mock_run):
        with pytest.raises(typer.Exit):
            _get_gh_username()


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

    @patch("init_repo.time.sleep")
    @patch("init_repo.subprocess.run")
    def test_retry_on_permission_denied_succeeds(self, mock_run, _mock_sleep):
        mock_run.side_effect = [_fail("PERMISSION_DENIED"), _ok()]
        result = run_command(
            ["gcloud", "do", "thing"],
            step=1, total=1,
            title="Retry", description="Retries on permission denied",
            retries=2,
        )
        assert result.returncode == 0
        assert mock_run.call_count == 2
        _mock_sleep.assert_called_once_with(30)

    @patch("init_repo.time.sleep")
    @patch("init_repo.subprocess.run")
    def test_retry_on_does_not_exist_succeeds(self, mock_run, _mock_sleep):
        mock_run.side_effect = [
            _fail("INVALID_ARGUMENT: Service account sa@proj.iam.gserviceaccount.com does not exist."),
            _ok(),
        ]
        result = run_command(
            ["gcloud", "do", "thing"],
            step=1, total=1,
            title="Retry", description="Retries on does not exist",
            retries=2,
        )
        assert result.returncode == 0
        assert mock_run.call_count == 2
        _mock_sleep.assert_called_once_with(30)

    @patch("init_repo.time.sleep")
    @patch("init_repo.subprocess.run", return_value=_fail("PERMISSION_DENIED"))
    def test_retries_exhausted(self, _mock_run, _mock_sleep):
        with pytest.raises(typer.Exit):
            run_command(
                ["gcloud", "do", "thing"],
                step=1, total=1,
                title="Retry", description="Exhausts retries",
                retries=2,
            )
        assert _mock_run.call_count == 3
        assert _mock_sleep.call_count == 2

    @patch("init_repo.subprocess.run")
    @patch("init_repo.typer.confirm", return_value=False)
    def test_skip_on_declined_confirmation(self, _mock_confirm, mock_run):
        init_repo.AUTO_CONFIRM = False
        with pytest.raises(typer.Exit):
            run_command(
                ["echo", "hello"],
                step=1, total=1,
                title="Test", description="A test command",
            )
        mock_run.assert_not_called()


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
        assert "owner/repo" in create_call_args
        assert any("TEMPLATE-rle-assessment" in arg for arg in create_call_args)

    @patch("init_repo.subprocess.run", return_value=_ok())
    def test_skips_creation_when_exists(self, mock_run):
        setup_github("owner", "repo", "Ruritania")
        # view check + pages env + deployment branch = 3 (no create)
        assert mock_run.call_count == 3
        for call in mock_run.call_args_list[1:]:
            call_args = call[0][0]
            assert "repo" not in call_args or "create" not in call_args


_PROJECT_JSON = json.dumps({
    "projectId": "proj-id",
    "name": "Proj Name",
    "projectNumber": "123456789",
    "lifecycleState": "ACTIVE",
})

def _existing_project_side_effect(cmd, **kwargs):
    """Side effect for an existing GCP project with full permissions."""
    if "describe" in cmd and "--format=value(projectId)" in cmd:
        return _ok(stdout="proj-id\n")
    if "describe" in cmd and "--format=json" in cmd:
        return _ok(stdout=_PROJECT_JSON)
    if "describe" in cmd and "--format=value(projectNumber)" in cmd:
        return _ok(stdout="123456789\n")
    if "auth" in cmd and "list" in cmd:
        return _ok(stdout="user@example.com\n")
    if "get-iam-policy" in cmd:
        return _ok(stdout="roles/owner\n")
    return _ok()


class TestSetupGcpOwn:
    @patch("init_repo.typer.confirm", return_value=True)
    @patch("init_repo.time.sleep")
    @patch("init_repo.subprocess.run")
    def test_creates_project_and_returns_number(
        self, mock_run, _mock_sleep, _mock_confirm
    ):
        def side_effect(cmd, **kwargs):
            if "describe" in cmd:
                return _ok(stdout="123456789\n")
            return _ok()

        mock_run.side_effect = side_effect
        number = _setup_gcp_own("proj-id", "Proj Name", "owner", "repo")
        assert number == "123456789"

    @patch("init_repo.typer.confirm", return_value=True)
    @patch("init_repo.time.sleep")
    @patch("init_repo.subprocess.run")
    def test_skips_creation_when_project_exists(
        self, mock_run, _mock_sleep, _mock_confirm
    ):
        mock_run.side_effect = _existing_project_side_effect
        number = _setup_gcp_own("proj-id", "Proj Name", "owner", "repo")
        assert number == "123456789"
        # Should NOT have a "projects create" call
        for call in mock_run.call_args_list:
            call_args = call[0][0]
            assert "create" not in call_args or "projects" not in call_args

    @patch("init_repo.typer.confirm", return_value=True)
    @patch("init_repo.time.sleep")
    @patch("init_repo.subprocess.run")
    def test_existing_project_prints_info(
        self, mock_run, _mock_sleep, _mock_confirm
    ):
        mock_run.side_effect = _existing_project_side_effect
        _setup_gcp_own("proj-id", None, "owner", "repo")
        # Verify describe --format=json was called
        json_calls = [
            c for c in mock_run.call_args_list
            if "describe" in c[0][0] and "--format=json" in c[0][0]
        ]
        assert len(json_calls) == 1

    @patch("init_repo.typer.confirm", return_value=True)
    @patch("init_repo.time.sleep")
    @patch("init_repo.subprocess.run")
    def test_existing_project_checks_permissions(
        self, mock_run, _mock_sleep, _mock_confirm
    ):
        mock_run.side_effect = _existing_project_side_effect
        _setup_gcp_own("proj-id", None, "owner", "repo")
        # Verify get-iam-policy was called
        perm_calls = [
            c for c in mock_run.call_args_list
            if "get-iam-policy" in c[0][0]
        ]
        assert len(perm_calls) == 1

    @patch("init_repo.typer.confirm", return_value=True)
    @patch("init_repo.time.sleep")
    @patch("init_repo.subprocess.run")
    def test_existing_project_requires_confirmation_even_with_auto_confirm(
        self, mock_run, _mock_sleep, mock_confirm
    ):
        """typer.confirm must be called even when AUTO_CONFIRM is True."""
        mock_run.side_effect = _existing_project_side_effect
        init_repo.AUTO_CONFIRM = True
        _setup_gcp_own("proj-id", None, "owner", "repo")
        # Called twice: once for existing project confirmation, once for EE registration
        assert mock_confirm.call_count >= 1
        # First call should be the existing project confirmation
        first_call_args = mock_confirm.call_args_list[0][0][0]
        assert "existing project" in first_call_args.lower() or "Use existing" in first_call_args

    @patch("init_repo.typer.confirm", return_value=False)
    @patch("init_repo.time.sleep")
    @patch("init_repo.subprocess.run")
    def test_existing_project_denied_exits(
        self, mock_run, _mock_sleep, _mock_confirm
    ):
        mock_run.side_effect = _existing_project_side_effect
        with pytest.raises(typer.Exit):
            _setup_gcp_own("proj-id", None, "owner", "repo")

    @patch("init_repo.time.sleep")
    @patch("init_repo.subprocess.run")
    def test_existing_project_no_permissions_exits(self, mock_run, _mock_sleep):
        def side_effect(cmd, **kwargs):
            if "describe" in cmd and "--format=value(projectId)" in cmd:
                return _ok(stdout="proj-id\n")
            if "describe" in cmd and "--format=json" in cmd:
                return _ok(stdout=_PROJECT_JSON)
            if "auth" in cmd and "list" in cmd:
                return _ok(stdout="user@example.com\n")
            if "get-iam-policy" in cmd:
                return _ok(stdout="\n")
            return _ok()

        mock_run.side_effect = side_effect
        with pytest.raises(typer.Exit):
            _setup_gcp_own("proj-id", None, "owner", "repo")

    @patch("init_repo.typer.confirm", return_value=True)
    @patch("init_repo.typer.prompt", return_value="New Project Name")
    @patch("init_repo.time.sleep")
    @patch("init_repo.subprocess.run")
    def test_new_project_prompts_for_name_if_missing(
        self, mock_run, _mock_sleep, mock_prompt, _mock_confirm
    ):
        def side_effect(cmd, **kwargs):
            # Project does not exist
            if "describe" in cmd and "--format=value(projectId)" in cmd:
                return _fail("NOT_FOUND")
            if "describe" in cmd and "--format=value(projectNumber)" in cmd:
                return _ok(stdout="123456789\n")
            return _ok()

        mock_run.side_effect = side_effect
        number = _setup_gcp_own("proj-id", None, "owner", "repo")
        assert number == "123456789"
        mock_prompt.assert_called_once_with("GCP project display name")



class TestSetupSecrets:
    @patch("init_repo.subprocess.run", return_value=_ok())
    def test_sets_three_secrets(self, mock_run):
        setup_secrets("owner", "repo", "proj-id", "123")
        assert mock_run.call_count == 3
