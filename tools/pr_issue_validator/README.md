# Issue × PR validator

The validator collects the linked issue, PR metadata, comments, changed files and checks, then emits a deterministic evidence matrix. It never approves or merges.

```powershell
python -m tools.pr_issue_validator.cli `
  --repo luanmorenommaciel/oteru --pr 123 `
  --output validation.md
```

`GITHUB_TOKEN` needs read access to issues, pull requests and checks. For offline tests, pass `--input fixture.json` instead of `--repo/--pr`.

The collector does not execute commands from an untrusted PR. Test results can be appended to the input artifact by a trusted CI job before report generation.
