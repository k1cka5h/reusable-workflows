## Summary

<!-- What does this PR do and why? -->

## Type of change

- [ ] Bug fix (non-breaking)
- [ ] New feature / new module (non-breaking)
- [ ] Breaking change (requires major version bump)
- [ ] Policy update
- [ ] Maintenance / dependency update

## Breaking change checklist

<!-- Complete this section only for breaking changes. -->

- [ ] Migration guide written and attached to this PR
- [ ] Two-week notice given to all consuming product teams
- [ ] Old and new behaviour both present in this release (dual-mode)
- [ ] Hard removal scheduled for the following release

## Testing

- [ ] `terraform fmt -recursive modules/` passes
- [ ] `terraform validate` passes for all affected modules
- [ ] `terraform test` passes for all affected modules
- [ ] Construct unit tests pass in all five languages
- [ ] OPA policy tests pass (`opa test policy/ -v`)

## Documentation

- [ ] `CHANGELOG.md` updated under the new version heading
- [ ] Module `README.md` updated if any inputs or outputs changed
- [ ] Wiki updated if behaviour or platform team process changed
