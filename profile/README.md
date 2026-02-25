Welcome to the GitHub organization for building Red List of Ecosystems (RLE) assessment reports.


## Prerequisites

- [ ] A [Google Cloud Project (GCP)](https://console.cloud.google.com) account. 
  - If you need to create a new account, visit https://console.cloud.google.com 
- [ ] A [GitHub](https://github.com/) account.
  - If you need to create a new account, visit https://github.com/signup

## Environment Variables

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

```
COUNTRY_NAME=Ruritania
GCP_PROJECT_ID=test-rle-project
GCP_PROJECT_NAME="TEST RLE PROJECT"
GH_OWNER=tylere
GH_REPO_NAME=TEST-REPO-NAME
```

## Creating a GCP Project

This workflow requires a Google Cloud Project (GCP) project, with Google Earth Engine enabled.

To create a new project:

1. Activate the Cloud Shell https://shell.cloud.google.com
    
2. Create the GCP project
    ```
    gcloud projects create ${GCP_PROJECT_ID} --name="${GCP_PROJECT_NAME}" --set-as-default
    ```
3. Enable required APIs

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

4. Create Workload Identity Pool and Provider

    A pool is a container for identity providers. You only need one pool per project.

    ```
    gcloud iam workload-identity-pools create "github-pool" \
      --project="${GCP_PROJECT_ID}" \
      --location="global" \
      --display-name="GitHub Actions Pool"
    ```

    A provider configures how GitHub tokens are validated and mapped to GCP identities.

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

5. Create a service account

    ```
    SA_NAME=github-actions
    gcloud iam service-accounts create "${SA_NAME}" \
        --project="${GCP_PROJECT_ID}" \
        --display-name="GitHub Actions"
    ```

6. Grant Service Account Required Roles

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

7. Allow GitHub Repo to Impersonate Service Account

    Get project number

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

8. Get Values for GitHub Secrets

    Workload Identity Provider (for GCP_WORKLOAD_IDENTITY_PROVIDER secret)
    
    ```
    echo "projects/${GCP_PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-pool/providers/github-provider"
    ```

    Service Account (for GCP_SERVICE_ACCOUNT secret)
    
    ```
    echo "${SA_NAME}@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
    ```

9. Add GitHub Repository Secrets

    Using the In your repo's Settings > Secrets and variables > Actions, add:

    | Secret | Value |
    |--------|-------|
    | `GCP_WORKLOAD_IDENTITY_PROVIDER` | Output from first echo above |
    | `GCP_SERVICE_ACCOUNT` | Output from second echo above |


10. Update Workflow (if using your own project)

    Update .github/workflows/deploy.yml line 51:


    GOOGLE_CLOUD_PROJECT: test-rle-assessment-1  # Your project ID
    And update config/country_config.yaml:
    
    gcp_project: test-rle-assessment-1


11. Set the quota project so API calls are billed correctly

    ***TODO Reorder this step***

    ```
    gcloud auth application-default set-quota-project ${GCP_PROJECT_ID}
    ```

## How to create a new assessment report

Detailed Steps

1. Activate the Cloud Shell https://console.cloud.google.com/welcome?cloudshell=true
  
2. Authenticate to GitHub from the Google Cloud Shell, using the GitHub CLI. Use the default options.
  
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

3. Set the name for the new GitHub repository
  
    ```
    GH_OWNER=YOUR-USER-NAME
    GH_REPO_NAME=YOUR-REPO-NAME
    # GH_OWNER=tylere
    # GH_REPO_NAME=TEST-REPO-NAME
    ```

4. Issue the GitHub CLI command to create a new repository for a country:
  
    ```
    gh repo create ${GH_REPO_NAME} --template RLE-Assessment/TEMPLATE-rle-assessment --description="An IUCN Red List of Ecosystems assessment for ${COUNTRY_NAME}" --public --include-all-branches
    ```

    If successful, you should see something similar to:
    > ✓ Created repository GH_OWNER/YOUR-REPO-NAME on github.com

5. Create/update the GitHub environment with a custom branch policy. This allows GitHub Actions to deploy content contained in specific branches (like `main`) to GitHub Pages.

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

6. Add 'main' as an allowed deployment branch

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

 7. Add IAM binding for the new repository
 
    ```
    gcloud iam service-accounts add-iam-policy-binding \
    "github-actions-rle@goog-rle-assessments.iam.gserviceaccount.com" \
    --project="goog-rle-assessments" \
    --role="roles/iam.workloadIdentityUser" \
    --member="principalSet://iam.googleapis.com/projects/${GCP_PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-pool/attribute.repository/${GH_OWNER}/${GH_REPO_NAME}"
    ```

---

1. **Fork the template repository**
    1. In a new window (so you can still read these instructions), open https://github.com/RLE-Assessment/TEMPLATE-rle-assessment
    <!-- 2. Select **Use this template** > **Create a new repository**
        1. Under **Include all branches**, select **On**
        2. Populate the **Repository name**. For example: "*ruritania-rle-assessment*"
        3. Add a **Description** for the repository. For example: "*An IUCN Red List of Ecosystems assessment for Ruritania*"
        4. Click **Create Repository** -->
    3. On the main page of your new repository, in the About section, click on the gear icon to edit the repository details.
        1. In the **Website** section, check the **Use your GitHub Pages website** option
        2. In the **Include in the home page** section, uncheck the **Packages** option
        2. Click **Save Changes**
    3. Go to the **Actions** tab, and wait for the workflow to run. (It is deploying content to GitHub Pages.)
        1. The workflow will likely fail with the error `Branch "main" is not allowed to deploy to github-pages due to environment protection rules.`
    <!-- 4. Configure environment protection
        1. Go to your repository on GitHub
        2. Navigate to Settings > Environments
        3. Click on github-pages
        4. Under "Deployment branches and tags", click Add deployment branch or tag rule and add `main` -->
    5. Configure GCP authentication

        2. Add secrets to the new repository
            1. Go to **Settings** > **Secrets and variables** > **Actions** for the templated repository and add:
                | Secret | Value |
                |---|---|
                | GCP_WORKLOAD_IDENTITY_PROVIDER | `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/providers/github-provider` |
                | GCP_SERVICE_ACCOUNT | `github-actions-rle@goog-rle-assessments.iam.gserviceaccount.com` |

        3. [***TODO Rerun Jobs***]
            1. Go to the templated repository's Actions tab, and re-run the last failed workflow. This time it should succeed, due to the new GCP configuration.
    4. On the main page, in the **About** section, click on the URL link to open the website generated by the repository.
        1. Look through the website, using the navigation menu on the left hand side.
        2. Note that there are numerous red warning boxes that provide instructions for customizing the website. 

---

## How to edit the assessment report

1. **Install `pixi`**

    [Pixi](https://pixi.prefix.dev) is a package management tool that can be used to create reproducible development environments.

    ```
    curl -fsSL https://pixi.sh/install.sh | sh
    ```

    The pixi install modifies your shell's startup script, so you need to rerun it.

    ```
    source ~/.bashrc
    ```

2. **Create a local clone**

    ```
    gh repo clone ${GH_OWNER}/${GH_REPO_NAME}

    cd ${GH_REPO_NAME}
    ```

3. **Install packages in the local development environment**

    ```
    pixi install
    pixi shell
    ```

4. **Preview the website**

    In the Cloud Shell terminal, run: 

    ```
    quarto preview --port 8080 --host 0.0.0.0 --no-browser --render html
    ```

    You can edit the pages using the Cloud Shell editor. When you save the pages, the preview will update (but it may take a minute to do so).

5. **Publish the website***

    ***TODO...***
