***Welcome to the GitHub organization for building Red List of Ecosystems (RLE) assessment reports.***

The intent of this content is to make it easier to build IUCN [Red List of Ecosystems](https://iucnrle.org/) assessment reports following the [Global Ecosystem Typology](https://global-ecosystems.org/) classification framework. This workflow creates a website and PDF document with standard RLE calculations that can be future customized.

## Prerequisites

- [ ] A [Google Cloud Project (GCP)](https://console.cloud.google.com) account
  - If you need to create a new account, visit https://console.cloud.google.com 
- [ ] A [GitHub](https://github.com/) account
  - If you need to create a new account, visit https://github.com/signup


## Create a new assessment repository

Creating a new assessment report repository involves configuring a GitHub code repository (for content) and a Google Cloud Platform project (for data access and storage). An initialization script automates the full setup process: creating a GitHub repository from the template, provisioning a GCP project with Workload Identity Federation, and configuring GitHub secrets. The script displays each command it runs with a detailed explanation of what it does and why.

Instructions are provided for either local development or in a GCP Cloud Shell development.

<details>
<summary><strong>Local development</strong></summary>

Additional prerequisites for local development:

- [ ] [GitHub CLI (`gh`)](https://cli.github.com) installed and authenticated (`gh auth login`). This is used to create and configure GitHub repositories.
- [ ] [Google Cloud CLI (`gcloud`)](https://cloud.google.com/sdk/docs/install) installed and authenticated (`gcloud auth login`). This is used to create and configure Google Cloud Platform projects.
- [ ] [uv](https://docs.astral.sh/uv/) installed. This is used to run the initialization script (dependencies are resolved automatically).

The script checks for these prerequisites and gives clear instructions if anything is missing.

</details>

<details>
<summary><strong>GCP Cloud Shell development</strong></summary>

In a browser, open a GCP Cloud Shell terminal by going to:

https://shell.cloud.google.com/?show=terminal

![Cloud Shell terminal](../images/cloud_shell_screenshot.png)

</details>


### EXTRA

<details>
<summary><strong>Local development</strong></summary>

...

</details>

<details>
<summary><strong>GCP Cloud Shell development</strong></summary>

In the Cloud Shell terminal, enter

```
cloudshell open-workspace .
```

</details>



### Run the initialization script

The `uv run` command downloads the script directly from GitHub and automatically installs its dependencies (in an isolated, temporary environment) before running it -- no cloning or manual setup required.

Replace the placeholder values below with your own before running:

```
uv run https://raw.githubusercontent.com/RLE-Assessment/.github/main/scripts/init_repo.py all \
  --country-name "Ruritania" \
  --gcp-project-id your-project-id \
  --gcp-project-name "YOUR PROJECT NAME" \
  --gh-repo-name YOUR-REPO-NAME
```

| Option | Description |
|---|---|
| `--country-name` | Name of the country for the assessment |
| `--gcp-project-id` | A globally unique GCP project identifier (lowercase letters, digits, and hyphens) |
| `--gcp-project-name` | *(Optional)* Display name for the GCP project (only needed when creating a new project; prompted if omitted) |
| `--gh-repo-name` | Name for the new GitHub repository |
| `--gh-owner` | *(Optional)* GitHub username or organization. Defaults to the authenticated user. |
| `--yes` / `-y` | *(Optional)* Skip confirmation prompts (useful for non-interactive use) |

Most options are prompted interactively if omitted. The `--gh-owner` option defaults to the authenticated GitHub user when not specified; pass it explicitly to create the repository under an organization.

The script displays each command with an explanation before running it and asks for confirmation. Use `--yes` to skip the prompts.

The script runs three phases:

1. **GitHub Repository Setup** -- creates the repo from the template and configures GitHub Pages deployment
2. **GCP Project Setup** -- creates (or reuses) a GCP project, enables APIs, sets up Workload Identity Federation for keyless authentication, and verifies Earth Engine registration
3. **GitHub Secrets** -- stores the WIF provider and service account as repository secrets

The script is idempotent -- it skips resources that already exist, so it is safe to re-run if a step fails partway through.

### Running individual phases

Each phase can be run independently:

```
uv run https://raw.githubusercontent.com/RLE-Assessment/.github/main/scripts/init_repo.py github --help
uv run https://raw.githubusercontent.com/RLE-Assessment/.github/main/scripts/init_repo.py gcp --help
uv run https://raw.githubusercontent.com/RLE-Assessment/.github/main/scripts/init_repo.py secrets --help
```

### Test example

```
uv run https://raw.githubusercontent.com/RLE-Assessment/.github/main/scripts/init_repo.py all \
  --country-name Ruritania \
  --gcp-project-id test-rle-project \
  --gcp-project-name "TEST RLE PROJECT" \
  --gh-repo-name TEST-RLE-FROM-TEMPLATE \
  --yes
```

## How to edit the assessment report

1. **Install `pixi`**

    [Pixi](https://pixi.prefix.dev) is a package management tool that can be used to create reproducible development environments.

    ```
    curl -fsSL https://pixi.sh/install.sh | sh
    ```

    The pixi install modifies your shell's startup script, so you need to re-execute it to update your current shell.

    ```
    source ~/.bashrc
    ```

1. **Create a local clone**

Clone the repository for editing on your local computer or within Cloud Shell. Change the working directory to be the root of the cloned repository.

    ```
    gh repo clone ${GH_OWNER}/${GH_REPO_NAME}

    cd ${GH_REPO_NAME}
    ```

1. **Install packages**

    Install packages in the development environment and open a shell containing those packages.

    ```
    pixi shell
    ```

1. **Open the repository files in an editor**

    <details>
    <summary><strong>Local development</strong></summary>

    ...

    </details>

    <details open>
    <summary><strong>GCP Cloud Shell development</strong></summary>

    In the Cloud Shell terminal, enter

    ```
    cloudshell open-workspace .
    ```



    </details>


1. **Preview the website**

    In the Cloud Shell terminal, run: 

    ```
    quarto preview --port 8080 --host 0.0.0.0 --no-browser --render html
    ```

    Edit files in your development environment. When you save the pages, the preview will update (but it may take a minute to do so, if using Cloud Shell).

1. **Publish the website***

    ***TODO...***
