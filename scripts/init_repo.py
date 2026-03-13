"""Initialize a new RLE assessment repository.

Automates the multi-step process of creating a GitHub repository from the
RLE assessment template, provisioning a Google Cloud Platform project,
configuring Workload Identity Federation, and wiring up GitHub secrets.

Usage:
    python scripts/init_repo.py --help
"""

# /// script
# requires-python = ">=3.11"
# dependencies = ["typer>=0.20", "rich>=13"]
# ///

import base64
import json
import os
import subprocess
import shutil
import time
from datetime import date

import typer
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule

console = Console()

SA_NAME = "github-actions"
TEMPLATE_REPO = "RLE-Assessment/TEMPLATE-rle-assessment"
AUTO_CONFIRM = False
MIN_DISK_GB = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt_bytes(n: int) -> str:
    """Human-readable size string (e.g. '2.1 GB', '384 MB')."""
    if n >= 1024**3:
        return f"{n / 1024**3:.1f} GB"
    if n >= 1024**2:
        return f"{n / 1024**2:.0f} MB"
    return f"{n / 1024:.0f} KB"


def check_disk_space() -> None:
    """Check that enough disk space is available (>= MIN_DISK_GB).

    If space is low, scans the home directory for git repositories that may
    be consuming disk and suggests removing them.
    """
    home = os.path.expanduser("~")
    usage = shutil.disk_usage(home)
    avail_gb = usage.free / (1024**3)

    if avail_gb >= MIN_DISK_GB:
        console.print(f"  [green]Disk space OK ({_fmt_bytes(usage.free)} available)[/green]")
        return

    # Find git repos in the home directory (depth-limited to 2 levels)
    git_repos: list[tuple[str, str]] = []
    for entry in os.scandir(home):
        if not entry.is_dir(follow_symlinks=False):
            continue
        # Check if top-level dir is itself a repo
        if os.path.isdir(os.path.join(entry.path, ".git")):
            size = subprocess.run(
                ["du", "-sh", entry.path],
                capture_output=True, text=True,
            )
            size_str = size.stdout.split()[0] if size.returncode == 0 else "?"
            git_repos.append((entry.path, size_str))
        else:
            # Check one level deeper
            try:
                for sub in os.scandir(entry.path):
                    if sub.is_dir(follow_symlinks=False) and os.path.isdir(
                        os.path.join(sub.path, ".git")
                    ):
                        size = subprocess.run(
                            ["du", "-sh", sub.path],
                            capture_output=True, text=True,
                        )
                        size_str = size.stdout.split()[0] if size.returncode == 0 else "?"
                        git_repos.append((sub.path, size_str))
            except PermissionError:
                continue

    msg = (
        f"[bold red]Low disk space: {_fmt_bytes(usage.free)} available "
        f"(need at least {MIN_DISK_GB} GB).[/bold red]\n"
    )

    if git_repos:
        msg += "\nThe following git repositories were found in your home directory:\n"
        for path, size in git_repos:
            msg += f"\n  [bold]{size}[/bold]  {path}"
        msg += (
            "\n\nConsider removing unneeded repositories to free up space, e.g.:\n"
            f"  [bold]rm -rf {git_repos[0][0]}[/bold]"
        )
    else:
        msg += "\nFree up disk space and try again."

    console.print(Panel(msg, title="Insufficient disk space"))
    raise typer.Exit(code=1)


def _step_header(step: int, total: int, title: str) -> None:
    """Print a Rich rule with the step counter."""
    console.print()
    console.print(Rule(f"[bold]Step {step} of {total}[/bold]  {title}"))


def _describe(text: str) -> None:
    """Print a description paragraph."""
    console.print(f"\n  The following command {text}\n")


def _show_command(cmd: list[str]) -> None:
    """Display the command that is about to run."""
    cmd_str = " ".join(cmd)
    console.print(Panel(cmd_str, style="reverse", border_style="dim", padding=(0, 1)))


def _is_already_exists_error(stderr: str) -> bool:
    """Check if a gcloud error indicates the resource already exists."""
    return "ALREADY_EXISTS" in stderr or "already exists" in stderr


def _is_project_id_in_use_error(stderr: str) -> bool:
    """Check if a gcloud error indicates the project ID is in use."""
    return "already in use by another project" in stderr


def _is_retryable_error(stderr: str) -> bool:
    """Check if a gcloud error is likely caused by propagation delay."""
    return "PERMISSION_DENIED" in stderr or "does not exist" in stderr


def run_command(
    cmd: list[str],
    *,
    step: int,
    total: int,
    title: str,
    description: str,
    capture: bool = False,
    input_data: str | None = None,
    skip_if_exists: bool = False,
    retries: int = 0,
    cwd: str | None = None,
) -> subprocess.CompletedProcess:
    """Run a shell command with Rich output describing what it does and why.

    Parameters
    ----------
    cmd : list[str]
        The command and arguments to execute.
    step, total : int
        Step counter for the header (e.g. "Step 3 of 12").
    title : str
        Short title for the step.
    description : str
        Detailed explanation of *why* this command is being run.
    capture : bool
        If True, capture stdout (useful when the output is needed later).
    input_data : str | None
        Optional stdin data to pass to the process.
    skip_if_exists : bool
        If True, treat ALREADY_EXISTS errors as a skip instead of a failure.
    retries : int
        Number of times to retry on transient errors such as PERMISSION_DENIED
        or "does not exist" (with a 30-second wait between attempts).  Useful
        after IAM or resource changes that need time to propagate.
    cwd : str | None
        Working directory for the command.

    Returns
    -------
    subprocess.CompletedProcess
    """
    _step_header(step, total, title)
    _describe(description)
    _show_command(cmd)

    if not AUTO_CONFIRM:
        if not typer.confirm("  Run this command?", default=True):
            console.print("  [dim]Skipped.[/dim]")
            raise typer.Exit(code=0)

    need_capture = capture or skip_if_exists or retries > 0
    attempts = 1 + retries

    for attempt in range(1, attempts + 1):
        console.print("  [dim]Running...[/dim]")
        result = subprocess.run(
            cmd,
            capture_output=need_capture,
            text=True,
            input=input_data,
            cwd=cwd,
        )

        if result.returncode == 0:
            console.print("  [green]Done[/green]")
            if capture and result.stdout.strip():
                console.print(f"  [dim]{result.stdout.strip()}[/dim]")
            return result

        stderr = result.stderr or ""

        if skip_if_exists and _is_already_exists_error(stderr):
            console.print("  [yellow]Already exists — skipping.[/yellow]")
            return result

        if retries > 0 and _is_retryable_error(stderr) and attempt < attempts:
            console.print(
                f"  [yellow]Transient error — waiting 30 seconds for "
                f"propagation (attempt {attempt}/{attempts})...[/yellow]"
            )
            time.sleep(30)
            continue

        console.print("  [red]Failed[/red]")
        if stderr.strip():
            console.print(Panel(stderr.strip(), title="Error", border_style="red"))
        raise typer.Exit(code=1)

    return result


# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------


def check_prerequisites(need_gh: bool = True, need_gcloud: bool = True, need_pixi: bool = False) -> None:
    """Verify that required CLIs are installed and authenticated.

    If a tool is missing or the user is not logged in, prints clear
    instructions and exits rather than launching interactive login flows.
    """
    if need_gh:
        if shutil.which("gh") is None:
            console.print(
                Panel(
                    "[bold red]GitHub CLI (gh) is not installed.[/bold red]\n\n"
                    "Install it from: https://cli.github.com\n"
                    "Then run: [bold]gh auth login[/bold]",
                    title="Missing prerequisite",
                )
            )
            raise typer.Exit(code=1)

        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(
                Panel(
                    "[bold red]Not authenticated to GitHub.[/bold red]\n\n"
                    "Run the following command and follow the prompts:\n"
                    "  [bold]gh auth login[/bold]",
                    title="Authentication required",
                )
            )
            raise typer.Exit(code=1)
        console.print("  [green]GitHub CLI authenticated[/green]")

    if need_gcloud:
        if shutil.which("gcloud") is None:
            console.print(
                Panel(
                    "[bold red]Google Cloud CLI (gcloud) is not installed.[/bold red]\n\n"
                    "Install it from: https://cloud.google.com/sdk/docs/install\n"
                    "Then run: [bold]gcloud auth login[/bold]",
                    title="Missing prerequisite",
                )
            )
            raise typer.Exit(code=1)

        # Try to get the active account name for display
        acct = subprocess.run(
            [
                "gcloud",
                "auth",
                "list",
                "--filter=status:ACTIVE",
                "--format=value(account)",
            ],
            capture_output=True,
            text=True,
        )
        # Fall back to checking if gcloud can produce an access token
        # (works in Cloud Shell where auth list may not show an account)
        if acct.returncode != 0 or not acct.stdout.strip():
            token = subprocess.run(
                ["gcloud", "auth", "print-access-token"],
                capture_output=True,
                text=True,
            )
            if token.returncode != 0:
                in_cloud_shell = os.environ.get("CLOUD_SHELL") == "true" or os.environ.get("DEVSHELL_PROJECT_ID")
                if in_cloud_shell:
                    console.print(
                        Panel(
                            "[bold red]Google Cloud Shell is not authorized.[/bold red]\n\n"
                            "Cloud Shell requires you to explicitly grant the gcloud CLI\n"
                            "access to your Google account. Run:\n\n"
                            "  [bold]gcloud auth login[/bold]\n\n"
                            "Cloud Shell may say 'You are already authenticated' and ask\n"
                            "'Do you wish to proceed anyway?' — answer [bold]Yes[/bold].\n"
                            "Complete the sign-in flow, then re-run this script.",
                            title="Authorization required",
                        )
                    )
                else:
                    console.print(
                        Panel(
                            "[bold red]Not authenticated to Google Cloud.[/bold red]\n\n"
                            "The gcloud CLI needs explicit consent to access your\n"
                            "Google account. Run the following command, which will\n"
                            "open a browser for you to sign in and grant access:\n\n"
                            "  [bold]gcloud auth login[/bold]",
                            title="Authentication required",
                        )
                    )
                raise typer.Exit(code=1)
            console.print("  [green]Google Cloud CLI authenticated[/green]")
        else:
            # Verify tokens are still valid (auth list reads local config
            # and succeeds even with expired tokens)
            token = subprocess.run(
                ["gcloud", "auth", "print-access-token"],
                capture_output=True,
                text=True,
            )
            if token.returncode != 0:
                console.print(
                    Panel(
                        "[bold red]Google Cloud auth tokens have expired.[/bold red]\n\n"
                        "Run the following command to refresh your credentials:\n\n"
                        "  [bold]gcloud auth login[/bold]",
                        title="Reauthentication required",
                    )
                )
                raise typer.Exit(code=1)
            console.print(
                f"  [green]Google Cloud CLI authenticated as {acct.stdout.strip()}[/green]"
            )

    if need_pixi:
        if shutil.which("pixi") is None:
            console.print(
                Panel(
                    "[bold red]pixi is not installed.[/bold red]\n\n"
                    "Install it from: https://pixi.sh\n"
                    "  [bold]curl -fsSL https://pixi.sh/install.sh | sh[/bold]",
                    title="Missing prerequisite",
                )
            )
            raise typer.Exit(code=1)
        console.print("  [green]pixi installed[/green]")


def _get_gh_username() -> str:
    """Return the authenticated GitHub username."""
    result = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        console.print("[red]Could not determine GitHub username.[/red]")
        raise typer.Exit(code=1)
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Phase 1 – GitHub repository
# ---------------------------------------------------------------------------


def customize_pyproject(
    gh_owner: str,
    gh_repo_name: str,
    gcp_project_id: str,
    step: int,
    total: int,
    ecosystem_gee_asset_id: str | None = None,
) -> None:
    """Replace template placeholders in pyproject.toml and config files."""
    _step_header(step, total, "Customize project config")
    _describe(
        "updates pyproject.toml and config/country_config.yaml in the new "
        "repository, replacing the template project name and GCP project ID "
        "placeholder."
    )

    repo = f"{gh_owner}/{gh_repo_name}"

    replacements = {
        "TEMPLATE-rle-assessment": gh_repo_name,
        "PLACEHOLDER_GCP_PROJECT_ID": gcp_project_id,
    }
    if ecosystem_gee_asset_id is not None:
        replacements["projects/goog-rle-assessments/assets/ruritania/ruritania_ecosystems"] = ecosystem_gee_asset_id

    file_paths = ["pyproject.toml", "config/country_config.yaml", "docs/GCP_SETUP.md"]

    console.print(f"  Replacements:")
    for old, new in replacements.items():
        console.print(f"    {old}  →  {new}")
    console.print(f"  Files: {', '.join(file_paths)}")

    if not AUTO_CONFIRM:
        if not typer.confirm("\n  Apply this change?", default=True):
            console.print("  [dim]Skipped.[/dim]")
            raise typer.Exit(code=0)

    for file_path in file_paths:
        # Fetch the file via GitHub API (with retries for template propagation)
        console.print(f"  [dim]Fetching {file_path}...[/dim]")
        max_attempts = 6
        for attempt in range(1, max_attempts + 1):
            result = subprocess.run(
                ["gh", "api", f"repos/{repo}/contents/{file_path}"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                break
            if attempt < max_attempts:
                console.print(
                    f"  [yellow]File not yet available — waiting 10 seconds "
                    f"(attempt {attempt}/{max_attempts})...[/yellow]"
                )
                time.sleep(10)

        if result.returncode != 0:
            console.print(f"  [red]Failed to fetch {file_path}[/red]")
            if result.stderr and result.stderr.strip():
                console.print(Panel(result.stderr.strip(), title="Error", border_style="red"))
            raise typer.Exit(code=1)

        data = json.loads(result.stdout)
        file_sha = data["sha"]
        content = base64.b64decode(data["content"]).decode("utf-8")

        # Perform replacements
        new_content = content
        for old, new in replacements.items():
            new_content = new_content.replace(old, new)

        if new_content == content:
            console.print(f"  [yellow]No placeholders found in {file_path} — skipping.[/yellow]")
            continue

        # Push the updated file
        new_content_b64 = base64.b64encode(new_content.encode("utf-8")).decode("ascii")
        update_payload = json.dumps({
            "message": f"Configure {gh_repo_name} for {gcp_project_id}",
            "content": new_content_b64,
            "sha": file_sha,
        })

        console.print(f"  [dim]Updating {file_path}...[/dim]")
        result = subprocess.run(
            [
                "gh", "api",
                f"repos/{repo}/contents/{file_path}",
                "-X", "PUT",
                "--input", "-",
            ],
            capture_output=True,
            text=True,
            input=update_payload,
        )
        if result.returncode != 0:
            console.print(f"  [red]Failed to update {file_path}[/red]")
            if result.stderr and result.stderr.strip():
                console.print(Panel(result.stderr.strip(), title="Error", border_style="red"))
            raise typer.Exit(code=1)

        console.print(f"  [green]Done — {file_path} customized[/green]")


def customize_quarto_config(
    gh_owner: str,
    gh_repo_name: str,
    country_name: str,
    step: int,
    total: int,
) -> None:
    """Replace template placeholders in _quarto.yml with actual values."""
    _step_header(step, total, "Customize _quarto.yml")
    _describe(
        "updates the _quarto.yml configuration file in the new repository, "
        "replacing template placeholders with the country name, current year, "
        "and today's date."
    )

    repo = f"{gh_owner}/{gh_repo_name}"
    today = date.today()

    replacements = {
        "Ruritania": country_name,
        "year: 2000": f"year: {today.year}",
        'date: "2000-01-01"': f'date: "{today.isoformat()}"',
    }
    console.print("  Replacements:")
    for old, new in replacements.items():
        console.print(f"    {old}  →  {new}")

    if not AUTO_CONFIRM:
        if not typer.confirm("\n  Apply these changes?", default=True):
            console.print("  [dim]Skipped.[/dim]")
            raise typer.Exit(code=0)

    # Fetch the file via GitHub API (with retries for template propagation)
    console.print("  [dim]Fetching _quarto.yml...[/dim]")
    max_attempts = 6
    for attempt in range(1, max_attempts + 1):
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/contents/_quarto.yml"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            break
        if attempt < max_attempts:
            console.print(
                f"  [yellow]File not yet available — waiting 10 seconds "
                f"(attempt {attempt}/{max_attempts})...[/yellow]"
            )
            time.sleep(10)

    if result.returncode != 0:
        console.print("  [red]Failed to fetch _quarto.yml[/red]")
        if result.stderr and result.stderr.strip():
            console.print(Panel(result.stderr.strip(), title="Error", border_style="red"))
        raise typer.Exit(code=1)

    data = json.loads(result.stdout)
    file_sha = data["sha"]
    content = base64.b64decode(data["content"]).decode("utf-8")

    # Perform replacements
    new_content = content
    for old, new in replacements.items():
        new_content = new_content.replace(old, new)

    if new_content == content:
        console.print("  [yellow]No placeholders found — skipping.[/yellow]")
        return

    # Push the updated file
    new_content_b64 = base64.b64encode(new_content.encode("utf-8")).decode("ascii")
    update_payload = json.dumps({
        "message": f"Configure assessment for {country_name}",
        "content": new_content_b64,
        "sha": file_sha,
    })

    console.print("  [dim]Updating _quarto.yml...[/dim]")
    result = subprocess.run(
        [
            "gh", "api",
            f"repos/{repo}/contents/_quarto.yml",
            "-X", "PUT",
            "--input", "-",
        ],
        capture_output=True,
        text=True,
        input=update_payload,
    )
    if result.returncode != 0:
        console.print("  [red]Failed to update _quarto.yml[/red]")
        if result.stderr and result.stderr.strip():
            console.print(Panel(result.stderr.strip(), title="Error", border_style="red"))
        raise typer.Exit(code=1)

    console.print("  [green]Done — _quarto.yml customized[/green]")


def setup_github(
    gh_owner: str,
    gh_repo_name: str,
    country_name: str,
    gcp_project_id: str,
    step_offset: int,
    total: int,
    ecosystem_gee_asset_id: str | None = None,
) -> None:
    """Create the GitHub repository and configure GitHub Pages deployment."""

    repo_full = f"{gh_owner}/{gh_repo_name}"

    # Check if repository already exists
    check = subprocess.run(
        ["gh", "repo", "view", repo_full, "--json", "name"],
        capture_output=True,
        text=True,
    )
    if check.returncode == 0:
        _step_header(step_offset + 1, total, "Create GitHub Repository")
        console.print(
            f"\n  [yellow]Repository {repo_full} already exists — skipping creation.[/yellow]\n"
        )
    else:
        run_command(
            [
                "gh",
                "repo",
                "create",
                repo_full,
                f"--template={TEMPLATE_REPO}",
                f"--description=An IUCN Red List of Ecosystems assessment for {country_name}",
                "--public",
                "--include-all-branches",
            ],
            step=step_offset + 1,
            total=total,
            title="Create GitHub Repository",
            description="creates a new public GitHub repository from the RLE assessment template. The template includes the Quarto project structure, GitHub Actions deploy workflow, country configuration files, and all report chapter scaffolding.",
        )

    run_command(
        [
            "gh",
            "api",
            f"repos/{gh_owner}/{gh_repo_name}/environments/github-pages",
            "-X",
            "PUT",
            "--input",
            "-",
        ],
        step=step_offset + 2,
        total=total,
        title="Create GitHub Pages Environment",
        description="configures the repository's 'github-pages' deployment environment with a custom branch policy. This allows GitHub Actions to deploy rendered content from specific branches (like main) to GitHub Pages, rather than only from protected branches.",
        input_data=json.dumps(
            {
                "deployment_branch_policy": {
                    "protected_branches": False,
                    "custom_branch_policies": True,
                }
            }
        ),
    )

    run_command(
        [
            "gh",
            "api",
            f"repos/{gh_owner}/{gh_repo_name}/environments/github-pages/deployment-branch-policies",
            "-X",
            "POST",
            "-f",
            "name=main",
            "-f",
            "type=branch",
        ],
        step=step_offset + 3,
        total=total,
        title="Add 'main' as Deployment Branch",
        description="adds the 'main' branch to the list of branches allowed to deploy to the github-pages environment. Without this, the GitHub Actions deploy workflow would be blocked from publishing the rendered Quarto site.",
        skip_if_exists=True,
    )

    customize_pyproject(
        gh_owner,
        gh_repo_name,
        gcp_project_id,
        step=step_offset + 4,
        total=total,
        ecosystem_gee_asset_id=ecosystem_gee_asset_id,
    )

    customize_quarto_config(
        gh_owner,
        gh_repo_name,
        country_name,
        step=step_offset + 5,
        total=total,
    )

    repo_url = f"https://github.com/{gh_owner}/{gh_repo_name}"
    console.print(f"\n  [bold green]GitHub repository ready:[/bold green] {repo_url}")


# ---------------------------------------------------------------------------
# Phase 2 – GCP project
# ---------------------------------------------------------------------------


def _setup_gcp_own(
    gcp_project_id: str,
    gcp_project_name: str | None,
    gh_owner: str,
    gh_repo_name: str,
    step_offset: int,
    total: int,
) -> str:
    """Create or reuse a GCP project with full Workload Identity Federation.

    Returns the GCP project number (needed for secrets).
    """

    console.print(
        Panel(
            "[dim]Google Cloud may prompt you to reauthenticate for privileged\n"
            "operations like creating projects. This is a normal security\n"
            "measure — enter your password if prompted.[/dim]",
            title="Note",
            border_style="dim",
        )
    )

    check = subprocess.run(
        ["gcloud", "projects", "describe", gcp_project_id, "--format=value(projectId)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    project_exists = check.returncode == 0 and check.stdout.strip() == gcp_project_id

    if not project_exists and check.returncode != 0 and check.stderr:
        stderr_lower = check.stderr.lower()
        if "not found" not in stderr_lower and "not exist" not in stderr_lower:
            console.print(
                Panel(
                    f"Could not verify whether project [bold]{gcp_project_id}[/bold] exists.\n\n"
                    f"{check.stderr.strip()}\n\n"
                    f"Fix the issue above and re-run, or continue to create a new project.",
                    title="Warning",
                    border_style="yellow",
                )
            )

    if project_exists:
        # --- Existing project: show info, check permissions, confirm ------
        _step_header(step_offset + 1, total, "Use Existing GCP Project")

        # Print project details
        info = subprocess.run(
            ["gcloud", "projects", "describe", gcp_project_id, "--format=json"],
            capture_output=True,
            text=True,
        )
        if info.returncode == 0 and info.stdout.strip():
            project_info = json.loads(info.stdout)
            details = (
                f"  Name:       {project_info.get('name', 'N/A')}\n"
                f"  Project ID: {project_info.get('projectId', 'N/A')}\n"
                f"  Number:     {project_info.get('projectNumber', 'N/A')}\n"
                f"  State:      {project_info.get('lifecycleState', 'N/A')}"
            )
            if project_info.get("labels"):
                labels = ", ".join(
                    f"{k}={v}" for k, v in project_info["labels"].items()
                )
                details += f"\n  Labels:     {labels}"
            console.print(
                Panel(details, title="Existing GCP Project", border_style="yellow")
            )

            if project_info.get("lifecycleState") == "DELETE_REQUESTED":
                console.print(
                    Panel(
                        f"This project is pending deletion and cannot be used.\n"
                        f"GCP reserves deleted project IDs for up to 30 days.\n\n"
                        f"To restore it, run:\n"
                        f"  [bold]gcloud projects undelete {gcp_project_id}[/bold]\n\n"
                        f"Then re-run this script.",
                        title="Project Pending Deletion",
                        border_style="red",
                    )
                )
                raise typer.Exit(code=1)

        # Check user permissions via IAM policy
        acct_result = subprocess.run(
            [
                "gcloud",
                "auth",
                "list",
                "--filter=status:ACTIVE",
                "--format=value(account)",
            ],
            capture_output=True,
            text=True,
        )
        active_account = acct_result.stdout.strip()

        perms = subprocess.run(
            [
                "gcloud",
                "projects",
                "get-iam-policy",
                gcp_project_id,
                "--flatten=bindings[].members",
                f"--filter=bindings.members:user:{active_account}",
                "--format=value(bindings.role)",
            ],
            capture_output=True,
            text=True,
        )
        if perms.returncode != 0:
            console.print(
                "\n  [red]Could not verify permissions on this project.[/red]\n"
            )
            if perms.stderr and perms.stderr.strip():
                console.print(
                    Panel(
                        perms.stderr.strip(),
                        title="Error",
                        border_style="red",
                    )
                )
            raise typer.Exit(code=1)

        roles = [r for r in perms.stdout.strip().splitlines() if r]
        if not roles:
            console.print(
                f"\n  [red]{active_account} has no IAM roles on "
                f"project {gcp_project_id}.[/red]\n"
            )
            raise typer.Exit(code=1)

        role_list = ", ".join(roles)
        console.print(
            f"\n  [green]Verified:[/green] {active_account} has roles: {role_list}\n"
        )

        # Always require explicit confirmation (ignore AUTO_CONFIRM)
        if not typer.confirm(f"  Use existing project {gcp_project_id}?", default=True):
            console.print("  [dim]Aborted.[/dim]")
            raise typer.Exit(code=0)

        subprocess.run(
            ["gcloud", "config", "set", "project", gcp_project_id],
            capture_output=True,
            text=True,
        )
    else:
        # --- New project: require gcp_project_name -----------------------
        if gcp_project_name is None:
            gcp_project_name = typer.prompt("GCP project display name")

        create_cmd = [
            "gcloud",
            "projects",
            "create",
            gcp_project_id,
            f"--name={gcp_project_name}",
            "--set-as-default",
        ]

        _step_header(step_offset + 1, total, "Create GCP Project")
        _describe(
            "creates a new Google Cloud Platform project that will host "
            "the Earth Engine resources and service accounts for this "
            "assessment. The project is set as the default for subsequent "
            "gcloud commands.",
        )
        _show_command(create_cmd)

        if not AUTO_CONFIRM:
            if not typer.confirm("  Run this command?", default=True):
                console.print("  [dim]Skipped.[/dim]")
                raise typer.Exit(code=0)

        console.print("  [dim]Running...[/dim]")
        create_result = subprocess.run(
            create_cmd, capture_output=True, text=True,
        )

        if create_result.returncode != 0:
            stderr = create_result.stderr or ""
            if _is_project_id_in_use_error(stderr):
                console.print(
                    Panel(
                        f"Project ID [bold]{gcp_project_id}[/bold] is already in use.\n\n"
                        f"This usually means a project with this ID was recently\n"
                        f"deleted and is still within the 30-day grace period.\n\n"
                        f"Options:\n"
                        f"  1. Restore the deleted project:\n"
                        f"     [bold]gcloud projects undelete {gcp_project_id}[/bold]\n"
                        f"     Then re-run this script.\n\n"
                        f"  2. Choose a different project ID and re-run this script.",
                        title="Project ID Conflict",
                        border_style="red",
                    )
                )
            else:
                console.print("  [red]Failed[/red]")
                if stderr.strip():
                    console.print(
                        Panel(stderr.strip(), title="Error", border_style="red")
                    )
            raise typer.Exit(code=1)

        console.print("  [green]Done[/green]")

    # Get the current authenticated account for the Owner binding
    acct_result = subprocess.run(
        ["gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
        capture_output=True,
        text=True,
    )
    active_account = acct_result.stdout.strip()

    run_command(
        [
            "gcloud",
            "projects",
            "add-iam-policy-binding",
            gcp_project_id,
            f"--member=user:{active_account}",
            "--role=roles/owner",
        ],
        step=step_offset + 2,
        total=total,
        title="Ensure Owner Permissions",
        description=f"grants the Owner role on the project to {active_account}. This ensures the current user has all permissions needed for subsequent steps (enabling APIs, creating workload identity pools, service accounts, and IAM bindings). The command is idempotent — if the role is already granted, this is a no-op.",
        retries=3,
    )

    apis = [
        (
            "earthengine.googleapis.com",
            "Earth Engine API — provides access to Google Earth Engine for geospatial analysis.",
        ),
        (
            "iamcredentials.googleapis.com",
            "IAM Service Account Credentials API — required for Workload Identity Federation authentication.",
        ),
        (
            "sts.googleapis.com",
            "Security Token Service API — exchanges GitHub's OIDC token for a GCP federated token.",
        ),
        (
            "cloudresourcemanager.googleapis.com",
            "Cloud Resource Manager API — allows gcloud commands to query project metadata.",
        ),
    ]

    for i, (api, reason) in enumerate(apis):
        run_command(
            ["gcloud", "services", "enable", api, f"--project={gcp_project_id}"],
            step=step_offset + 3,
            total=total,
            title=f"Enable API ({i + 1}/{len(apis)})",
            description=f"enables the {reason}",
            retries=3,
        )

    run_command(
        [
            "gcloud",
            "iam",
            "workload-identity-pools",
            "create",
            "github-pool",
            f"--project={gcp_project_id}",
            "--location=global",
            "--display-name=GitHub Actions Pool",
        ],
        step=step_offset + 4,
        total=total,
        title="Create Workload Identity Pool",
        description="creates a workload identity pool, which is a container for external identity providers. This pool allows GitHub Actions to authenticate to GCP without storing long-lived credentials as secrets.",
        skip_if_exists=True,
        retries=3,
    )

    run_command(
        [
            "gcloud",
            "iam",
            "workload-identity-pools",
            "providers",
            "create-oidc",
            "github-provider",
            f"--project={gcp_project_id}",
            "--location=global",
            "--workload-identity-pool=github-pool",
            "--display-name=GitHub Provider",
            "--attribute-mapping=google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner",
            "--attribute-condition=assertion.repository != ''",
            "--issuer-uri=https://token.actions.githubusercontent.com",
        ],
        step=step_offset + 5,
        total=total,
        title="Create OIDC Provider",
        description="creates an OpenID Connect (OIDC) identity provider within the pool. This configures how GitHub Actions OIDC tokens are validated and mapped to GCP identities — the key piece of Workload Identity Federation that eliminates the need for static service account keys.",
        skip_if_exists=True,
        retries=3,
    )

    run_command(
        [
            "gcloud",
            "iam",
            "service-accounts",
            "create",
            SA_NAME,
            f"--project={gcp_project_id}",
            "--display-name=GitHub Actions",
        ],
        step=step_offset + 6,
        total=total,
        title="Create Service Account",
        description="creates a dedicated service account that GitHub Actions will impersonate. This account will be granted only the minimum permissions needed: Earth Engine access and API usage.",
        skip_if_exists=True,
        retries=3,
    )

    sa_email = f"{SA_NAME}@{gcp_project_id}.iam.gserviceaccount.com"

    run_command(
        [
            "gcloud",
            "projects",
            "add-iam-policy-binding",
            gcp_project_id,
            f"--member=serviceAccount:{sa_email}",
            "--role=roles/earthengine.writer",
        ],
        step=step_offset + 7,
        total=total,
        title="Grant IAM Roles (1/2)",
        description="grants the Earth Engine Writer role to the service account, allowing it to read and write Earth Engine assets (images, feature collections, etc.) within this project.",
        retries=3,
    )

    run_command(
        [
            "gcloud",
            "projects",
            "add-iam-policy-binding",
            gcp_project_id,
            f"--member=serviceAccount:{sa_email}",
            "--role=roles/serviceusage.serviceUsageConsumer",
        ],
        step=step_offset + 7,
        total=total,
        title="Grant IAM Roles (2/2)",
        description="grants the Service Usage Consumer role, which allows API calls to be billed to this project. Without this, the service account would not be able to make Earth Engine API requests.",
        retries=3,
    )

    result = run_command(
        [
            "gcloud",
            "projects",
            "describe",
            gcp_project_id,
            "--format=value(projectNumber)",
        ],
        step=step_offset + 8,
        total=total,
        title="Get GCP Project Number",
        description="retrieves the GCP project number (a numeric identifier) needed to construct the Workload Identity Federation principal for the IAM binding.",
        capture=True,
    )
    project_number = result.stdout.strip()

    member = (
        f"principalSet://iam.googleapis.com/"
        f"projects/{project_number}/locations/global/"
        f"workloadIdentityPools/github-pool/"
        f"attribute.repository/{gh_owner}/{gh_repo_name}"
    )

    run_command(
        [
            "gcloud",
            "iam",
            "service-accounts",
            "add-iam-policy-binding",
            sa_email,
            f"--project={gcp_project_id}",
            "--role=roles/iam.workloadIdentityUser",
            f"--member={member}",
        ],
        step=step_offset + 8,
        total=total,
        title="Bind Repository to Service Account",
        description="creates an IAM binding that allows only this specific GitHub repository to impersonate the service account via Workload Identity Federation. This is the final link connecting GitHub Actions to GCP.",
        retries=3,
    )

    # Earth Engine registration
    _step_header(step_offset + 9, total, "Register Project with Earth Engine")

    # Check if already registered
    token_result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True,
        text=True,
    )
    token = token_result.stdout.strip()

    check = subprocess.run(
        [
            "curl",
            "-sf",
            "-H",
            f"Authorization: Bearer {token}",
            "-H",
            f"x-goog-user-project: {gcp_project_id}",
            f"https://earthengine.googleapis.com/v1/projects/{gcp_project_id}/config",
        ],
        capture_output=True,
        text=True,
    )
    if check.returncode == 0:
        console.print(
            "\n  [green]Project is already registered with Earth Engine — skipping.[/green]"
        )
    else:
        ee_url = (
            f"https://console.cloud.google.com/earth-engine/configuration"
            f"?project={gcp_project_id}"
        )
        console.print(
            Panel(
                f"[bold]The project must be registered with Earth Engine.[/bold]\n\n"
                f"Open this URL in your browser and follow the prompts:\n\n"
                f"  [link={ee_url}]{ee_url}[/link]\n\n"
                f"[dim]This step requires accepting the Earth Engine Terms of Service\n"
                f"and cannot be automated.[/dim]",
                border_style="yellow",
            )
        )
        typer.confirm("  Press Enter once registration is complete", default=True)

        # Retry verification to allow for propagation delay
        max_attempts = 6
        for attempt in range(1, max_attempts + 1):
            check = subprocess.run(
                [
                    "curl",
                    "-sf",
                    "-H",
                    f"Authorization: Bearer {token}",
                    "-H",
                    f"x-goog-user-project: {gcp_project_id}",
                    f"https://earthengine.googleapis.com/v1/projects/{gcp_project_id}/config",
                ],
                capture_output=True,
                text=True,
            )
            if check.returncode == 0:
                break
            if attempt < max_attempts:
                console.print(
                    f"  [yellow]Waiting for registration to propagate "
                    f"(attempt {attempt}/{max_attempts})...[/yellow]"
                )
                time.sleep(10)

        if check.returncode != 0:
            console.print(
                "  [red]Project does not appear to be registered with Earth Engine.[/red]"
            )
            console.print(
                "  [red]Without registration, pages that use Earth Engine will not render correctly.[/red]"
            )
            raise typer.Exit(code=1)
        console.print("  [green]Verified: project is registered with Earth Engine.[/green]")

    return project_number


def setup_gcp(
    gcp_project_id: str,
    gcp_project_name: str | None,
    gh_owner: str,
    gh_repo_name: str,
    step_offset: int,
    total: int,
) -> str:
    """Set up GCP infrastructure. Returns the project number."""

    return _setup_gcp_own(
        gcp_project_id,
        gcp_project_name,
        gh_owner,
        gh_repo_name,
        step_offset=step_offset,
        total=total,
    )


# ---------------------------------------------------------------------------
# Phase 3 – GitHub secrets
# ---------------------------------------------------------------------------


def setup_secrets(
    gh_owner: str,
    gh_repo_name: str,
    gcp_project_id: str,
    project_number: str,
    step_offset: int,
    total: int,
) -> None:
    """Set GitHub repository secrets for Workload Identity Federation."""

    wif_provider = (
        f"projects/{project_number}/locations/global/"
        f"workloadIdentityPools/github-pool/providers/github-provider"
    )
    sa_email = f"{SA_NAME}@{gcp_project_id}.iam.gserviceaccount.com"

    repo = f"{gh_owner}/{gh_repo_name}"

    run_command(
        [
            "gh",
            "secret",
            "set",
            "GCP_WORKLOAD_IDENTITY_PROVIDER",
            "--repo",
            repo,
            "--body",
            wif_provider,
        ],
        step=step_offset + 1,
        total=total,
        title="Set GCP_WORKLOAD_IDENTITY_PROVIDER Secret",
        description="stores the full Workload Identity Provider resource path as a GitHub repository secret. The GitHub Actions deploy workflow uses this value to request a federated token from GCP, enabling keyless authentication.",
    )

    run_command(
        [
            "gh",
            "secret",
            "set",
            "GCP_SERVICE_ACCOUNT",
            "--repo",
            repo,
            "--body",
            sa_email,
        ],
        step=step_offset + 2,
        total=total,
        title="Set GCP_SERVICE_ACCOUNT Secret",
        description="stores the service account email as a GitHub repository secret. The deploy workflow uses this to specify which service account to impersonate when authenticating to Earth Engine.",
    )

    run_command(
        [
            "gh",
            "secret",
            "set",
            "GCP_PROJECT_ID",
            "--repo",
            repo,
            "--body",
            gcp_project_id,
        ],
        step=step_offset + 3,
        total=total,
        title="Set GCP_PROJECT_ID Secret",
        description="stores the GCP project ID as a GitHub repository secret. The deploy workflow uses this as the GOOGLE_CLOUD_PROJECT for Earth Engine API calls, ensuring the service account's permissions and billing are applied correctly.",
    )


# ---------------------------------------------------------------------------
# Phase 4 – Local setup
# ---------------------------------------------------------------------------


def setup_local(
    gh_owner: str,
    gh_repo_name: str,
    project_dir: str,
    step_offset: int,
    total: int,
) -> str:
    """Clone the repository and install packages.

    Returns the path to the cloned repository.
    """
    clone_path = os.path.join(project_dir, gh_repo_name)

    if os.path.isdir(clone_path):
        _step_header(step_offset + 1, total, "Clone Repository")
        console.print(
            f"\n  [yellow]Directory {clone_path} already exists — skipping clone.[/yellow]\n"
        )
    else:
        run_command(
            ["gh", "repo", "clone", f"{gh_owner}/{gh_repo_name}", clone_path,
             "--", "--depth", "1"],
            step=step_offset + 1,
            total=total,
            title="Clone Repository",
            description="clones the newly created GitHub repository to your "
            "local machine so you can edit and preview the assessment report.",
        )

    in_cloud_shell = (
        os.environ.get("CLOUD_SHELL") == "true"
        or os.environ.get("DEVSHELL_PROJECT_ID")
    )
    if in_cloud_shell:
        # Proactively clear caches to maximize available space for install.
        for cache_path in [
            "~/.cache/rattler/cache",
            "~/.cache/pip",
        ]:
            full = os.path.expanduser(cache_path)
            if os.path.isdir(full):
                shutil.rmtree(full)
                console.print(f"  [dim]Cleared {cache_path} to free disk space.[/dim]")

        free_gb = shutil.disk_usage(os.path.expanduser("~")).free / (1024**3)
        if free_gb < 1.5:
            console.print(
                Panel(
                    f"[bold yellow]Low disk space: {free_gb:.1f} GB free[/bold yellow]\n\n"
                    "  Cloud Shell has limited storage (~5 GB). The package install\n"
                    "  requires approximately 1.5 GB of free space.\n\n"
                    "  Check for unnecessary files or directories:\n"
                    "    [bold]du -sh ~/* | sort -hr | head -10[/bold]",
                    title="Low Disk Space",
                    border_style="yellow",
                )
            )
            raise typer.Exit(1)

    # On Cloud Shell, redirect the rattler package cache to /tmp so it
    # doesn't consume the limited home-directory storage during install.
    old_cache_dir = os.environ.get("RATTLER_CACHE_DIR")
    if in_cloud_shell:
        os.environ["RATTLER_CACHE_DIR"] = "/tmp/rattler-cache"

    run_command(
        ["pixi", "install"],
        step=step_offset + 2,
        total=total,
        title="Install Packages",
        description="installs the project's dependencies (Python, Quarto, "
        "Jupyter, and geospatial libraries) into an isolated pixi environment.",
        cwd=clone_path,
    )

    if in_cloud_shell:
        # Restore original env and clean up the temporary cache.
        if old_cache_dir is None:
            os.environ.pop("RATTLER_CACHE_DIR", None)
        else:
            os.environ["RATTLER_CACHE_DIR"] = old_cache_dir
        for cache_path in ["/tmp/rattler-cache", os.path.expanduser("~/.cache/rattler/cache")]:
            if os.path.isdir(cache_path):
                shutil.rmtree(cache_path)
        console.print("  [dim]Cleared package cache to save disk space.[/dim]")

    return clone_path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(
    country_name: str = typer.Option(
        ...,
        prompt="Country name",
        help="Name of the country for the assessment (e.g. Ruritania).",
    ),
    gcp_project_id: str = typer.Option(
        ...,
        prompt="GCP project ID",
        help="Google Cloud project ID (e.g. my-rle-project).",
    ),
    gcp_project_name: str | None = typer.Option(
        None,
        help="Human-readable GCP project name (required only when creating a new project).",
    ),
    gh_owner: str | None = typer.Option(
        None,
        help="GitHub user or organization that will own the repository. Defaults to the authenticated user.",
    ),
    gh_repo_name: str = typer.Option(
        ...,
        prompt="GitHub repository name",
        help="Name for the new GitHub repository.",
    ),
    project_dir: str = typer.Option(
        ...,
        prompt="Project directory (where to clone the repository)",
        help="Directory in which to clone the repository.",
    ),
    ecosystem_gee_asset_id: str | None = typer.Option(
        None,
        help="Earth Engine asset ID for the ecosystem map (e.g. projects/my-project/assets/my-ecosystems).",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts."),
) -> None:
    """Initialize a new RLE assessment repository.

    Creates a GitHub repository from the template, provisions a GCP project
    with Workload Identity Federation, configures GitHub secrets, and clones
    the repository locally.
    """
    global AUTO_CONFIRM
    AUTO_CONFIRM = yes

    check_disk_space()
    check_prerequisites(need_gh=True, need_gcloud=True, need_pixi=True)

    # Check if project_dir is inside an existing git repository
    abs_project_dir = os.path.abspath(project_dir)
    check_dir = abs_project_dir
    while check_dir != os.path.dirname(check_dir):  # stop at filesystem root
        if os.path.isdir(os.path.join(check_dir, ".git")):
            console.print(
                Panel(
                    f"[bold red]Project directory is inside an existing git repository.[/bold red]\n\n"
                    f"  Project dir: {abs_project_dir}\n"
                    f"  Git repo:    {check_dir}\n\n"
                    "Cloning a new repository inside an existing one wastes disk space\n"
                    "and causes confusion. Consider removing the old repository first:\n\n"
                    f"  [bold]rm -rf {check_dir}[/bold]\n\n"
                    "Then re-run this script from your home directory:\n\n"
                    "  [bold]cd ~[/bold]",
                    title="Nested git repository",
                )
            )
            raise typer.Exit(code=1)
        check_dir = os.path.dirname(check_dir)

    if gh_owner is None:
        gh_owner = _get_gh_username()
    github_steps = 5
    gcp_steps = 9
    secret_steps = 3
    local_steps = 2
    total = github_steps + gcp_steps + secret_steps + local_steps

    summary = (
        f"[bold]Initializing RLE assessment repository[/bold]\n\n"
        f"  Country:     {country_name}\n"
        f"  Repository:  {gh_owner}/{gh_repo_name}\n"
        f"  GCP project: {gcp_project_id}\n"
    )
    if gcp_project_name is not None:
        summary += f"  GCP name:    {gcp_project_name}\n"
    if ecosystem_gee_asset_id is not None:
        summary += f"  GEE asset:   {ecosystem_gee_asset_id}\n"
    summary += f"  Project dir: {os.path.abspath(project_dir)}\n"
    summary += f"  Total steps: {total}"
    console.print(Panel(summary, title="RLE Assessment Init", border_style="blue"))

    console.print(Rule("[bold blue]Phase 1: GitHub Repository Setup"))
    setup_github(
        gh_owner,
        gh_repo_name,
        country_name,
        gcp_project_id,
        step_offset=0,
        total=total,
        ecosystem_gee_asset_id=ecosystem_gee_asset_id,
    )

    console.print(Rule("[bold blue]Phase 2: GCP Project Setup"))
    project_number = setup_gcp(
        gcp_project_id,
        gcp_project_name,
        gh_owner,
        gh_repo_name,
        step_offset=github_steps,
        total=total,
    )

    console.print(Rule("[bold blue]Phase 3: GitHub Secrets"))
    setup_secrets(
        gh_owner,
        gh_repo_name,
        gcp_project_id,
        project_number,
        step_offset=github_steps + gcp_steps,
        total=total,
    )

    # Set the SETUP_COMPLETE variable so the deploy workflow is no longer
    # skipped.  The workflow's job-level condition checks for this variable.
    console.print(Rule("[bold blue]Enable Deploy Workflow"))
    gate_cmd = [
        "gh",
        "variable",
        "set",
        "SETUP_COMPLETE",
        "--body=true",
        f"--repo={gh_owner}/{gh_repo_name}",
    ]
    _show_command(gate_cmd)
    gate = subprocess.run(gate_cmd, capture_output=True, text=True)
    if gate.returncode == 0:
        console.print("  [green]SETUP_COMPLETE variable set.[/green]")
    else:
        console.print("  [yellow]Could not set SETUP_COMPLETE variable.[/yellow]")
        console.print(
            "  [dim]Manually run: gh variable set SETUP_COMPLETE --body true "
            f"--repo={gh_owner}/{gh_repo_name}[/dim]"
        )

    # Re-trigger the deploy workflow now that the environment, secrets,
    # and gate variable are fully configured.
    console.print(Rule("[bold blue]Trigger Deploy Workflow"))
    trigger_cmd = [
        "gh",
        "workflow",
        "run",
        "deploy.yml",
        f"--repo={gh_owner}/{gh_repo_name}",
    ]
    _show_command(trigger_cmd)
    trigger = subprocess.run(trigger_cmd, capture_output=True, text=True)
    if trigger.returncode == 0:
        console.print("  [green]Deploy workflow triggered.[/green]")
    else:
        console.print("  [yellow]Could not trigger workflow automatically.[/yellow]")
        console.print(
            "  [dim]Re-run the deploy workflow manually from the Actions tab.[/dim]"
        )

    console.print(Rule("[bold blue]Phase 4: Local Setup"))
    clone_path = setup_local(
        gh_owner,
        gh_repo_name,
        project_dir,
        step_offset=github_steps + gcp_steps + secret_steps,
        total=total,
    )

    in_cloud_shell = os.environ.get("CLOUD_SHELL") == "true" or os.environ.get("DEVSHELL_PROJECT_ID")
    quarto_cmd = (
        "pixi run quarto preview --port 8080 --host 0.0.0.0 --no-browser"
        if in_cloud_shell
        else "pixi run quarto preview"
    )

    console.print()
    console.print(
        Panel(
            f"[bold green]All done![/bold green]\n\n"
            f"  Repository:  https://github.com/{gh_owner}/{gh_repo_name}\n"
            f"  Local clone: {os.path.abspath(clone_path)}\n\n"
            f"  To preview the site:\n"
            f"    [bold]cd {os.path.abspath(clone_path)}[/bold]\n"
            f"    [bold]gcloud auth application-default login[/bold]\n"
            f"    [bold]{quarto_cmd}[/bold]",
            title="Setup Complete",
            border_style="green",
        )
    )


if __name__ == "__main__":
    typer.run(main)
