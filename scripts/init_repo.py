"""Initialize a new RLE assessment repository.

Automates the multi-step process of creating a GitHub repository from the
RLE assessment template, provisioning a Google Cloud Platform project,
configuring Workload Identity Federation, and wiring up GitHub secrets.

Usage:
    python scripts/init_repo.py all --help
    python scripts/init_repo.py github --help
    python scripts/init_repo.py gcp --help
    python scripts/init_repo.py secrets --help
"""

# /// script
# requires-python = ">=3.11"
# dependencies = ["typer>=0.20", "rich>=13"]
# ///

import json
import subprocess
import shutil
import time

import typer
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule

console = Console()

SA_NAME = "github-actions"
TEMPLATE_REPO = "RLE-Assessment/TEMPLATE-rle-assessment"
AUTO_CONFIRM = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def check_prerequisites(need_gh: bool = True, need_gcloud: bool = True) -> None:
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

        result = subprocess.run(
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
        if result.returncode != 0 or not result.stdout.strip():
            console.print(
                Panel(
                    "[bold red]Not authenticated to Google Cloud.[/bold red]\n\n"
                    "Run the following command and follow the prompts:\n"
                    "  [bold]gcloud auth login[/bold]",
                    title="Authentication required",
                )
            )
            raise typer.Exit(code=1)
        console.print(
            f"  [green]Google Cloud CLI authenticated as {result.stdout.strip()}[/green]"
        )


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


def setup_github(
    gh_owner: str,
    gh_repo_name: str,
    country_name: str,
    step_offset: int = 0,
    total: int = 3,
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
    step_offset: int = 0,
    total: int = 9,
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
        capture_output=True,
        text=True,
    )
    if check.returncode == 0 and check.stdout.strip() == gcp_project_id:
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

        run_command(
            [
                "gcloud",
                "projects",
                "create",
                gcp_project_id,
                f"--name={gcp_project_name}",
                "--set-as-default",
            ],
            step=step_offset + 1,
            total=total,
            title="Create GCP Project",
            description="creates a new Google Cloud Platform project that will host the Earth Engine resources and service accounts for this assessment. The project is set as the default for subsequent gcloud commands.",
        )

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
    )

    # Earth Engine registration (manual step — requires accepting ToS)
    ee_url = (
        f"https://console.cloud.google.com/earth-engine/configuration"
        f"?project={gcp_project_id}"
    )
    _step_header(step_offset + 9, total, "Register Project with Earth Engine")
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

    # Verify registration by calling the Earth Engine API
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
            f"https://earthengine.googleapis.com/v1/projects/{gcp_project_id}/config",
        ],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        console.print(
            "  [red]Project does not appear to be registered with Earth Engine.[/red]"
        )
        raise typer.Exit(code=1)
    console.print("  [green]Verified: project is registered with Earth Engine.[/green]")

    return project_number


def setup_gcp(
    gcp_project_id: str,
    gcp_project_name: str | None,
    gh_owner: str,
    gh_repo_name: str,
    step_offset: int = 0,
    total: int | None = None,
) -> str:
    """Set up GCP infrastructure. Returns the project number."""

    final_total = total if total is not None else 9
    return _setup_gcp_own(
        gcp_project_id,
        gcp_project_name,
        gh_owner,
        gh_repo_name,
        step_offset=step_offset,
        total=final_total,
    )


# ---------------------------------------------------------------------------
# Phase 3 – GitHub secrets
# ---------------------------------------------------------------------------


def setup_secrets(
    gh_owner: str,
    gh_repo_name: str,
    gcp_project_id: str,
    project_number: str,
    step_offset: int = 0,
    total: int = 3,
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
# Typer app
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="init-repo",
    help="Initialize a new RLE assessment repository.",
    add_completion=False,
)


@app.command(name="all")
def cmd_all(
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
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts."),
) -> None:
    """Run all initialization steps: GitHub repo, GCP project, and secrets."""
    global AUTO_CONFIRM
    AUTO_CONFIRM = yes
    if gh_owner is None:
        gh_owner = _get_gh_username()
    github_steps = 3
    gcp_steps = 9
    secret_steps = 3
    total = github_steps + gcp_steps + secret_steps

    summary = (
        f"[bold]Initializing RLE assessment repository[/bold]\n\n"
        f"  Country:     {country_name}\n"
        f"  Repository:  {gh_owner}/{gh_repo_name}\n"
        f"  GCP project: {gcp_project_id}\n"
    )
    if gcp_project_name is not None:
        summary += f"  GCP name:    {gcp_project_name}\n"
    summary += f"  Total steps: {total}"
    console.print(Panel(summary, title="RLE Assessment Init", border_style="blue"))

    check_prerequisites(need_gh=True, need_gcloud=True)

    console.print(Rule("[bold blue]Phase 1: GitHub Repository Setup"))
    setup_github(
        gh_owner,
        gh_repo_name,
        country_name,
        step_offset=0,
        total=total,
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

    # Re-trigger the deploy workflow now that the environment and secrets
    # are fully configured.  The initial run (triggered by repo creation)
    # likely failed because neither was in place yet.
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

    console.print()
    console.print(
        Panel(
            f"[bold green]All done![/bold green]\n\n"
            f"  Repository: https://github.com/{gh_owner}/{gh_repo_name}\n"
            f"  Next steps:\n"
            f"    1. cd to your projects directory\n"
            f"    2. Clone the repository:  gh repo clone {gh_owner}/{gh_repo_name}\n"
            f"    3. cd {gh_repo_name}\n"
            f"    4. Install packages:      pixi shell\n"
            f"    5. Preview the site:      quarto preview",
            title="Setup Complete",
            border_style="green",
        )
    )


@app.command()
def github(
    country_name: str = typer.Option(
        ...,
        prompt="Country name",
        help="Name of the country for the assessment.",
    ),
    gh_owner: str | None = typer.Option(
        None,
        help="GitHub user or organization. Defaults to the authenticated user.",
    ),
    gh_repo_name: str = typer.Option(
        ...,
        prompt="GitHub repository name",
        help="Name for the new GitHub repository.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts."),
) -> None:
    """Create the GitHub repository and configure Pages deployment."""
    global AUTO_CONFIRM
    AUTO_CONFIRM = yes
    check_prerequisites(need_gh=True, need_gcloud=False)
    if gh_owner is None:
        gh_owner = _get_gh_username()
    setup_github(gh_owner, gh_repo_name, country_name)


@app.command()
def gcp(
    gcp_project_id: str = typer.Option(
        ...,
        prompt="GCP project ID",
        help="Google Cloud project ID.",
    ),
    gcp_project_name: str | None = typer.Option(
        None,
        help="Human-readable GCP project name (required only when creating a new project).",
    ),
    gh_owner: str | None = typer.Option(
        None,
        help="GitHub user or organization. Defaults to the authenticated user.",
    ),
    gh_repo_name: str = typer.Option(
        ...,
        prompt="GitHub repository name",
        help="Name of the GitHub repository.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts."),
) -> None:
    """Set up the GCP project and Workload Identity Federation."""
    global AUTO_CONFIRM
    AUTO_CONFIRM = yes
    check_prerequisites(need_gh=False, need_gcloud=True)
    if gh_owner is None:
        gh_owner = _get_gh_username()
    setup_gcp(gcp_project_id, gcp_project_name, gh_owner, gh_repo_name)


@app.command()
def secrets(
    gh_owner: str | None = typer.Option(
        None,
        help="GitHub user or organization. Defaults to the authenticated user.",
    ),
    gh_repo_name: str = typer.Option(
        ...,
        prompt="GitHub repository name",
        help="Name of the GitHub repository.",
    ),
    gcp_project_id: str = typer.Option(
        ...,
        prompt="GCP project ID",
        help="Google Cloud project ID.",
    ),
    project_number: str = typer.Option(
        ...,
        prompt="GCP project number",
        help="Numeric GCP project number (from gcloud projects describe).",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts."),
) -> None:
    """Set GitHub repository secrets for GCP authentication."""
    global AUTO_CONFIRM
    AUTO_CONFIRM = yes
    check_prerequisites(need_gh=True, need_gcloud=False)
    if gh_owner is None:
        gh_owner = _get_gh_username()
    setup_secrets(gh_owner, gh_repo_name, gcp_project_id, project_number)


if __name__ == "__main__":
    app()
