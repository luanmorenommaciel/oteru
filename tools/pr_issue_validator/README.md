# Issue × PR validator

The validator collects the linked issue, PR metadata, comments, changed files and checks, then emits a deterministic evidence matrix. It never approves or merges.

```powershell
python -m tools.pr_issue_validator.cli `
  --repo luanmorenommaciel/oteru --pr 123 `
  --output validation.md
```

`GITHUB_TOKEN` needs read access to issues, pull requests and checks. For offline tests, pass `--input fixture.json` instead of `--repo/--pr`.

The collector does not execute commands from an untrusted PR. Test results can be appended to the input artifact by a trusted CI job before report generation.

Maintainers may provide one or more explicit `--test-command` values. Commands run without a shell, have a bounded timeout/output, and record the exact command, exit code, stdout, stderr and timeout state. The workflow disables persisted checkout credentials. Only run commands reviewed for the selected PR.

The committed end-to-end fixture exercises the two historical regression classes required by issue #119: mixed complete/incomplete dbt telemetry where `dbt run` is insufficient, and ingestor changes without Windows/CRLF evidence. Run it with the normal test suite:

```powershell
python -m pytest -q tools/pr_issue_validator/tests
```
