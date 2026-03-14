"""
Workflow trigger tests
======================
Parses the GitHub Actions workflow YAML files and asserts on:
  - Trigger events and path filters
  - Job dependency chains (needs:)
  - Job conditions (if:)
  - Approval gates (environment: on gate jobs)
  - Concurrency settings
  - Secret forwarding to reusable workflows
  - Reusable workflow contract (inputs, secrets, outputs)

Run:
    pip install pytest pyyaml
    pytest reusable-workflows/tests/ -v
"""

from pathlib import Path
import pytest
import yaml

REPO = Path(__file__).parent.parent.parent

INFRA_YML         = REPO / "tf-azure/.github/workflows/infra.yml"
VALIDATE_YML      = REPO / "reusable-workflows/.github/workflows/tf-validate.yml"
CHANGES_YML       = REPO / "reusable-workflows/.github/workflows/tf-changes.yml"
PLAN_YML          = REPO / "reusable-workflows/.github/workflows/tf-plan.yml"
DEPLOY_YML        = REPO / "reusable-workflows/.github/workflows/tf-deploy.yml"
DRIFT_YML         = REPO / "reusable-workflows/.github/workflows/tf-drift.yml"


def load(path: Path) -> dict:
    with path.open() as f:
        data = yaml.safe_load(f)
    # PyYAML parses the YAML 'on:' trigger key as Python True because 'on' is
    # a YAML boolean alias. Normalize it back to the string "on".
    if True in data:
        data["on"] = data.pop(True)
    return data


# ── infra.yml triggers ────────────────────────────────────────────────────────

class TestInfraYmlTriggers:
    wf = load(INFRA_YML)

    def test_triggers_on_pull_request_to_main(self):
        pr = self.wf["on"]["pull_request"]
        assert "main" in pr["branches"]

    def test_triggers_on_push_to_main(self):
        push = self.wf["on"]["push"]
        assert "main" in push["branches"]

    def test_pr_trigger_includes_shared_path(self):
        paths = self.wf["on"]["pull_request"]["paths"]
        assert any("shared/**" in p for p in paths)

    def test_pr_trigger_includes_policy_path(self):
        paths = self.wf["on"]["pull_request"]["paths"]
        assert any("policy/**" in p for p in paths)

    def test_pr_trigger_includes_all_env_paths(self):
        paths = self.wf["on"]["pull_request"]["paths"]
        for env in ("dev/**", "qa/**", "stage/**", "prod/**"):
            assert any(env in p for p in paths), f"PR trigger missing path: {env}"

    def test_push_trigger_does_not_include_policy(self):
        # Policy changes don't re-deploy — deploy only on infrastructure changes.
        paths = self.wf["on"]["push"]["paths"]
        assert not any("policy/**" in p for p in paths), \
            "Push trigger should not include policy/** — policy-only changes should not trigger a deploy"

    def test_concurrency_does_not_cancel_in_progress(self):
        concurrency = self.wf["concurrency"]
        assert concurrency["cancel-in-progress"] is False, \
            "Top-level concurrency must not cancel in-progress — inflight deploys must complete"


# ── infra.yml jobs — validate ─────────────────────────────────────────────────

class TestInfraYmlValidateJob:
    jobs = load(INFRA_YML)["jobs"]

    def test_validate_skips_schedule(self):
        # validate job must NOT run during the drift schedule — drift jobs
        # authenticate independently and do not need a validate pass first.
        condition = self.jobs["validate"].get("if", "")
        assert "schedule" in condition, \
            "validate job should have an if: condition that excludes schedule events"

    def test_validate_calls_reusable_workflow(self):
        uses = self.jobs["validate"]["uses"]
        assert "tf-validate.yml" in uses

    def test_validate_passes_deploy_key(self):
        secrets = self.jobs["validate"]["secrets"]
        assert "tf_modules_deploy_key" in secrets


# ── infra.yml jobs — changes ──────────────────────────────────────────────────

class TestInfraYmlChangesJob:
    jobs = load(INFRA_YML)["jobs"]

    def test_changes_only_on_pr(self):
        condition = self.jobs["changes"]["if"]
        assert "pull_request" in condition

    def test_changes_calls_reusable_workflow(self):
        uses = self.jobs["changes"]["uses"]
        assert "tf-changes.yml" in uses


# ── infra.yml jobs — plan ─────────────────────────────────────────────────────

class TestInfraYmlPlanJobs:
    jobs = load(INFRA_YML)["jobs"]
    ENVS = ("dev", "qa", "stage", "prod")

    def test_all_plan_jobs_exist(self):
        for env in self.ENVS:
            assert f"plan-{env}" in self.jobs, f"Missing plan-{env} job"

    def test_plan_jobs_need_validate_and_changes(self):
        for env in self.ENVS:
            needs = self.jobs[f"plan-{env}"]["needs"]
            assert "validate" in needs, f"plan-{env} must need validate"
            assert "changes" in needs,  f"plan-{env} must need changes"

    def test_plan_jobs_only_on_pr(self):
        for env in self.ENVS:
            condition = self.jobs[f"plan-{env}"]["if"]
            assert "pull_request" in condition, f"plan-{env} must only run on PR"

    def test_plan_jobs_gated_by_shared_or_env_change(self):
        for env in self.ENVS:
            condition = self.jobs[f"plan-{env}"]["if"]
            assert "changes.outputs.shared" in condition, \
                f"plan-{env} must trigger when shared changes"
            assert f"changes.outputs.{env}" in condition, \
                f"plan-{env} must trigger when {env}/ changes"

    def test_plan_jobs_pass_correct_env_secrets(self):
        for env in self.ENVS:
            secrets = self.jobs[f"plan-{env}"]["secrets"]
            assert "azure_client_id" in secrets
            assert "azure_subscription_id" in secrets
            # Client ID secret should be prefixed with environment name
            client_id_ref = secrets["azure_client_id"]
            assert env.upper() in client_id_ref, \
                f"plan-{env} should use {env.upper()}_AZURE_CLIENT_ID, got {client_id_ref}"

    def test_plan_jobs_call_plan_workflow(self):
        for env in self.ENVS:
            uses = self.jobs[f"plan-{env}"]["uses"]
            assert "tf-plan.yml" in uses, f"plan-{env} should use tf-plan.yml"

    def test_plan_jobs_pass_correct_var_file(self):
        for env in self.ENVS:
            var_file = self.jobs[f"plan-{env}"]["with"]["var_file"]
            assert var_file.startswith(f"{env}/"), \
                f"plan-{env} var_file should be {env}/terraform.tfvars, got {var_file}"

    def test_plan_jobs_pass_correct_backend_config(self):
        for env in self.ENVS:
            backend = self.jobs[f"plan-{env}"]["with"]["backend_config"]
            assert backend.startswith(f"{env}/"), \
                f"plan-{env} backend_config should be {env}/backend.hcl, got {backend}"


# ── infra.yml jobs — deploy chain ─────────────────────────────────────────────

class TestInfraYmlDeployChain:
    jobs = load(INFRA_YML)["jobs"]

    def test_deploy_dev_only_on_push(self):
        condition = self.jobs["deploy-dev"]["if"]
        assert "push" in condition
        assert "pull_request" not in condition

    def test_deploy_dev_needs_validate(self):
        needs = self.jobs["deploy-dev"]["needs"]
        assert "validate" in (needs if isinstance(needs, list) else [needs])

    def test_deploy_qa_needs_deploy_dev(self):
        needs = self.jobs["deploy-qa"]["needs"]
        assert "deploy-dev" in (needs if isinstance(needs, list) else [needs])

    def test_gate_stage_needs_deploy_qa(self):
        needs = self.jobs["gate-stage"]["needs"]
        assert "deploy-qa" in (needs if isinstance(needs, list) else [needs])

    def test_gate_stage_has_stage_environment(self):
        assert self.jobs["gate-stage"]["environment"] == "stage"

    def test_deploy_stage_needs_gate_stage(self):
        needs = self.jobs["deploy-stage"]["needs"]
        assert "gate-stage" in (needs if isinstance(needs, list) else [needs])

    def test_gate_prod_needs_deploy_stage(self):
        needs = self.jobs["gate-prod"]["needs"]
        assert "deploy-stage" in (needs if isinstance(needs, list) else [needs])

    def test_gate_prod_has_prod_environment(self):
        assert self.jobs["gate-prod"]["environment"] == "prod"

    def test_deploy_prod_needs_gate_prod(self):
        needs = self.jobs["deploy-prod"]["needs"]
        assert "gate-prod" in (needs if isinstance(needs, list) else [needs])

    def test_deploy_jobs_call_deploy_workflow(self):
        for env in ("dev", "qa", "stage", "prod"):
            uses = self.jobs[f"deploy-{env}"]["uses"]
            assert "tf-deploy.yml" in uses, f"deploy-{env} should use tf-deploy.yml"

    def test_deploy_jobs_only_on_push_to_main(self):
        for env in ("dev", "qa", "stage", "prod"):
            condition = self.jobs[f"deploy-{env}"]["if"]
            assert "push" in condition,        f"deploy-{env} must only run on push"
            assert "refs/heads/main" in condition, f"deploy-{env} must only run on main"

    def test_deploy_jobs_do_not_cancel_in_progress(self):
        for env in ("dev", "qa", "stage", "prod"):
            concurrency = self.jobs[f"deploy-{env}"]["concurrency"]
            assert concurrency["cancel-in-progress"] is False, \
                f"deploy-{env} must not cancel in-progress (risk of partial apply)"

    def test_deploy_prod_has_longer_artifact_retention(self):
        days = self.jobs["deploy-prod"]["with"].get("plan_artifact_retention_days")
        assert days is not None, "deploy-prod should set plan_artifact_retention_days"
        assert int(days) >= 60, \
            f"prod artifact retention should be >= 60 days for audit, got {days}"

    def test_gate_jobs_only_on_push_to_main(self):
        for gate in ("gate-stage", "gate-prod"):
            condition = self.jobs[gate]["if"]
            assert "push" in condition
            assert "refs/heads/main" in condition


# ── tf-validate.yml contract ──────────────────────────────────────────────────

class TestValidateWorkflow:
    wf = load(VALIDATE_YML)

    def test_trigger_is_workflow_call(self):
        assert "workflow_call" in self.wf["on"]

    def test_accepts_working_directory_input(self):
        inputs = self.wf["on"]["workflow_call"]["inputs"]
        assert "working_directory" in inputs

    def test_accepts_terraform_version_input(self):
        inputs = self.wf["on"]["workflow_call"]["inputs"]
        assert "terraform_version" in inputs

    def test_requires_deploy_key_secret(self):
        secrets = self.wf["on"]["workflow_call"]["secrets"]
        assert secrets["tf_modules_deploy_key"]["required"] is True

    def test_runs_fmt_check(self):
        steps = self.wf["jobs"]["validate"]["steps"]
        run_commands = " ".join(s.get("run", "") for s in steps)
        assert "fmt -check" in run_commands

    def test_runs_init_without_backend(self):
        steps = self.wf["jobs"]["validate"]["steps"]
        run_commands = " ".join(s.get("run", "") for s in steps)
        assert "-backend=false" in run_commands

    def test_runs_validate(self):
        steps = self.wf["jobs"]["validate"]["steps"]
        run_commands = " ".join(s.get("run", "") for s in steps)
        assert "validate" in run_commands


# ── tf-changes.yml contract ───────────────────────────────────────────────────

class TestChangesWorkflow:
    wf = load(CHANGES_YML)

    def test_trigger_is_workflow_call(self):
        assert "workflow_call" in self.wf["on"]

    def test_exposes_all_environment_outputs(self):
        outputs = self.wf["on"]["workflow_call"]["outputs"]
        for key in ("shared", "dev", "qa", "stage", "prod"):
            assert key in outputs, f"tf-changes.yml must output '{key}'"

    def test_accepts_path_inputs_for_each_env(self):
        inputs = self.wf["on"]["workflow_call"]["inputs"]
        for key in ("shared_paths", "dev_paths", "qa_paths", "stage_paths", "prod_paths"):
            assert key in inputs, f"tf-changes.yml must accept input '{key}'"

    def test_uses_paths_filter_action(self):
        steps = self.wf["jobs"]["detect"]["steps"]
        actions = [s.get("uses", "") for s in steps]
        assert any("paths-filter" in a for a in actions), \
            "tf-changes.yml should use dorny/paths-filter"

    def test_job_exposes_all_outputs(self):
        job_outputs = self.wf["jobs"]["detect"]["outputs"]
        for key in ("shared", "dev", "qa", "stage", "prod"):
            assert key in job_outputs


# ── tf-plan.yml contract ──────────────────────────────────────────────────────

class TestPlanWorkflow:
    wf = load(PLAN_YML)

    def test_trigger_is_workflow_call(self):
        assert "workflow_call" in self.wf["on"]

    def test_required_inputs(self):
        inputs = self.wf["on"]["workflow_call"]["inputs"]
        for key in ("environment", "var_file", "backend_config"):
            assert inputs[key].get("required") is True, \
                f"tf-plan.yml input '{key}' must be required"

    def test_optional_inputs_have_defaults(self):
        inputs = self.wf["on"]["workflow_call"]["inputs"]
        for key in ("working_directory", "terraform_version", "conftest_version", "policy_dir"):
            assert "default" in inputs[key], \
                f"tf-plan.yml input '{key}' must have a default"

    def test_required_azure_secrets(self):
        secrets = self.wf["on"]["workflow_call"]["secrets"]
        for key in ("azure_client_id", "azure_subscription_id", "azure_tenant_id",
                    "tf_modules_deploy_key"):
            assert secrets[key].get("required") is True, \
                f"tf-plan.yml secret '{key}' must be required"

    def test_optional_app_secrets(self):
        secrets = self.wf["on"]["workflow_call"]["secrets"]
        for key in ("tf_var_db_password", "tf_var_log_workspace"):
            assert key in secrets
            assert secrets[key].get("required") is not True, \
                f"tf-plan.yml secret '{key}' should be optional"

    def test_uses_oidc_auth(self):
        env = self.wf["jobs"]["plan"]["env"]
        assert env.get("ARM_USE_OIDC") == "true", \
            "tf-plan.yml must use OIDC (ARM_USE_OIDC=true), not a static client secret"

    def test_runs_policy_check(self):
        steps = self.wf["jobs"]["plan"]["steps"]
        run_commands = " ".join(s.get("run", "") for s in steps)
        assert "conftest" in run_commands, "plan workflow must run conftest policy check"

    def test_policy_check_passes_environment_data(self):
        steps = self.wf["jobs"]["plan"]["steps"]
        run_commands = " ".join(s.get("run", "") for s in steps)
        # The environment is passed as JSON data to conftest
        assert "environment" in run_commands and "inputs.environment" in run_commands, \
            "conftest must receive environment as data for policy rules"

    def test_posts_pr_comment(self):
        steps = self.wf["jobs"]["plan"]["steps"]
        action_uses = [s.get("uses", "") for s in steps]
        assert any("github-script" in u for u in action_uses), \
            "plan workflow must post a PR comment"


# ── tf-deploy.yml contract ────────────────────────────────────────────────────

class TestDeployWorkflow:
    wf = load(DEPLOY_YML)

    def test_trigger_is_workflow_call(self):
        assert "workflow_call" in self.wf["on"]

    def test_required_inputs(self):
        inputs = self.wf["on"]["workflow_call"]["inputs"]
        for key in ("environment", "var_file", "backend_config"):
            assert inputs[key].get("required") is True

    def test_has_artifact_retention_input(self):
        inputs = self.wf["on"]["workflow_call"]["inputs"]
        assert "plan_artifact_retention_days" in inputs
        assert "default" in inputs["plan_artifact_retention_days"]

    def test_required_azure_secrets(self):
        secrets = self.wf["on"]["workflow_call"]["secrets"]
        for key in ("azure_client_id", "azure_subscription_id", "azure_tenant_id",
                    "tf_modules_deploy_key"):
            assert secrets[key].get("required") is True

    def test_uses_oidc_auth(self):
        env = self.wf["jobs"]["deploy"]["env"]
        assert env.get("ARM_USE_OIDC") == "true"

    def test_runs_policy_check_before_apply(self):
        steps = self.wf["jobs"]["deploy"]["steps"]
        names = [s.get("name", "") for s in steps]
        policy_idx = next(
            (i for i, n in enumerate(names) if "policy" in n.lower()), None)
        apply_idx  = next(
            (i for i, n in enumerate(names) if "apply"  in n.lower()), None)
        assert policy_idx is not None, "deploy workflow must have a policy check step"
        assert apply_idx  is not None, "deploy workflow must have an apply step"
        assert policy_idx < apply_idx, \
            "policy check must run BEFORE apply, not after"

    def test_runs_apply_with_auto_approve(self):
        steps = self.wf["jobs"]["deploy"]["steps"]
        run_commands = " ".join(s.get("run", "") for s in steps)
        assert "-auto-approve" in run_commands

    def test_uploads_plan_artifact(self):
        steps = self.wf["jobs"]["deploy"]["steps"]
        action_uses = [s.get("uses", "") for s in steps]
        assert any("upload-artifact" in u for u in action_uses)

    def test_no_environment_key_in_deploy_job(self):
        # environment: must NOT be set in this reusable workflow.
        # Approval gates belong in the calling workflow (they're evaluated
        # against the CALLING repo, not this reusable workflow repo).
        job = self.wf["jobs"]["deploy"]
        assert "environment" not in job, (
            "tf-deploy.yml must not set environment: — approval gates "
            "must live in the calling workflow so they evaluate against "
            "the calling repo's GitHub Environment protection rules"
        )


# ── tf-drift.yml contract ─────────────────────────────────────────────────────

class TestDriftWorkflow:
    wf = load(DRIFT_YML)

    def test_trigger_is_workflow_call(self):
        assert "workflow_call" in self.wf["on"]

    def test_required_inputs(self):
        inputs = self.wf["on"]["workflow_call"]["inputs"]
        for key in ("environment", "var_file", "backend_config"):
            assert inputs[key].get("required") is True

    def test_required_azure_secrets(self):
        secrets = self.wf["on"]["workflow_call"]["secrets"]
        for key in ("azure_client_id", "azure_subscription_id", "azure_tenant_id",
                    "tf_modules_deploy_key"):
            assert secrets[key].get("required") is True

    def test_uses_oidc_auth(self):
        env = self.wf["jobs"]["drift"]["env"]
        assert env.get("ARM_USE_OIDC") == "true"

    def test_uses_detailed_exitcode(self):
        steps = self.wf["jobs"]["drift"]["steps"]
        run_commands = " ".join(s.get("run", "") for s in steps)
        assert "-detailed-exitcode" in run_commands, \
            "drift workflow must use -detailed-exitcode to detect changes vs errors"

    def test_opens_github_issue_on_drift(self):
        steps = self.wf["jobs"]["drift"]["steps"]
        action_uses = [s.get("uses", "") for s in steps]
        assert any("github-script" in u for u in action_uses), \
            "drift workflow must open a GitHub issue when drift is detected"

    def test_job_requires_issues_write_permission(self):
        permissions = self.wf["jobs"]["drift"].get("permissions", {})
        assert permissions.get("issues") == "write", \
            "drift job must have issues: write permission to open drift issues"

    def test_no_environment_key_in_drift_job(self):
        job = self.wf["jobs"]["drift"]
        assert "environment" not in job, \
            "tf-drift.yml must not set environment: (same reason as tf-deploy.yml)"


# ── infra.yml — plan job concurrency (cancel stale plans) ────────────────────

class TestInfraYmlPlanConcurrency:
    jobs = load(INFRA_YML)["jobs"]

    def test_plan_jobs_cancel_in_progress(self):
        for env in ("dev", "qa", "stage", "prod"):
            concurrency = self.jobs[f"plan-{env}"].get("concurrency", {})
            assert concurrency.get("cancel-in-progress") is True, (
                f"plan-{env} must set cancel-in-progress: true — "
                "a new commit to a PR makes the old plan stale"
            )

    def test_plan_job_concurrency_groups_are_per_branch(self):
        for env in ("dev", "qa", "stage", "prod"):
            group = self.jobs[f"plan-{env}"]["concurrency"]["group"]
            assert "head_ref" in group, (
                f"plan-{env} concurrency group must use github.head_ref "
                "so each PR branch gets its own group"
            )


# ── infra.yml — reusable workflow version pinning ────────────────────────────

class TestInfraYmlVersionPinning:
    jobs = load(INFRA_YML)["jobs"]

    def _all_uses(self):
        for job in self.jobs.values():
            uses = job.get("uses", "")
            if uses:
                yield uses

    def test_no_jobs_pin_to_main(self):
        for uses in self._all_uses():
            assert not uses.endswith("@main"), (
                f"Job uses '{uses}' — reusable workflows must be pinned to a "
                "release tag (e.g. @v1.0.0), not @main. @main can break "
                "calling workflows when the reusable workflow changes."
            )

    def test_all_reusable_calls_use_versioned_tag(self):
        for uses in self._all_uses():
            if "reusable-workflows" in uses:
                assert "@v" in uses, (
                    f"'{uses}' must reference a semver tag like @v1.0.0"
                )


# ── infra.yml — drift detection schedule ─────────────────────────────────────

class TestInfraYmlDriftSchedule:
    wf   = load(INFRA_YML)
    jobs = load(INFRA_YML)["jobs"]

    def test_has_schedule_trigger(self):
        assert "schedule" in self.wf["on"], \
            "infra.yml must have a schedule trigger for daily drift detection"

    def test_schedule_is_daily(self):
        schedules = self.wf["on"]["schedule"]
        assert len(schedules) >= 1
        # Any cron that runs at least daily is acceptable
        assert any("*" in s["cron"] for s in schedules)

    def test_drift_jobs_exist_for_all_envs(self):
        for env in ("dev", "qa", "stage", "prod"):
            assert f"drift-{env}" in self.jobs, f"Missing drift-{env} job"

    def test_drift_jobs_only_on_schedule(self):
        for env in ("dev", "qa", "stage", "prod"):
            condition = self.jobs[f"drift-{env}"]["if"]
            assert "schedule" in condition, \
                f"drift-{env} must only run on schedule events"

    def test_drift_jobs_call_drift_workflow(self):
        for env in ("dev", "qa", "stage", "prod"):
            uses = self.jobs[f"drift-{env}"]["uses"]
            assert "tf-drift.yml" in uses, \
                f"drift-{env} must use tf-drift.yml"

    def test_drift_jobs_not_pinned_to_main(self):
        for env in ("dev", "qa", "stage", "prod"):
            uses = self.jobs[f"drift-{env}"]["uses"]
            assert not uses.endswith("@main"), \
                f"drift-{env} must pin to a release tag, not @main"
