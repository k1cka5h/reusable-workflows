# k1cka5h/reusable-workflows — Terraform

Reusable GitHub Actions workflows for Nautilus Terraform repos. Call these from
any product team infrastructure repository instead of copy-pasting pipeline logic.

## Workflows

| Workflow | Purpose | Needs credentials? |
|----------|---------|-------------------|
| `tf-validate.yml` | `fmt -check`, `init -backend=false`, `validate` | Deploy key only |
| `tf-changes.yml` | Detect which env folders changed in a PR | None |
| `tf-plan.yml` | Init, plan, policy check, post PR comment | Azure OIDC + deploy key |
| `tf-deploy.yml` | Init, plan, policy check, apply, upload artifact | Azure OIDC + deploy key |

---

## Required secrets in the calling repo

| Secret | Scope | Description |
|--------|-------|-------------|
| `{ENV}_AZURE_CLIENT_ID` | Repository | App registration client ID per environment |
| `{ENV}_AZURE_SUBSCRIPTION_ID` | Repository | Azure subscription ID per environment |
| `AZURE_TENANT_ID` | Repository variable | Azure AD tenant (same across envs) |
| `TF_MODULES_DEPLOY_KEY` | Repository | Read-only SSH key for `k1cka5h/terraform-modules` |
| `DB_ADMIN_PASSWORD` | Repository | Passed as `TF_VAR_administrator_password` |
| `LOG_WORKSPACE_ID` | Repository | Passed as `TF_VAR_log_analytics_workspace_id` |

GitHub Environments (`dev`, `qa`, `stage`, `prod`) — configure required reviewers
for `stage` and `prod` to gate deployments.

---

## Approval gates

GitHub evaluates `environment:` protection rules against the repo where the workflow
is **defined**, not where it's **called**. Because these reusable workflows live in
`k1cka5h/reusable-workflows`, they cannot enforce your repo's approval rules.

**Pattern:** add a lightweight gate job in your calling workflow before the deploy:

```yaml
gate-prod:
  needs: deploy-stage
  if: github.event_name == 'push' && github.ref == 'refs/heads/main'
  environment: prod          # ← evaluated against YOUR repo's environments
  runs-on: ubuntu-latest
  steps:
    - run: echo "Approved — deploying to prod"

deploy-prod:
  needs: gate-prod
  uses: k1cka5h/reusable-workflows/.github/workflows/tf-deploy.yml@main
  ...
```

---

## Minimal calling workflow

```yaml
name: Infrastructure

on:
  pull_request:
    branches: [main]
    paths: ["shared/**", "dev/**", "qa/**", "stage/**", "prod/**", "policy/**"]
  push:
    branches: [main]
    paths: ["shared/**", "dev/**", "qa/**", "stage/**", "prod/**"]

concurrency:
  group: infra-${{ github.ref }}
  cancel-in-progress: false

permissions:
  contents: read
  id-token: write
  pull-requests: write

jobs:
  # ── Always ────────────────────────────────────────────────────────────────────
  validate:
    uses: k1cka5h/reusable-workflows/.github/workflows/tf-validate.yml@main
    secrets:
      tf_modules_deploy_key: ${{ secrets.TF_MODULES_DEPLOY_KEY }}

  # ── PR: detect what changed ────────────────────────────────────────────────
  changes:
    if: github.event_name == 'pull_request'
    uses: k1cka5h/reusable-workflows/.github/workflows/tf-changes.yml@main

  # ── PR: plan only affected environments ────────────────────────────────────
  plan-dev:
    needs: [validate, changes]
    if: github.event_name == 'pull_request' && (needs.changes.outputs.shared == 'true' || needs.changes.outputs.dev == 'true')
    uses: k1cka5h/reusable-workflows/.github/workflows/tf-plan.yml@main
    with:
      environment:    dev
      var_file:       dev/terraform.tfvars
      backend_config: dev/backend.hcl
    secrets:
      azure_client_id:       ${{ secrets.DEV_AZURE_CLIENT_ID }}
      azure_subscription_id: ${{ secrets.DEV_AZURE_SUBSCRIPTION_ID }}
      azure_tenant_id:       ${{ vars.AZURE_TENANT_ID }}
      tf_modules_deploy_key: ${{ secrets.TF_MODULES_DEPLOY_KEY }}
      tf_var_db_password:    ${{ secrets.DB_ADMIN_PASSWORD }}
      tf_var_log_workspace:  ${{ secrets.LOG_WORKSPACE_ID }}

  # ... repeat plan-qa, plan-stage, plan-prod

  # ── Push: sequential deploy ────────────────────────────────────────────────
  deploy-dev:
    needs: validate
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    uses: k1cka5h/reusable-workflows/.github/workflows/tf-deploy.yml@main
    with:
      environment:    dev
      var_file:       dev/terraform.tfvars
      backend_config: dev/backend.hcl
    secrets:
      azure_client_id:       ${{ secrets.DEV_AZURE_CLIENT_ID }}
      azure_subscription_id: ${{ secrets.DEV_AZURE_SUBSCRIPTION_ID }}
      azure_tenant_id:       ${{ vars.AZURE_TENANT_ID }}
      tf_modules_deploy_key: ${{ secrets.TF_MODULES_DEPLOY_KEY }}
      tf_var_db_password:    ${{ secrets.DB_ADMIN_PASSWORD }}
      tf_var_log_workspace:  ${{ secrets.LOG_WORKSPACE_ID }}

  # ... deploy-qa similarly, then gate-stage / deploy-stage / gate-prod / deploy-prod
```

See [`tf-azure/.github/workflows/infra.yml`](../../tf-azure/.github/workflows/infra.yml)
for the complete calling workflow example.

---

## Adding a new environment

1. Create `<env>/backend.hcl` and `<env>/terraform.tfvars` in your repo.
2. Add `{ENV}_AZURE_CLIENT_ID` and `{ENV}_AZURE_SUBSCRIPTION_ID` to repo secrets.
3. Add `plan-<env>` and `deploy-<env>` jobs to your calling workflow following
   the existing pattern.
4. If the environment needs an approval gate, add a `gate-<env>` job before
   `deploy-<env>` with `environment: <env>` and configure reviewers in GitHub.

## Versioning

Pin a specific release tag in calling workflows for stability:

```yaml
uses: k1cka5h/reusable-workflows/.github/workflows/tf-plan.yml@v1.2.0
```

Use `@main` only in development. All Nautilus product repos should pin a release.
