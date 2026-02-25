Welcome to the GitHub organization for building Red List of Ecosystems (RLE) assessment reports.


## Prerequisites

- [ ] A [Google Cloud Project (GCP)](https://console.cloud.google.com) account. 
  - If you need to create a new account, visit https://console.cloud.google.com 
- [ ] A [GitHub](https://github.com/) account.
  - If you need to create a new account, visit https://github.com/signup


## Setting up the development environment

This workflow requires a Google Cloud Project (GCP) project, with Google Earth Engine enabled.

To create a new project:

1. **Open a terminal**

    <details>
    <summary><strong>Local development</strong></summary>

    Use your local terminal with the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) installed and authenticated.

    </details>

    <details open>
    <summary><strong>GCP Cloud Shell development</strong></summary>

    Activate the Cloud Shell at https://shell.cloud.google.com

    </details>

2. **Define environment variables**

    The shell commands in the following sections use a set of environment variables. Edit the following code to set these variables for your Google Cloud Project and GitHub accounts.

    ```
    # Country name
    COUNTRY_NAME=Ruritania

    # Google Cloud Project variables
    GCP_PROJECT_ID=your-project-id
    GCP_PROJECT_NAME="YOUR PROJECT NAME"

    # GitHub variables
    GH_OWNER=YOUR-USER-NAME
    GH_REPO_NAME=YOUR-REPO-NAME
    ```

    TEST set:
    ```
    COUNTRY_NAME=Ruritania
    GCP_PROJECT_ID=test-rle-project-2
    GCP_PROJECT_NAME="TEST RLE PROJECT 2"
    GH_OWNER=tylere
    GH_REPO_NAME=TEST-RLE-FROM-TEMPLATE-2
    ```

## Create the assessment's GitHub repository

1. **Authenticate to GitHub**

    Authenticate to GitHub from the Google Cloud Shell, using the GitHub CLI. Use the default options.

    ```
    gh auth login
    ```

    If successful, you should see a response like:
    > ✓ Authentication complete.
    >
    > &hyphen; gh config set -h github.com git_protocol https
    >
    > ✓ Configured git protocol
    >
    > ! Authentication credentials saved in plain text
    >
    > ✓ Logged in as YOUR-ACCOUNT-NAME

1. **Issue the GitHub CLI command to create a new repository for a country**

    ```
    gh repo create ${GH_REPO_NAME} --template RLE-Assessment/TEMPLATE-rle-assessment --description="An IUCN Red List of Ecosystems assessment for ${COUNTRY_NAME}" --public --include-all-branches
    ```

    If successful, you should see something similar to:
    > ✓ Created repository GH_OWNER/YOUR-REPO-NAME on github.com

    Your repository's URL can be constructed by running:

    ```
    echo https://github.com/$GH_OWNER/$GH_REPO_NAME
    ```

1. **Create/update a GitHub environment with a custom branch policy.** 

    This allows GitHub Actions to deploy content contained in specific branches (like `main`) to GitHub Pages.

    ```
    gh api repos/${GH_OWNER}/${GH_REPO_NAME}/environments/github-pages \
    -X PUT \
    --input - <<< '{"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}'
    ```

    If successful, you should see something similar to:
    ```
    {
        "id": 11904269886,
        "node_id": "EN_abcdefghijk-Pg",
        "name": "github-pages",
        "url": "https://api.github.com/repos/GH_OWNER/YOUR-REPO-NAME/environments/github-pages",
        "html_url": "https://github.com/GH_OWNER/YOUR-REPO-NAME/deployments/activity_log?environments_filter=github-pages",
        "created_at": "2026-02-06T23:58:32Z",
        "updated_at": "2026-02-06T23:58:32Z",
        "can_admins_bypass": true,
        "protection_rules": [
            {
            "id": 123456789,
            "node_id": "GA_abcdefg1234356789",
            "type": "branch_policy"
            }
        ],
        "deployment_branch_policy": {
            "protected_branches": false,
            "custom_branch_policies": true
        }
    }
    ```

1. **Add 'main' as an allowed deployment branch**

    ```
    gh api repos/${GH_OWNER}/${GH_REPO_NAME}/environments/github-pages/deployment-branch-policies \
    -X POST \
    -f name="main" \
    -f type="branch"
    ```
    If successful, you should see something similar to:

    ```
    {
        "id": 42507027,
        "node_id": "ABCDEFGHIJ==",
        "name": "main",
        "type": "branch"
    }
    ```


## Create the assessment's GCP project

1. **Create the GCP project**
    ```
    gcloud projects create ${GCP_PROJECT_ID} --name="${GCP_PROJECT_NAME}" --set-as-default
    ```

    You may see a warning message *"Project 'XXXXXXXX' lacks an 'environment' tag..."*  Adding tags is optional, but may be helpful when managing a large number of projects.

1. **Enable required APIs**

    ```
    # Enable Earth Engine API
    gcloud services enable earthengine.googleapis.com --project=${GCP_PROJECT_ID}
    
    # Enable the IAM Service Account Credentials API on your project. This is required for the Workload Identity Federation authentication workflow.
    gcloud services enable iamcredentials.googleapis.com --project=${GCP_PROJECT_ID}
    
    # Enable the Security Token Service (STS) API. This handles step 1 of the Workload Identity Federation flow: exchanging GitHub's OIDC token for a GCP federated token.
    gcloud services enable sts.googleapis.com --project=${GCP_PROJECT_ID}
    
    # Enable the Cloud Resource Manager API on your project. This allows gcloud commands to query project metadata.
    gcloud services enable cloudresourcemanager.googleapis.com --project=${GCP_PROJECT_ID}
    ```

1. **Create Workload Identity Pool and Provider**

    Create a workload identity pool, which is a container for identity providers. You only need one pool per project.

    ```
    gcloud iam workload-identity-pools create "github-pool" \
      --project="${GCP_PROJECT_ID}" \
      --location="global" \
      --display-name="GitHub Actions Pool"
    ```

    Create an OpenID Connect (OIDC) provider, which configures how GitHub tokens are validated and mapped to GCP identities.

    ```
    gcloud iam workload-identity-pools providers create-oidc "github-provider" \
        --project="${GCP_PROJECT_ID}" \
        --location="global" \
        --workload-identity-pool="github-pool" \
        --display-name="GitHub Provider" \
        --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
        --attribute-condition="assertion.repository != ''" \
        --issuer-uri="https://token.actions.githubusercontent.com"
    ```

1. **Create a service account**

    ```
    SA_NAME=github-actions
    gcloud iam service-accounts create "${SA_NAME}" \
        --project="${GCP_PROJECT_ID}" \
        --display-name="GitHub Actions"
    ```

1. **Grant Service Account Required Roles**

    Grant Earth Engine access

    ```
    gcloud projects add-iam-policy-binding "${GCP_PROJECT_ID}" \
        --member="serviceAccount:${SA_NAME}@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
        --role="roles/earthengine.writer"
    ```

    Grant API usage permission

    ```
    gcloud projects add-iam-policy-binding "${GCP_PROJECT_ID}" \
        --member="serviceAccount:${SA_NAME}@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
        --role="roles/serviceusage.serviceUsageConsumer"
    ```

1. **Allow GitHub Repo to Impersonate Service Account**

    Get the GCP project number

    ```
    GCP_PROJECT_NUMBER=$(gcloud projects describe ${GCP_PROJECT_ID} --format="value(projectNumber)")

    echo $GCP_PROJECT_NUMBER
    ```

    Add IAM binding for your specific repository

    ```
    gcloud iam service-accounts add-iam-policy-binding \
        "${SA_NAME}@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
        --project="${GCP_PROJECT_ID}" \
        --role="roles/iam.workloadIdentityUser" \
        --member="principalSet://iam.googleapis.com/projects/${GCP_PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-pool/attribute.repository/${GH_OWNER}/${GH_REPO_NAME}"
    ```

1. **Add GitHub Repository Secrets**

    ```
    gh secret set GCP_WORKLOAD_IDENTITY_PROVIDER --repo ${GH_OWNER}/${GH_REPO_NAME} --body "projects/${GCP_PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-pool/providers/github-provider"

    gh secret set GCP_SERVICE_ACCOUNT --repo ${GH_OWNER}/${GH_REPO_NAME} --body "${SA_NAME}@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
    ```


1. Update Workflow (if using your own project)

    Update .github/workflows/deploy.yml line 51:


    GOOGLE_CLOUD_PROJECT: test-rle-assessment-1  # Your project ID
    And update config/country_config.yaml:
    
    gcp_project: test-rle-assessment-1


1. Set the quota project so API calls are billed correctly

    ***TODO Reorder this step***

    ```
    gcloud auth application-default set-quota-project ${GCP_PROJECT_ID}
    ```

    test

1. **Add IAM binding for the new repository**
 
    ```
    gcloud iam service-accounts add-iam-policy-binding \
    "github-actions-rle@goog-rle-assessments.iam.gserviceaccount.com" \
    --project="goog-rle-assessments" \
    --role="roles/iam.workloadIdentityUser" \
    --member="principalSet://iam.googleapis.com/projects/${GCP_PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-pool/attribute.repository/${GH_OWNER}/${GH_REPO_NAME}"
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
