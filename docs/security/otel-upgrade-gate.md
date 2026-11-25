# OTel dependency upgrade gate

Issue [#104](https://github.com/luanmorenommaciel/oteru/issues/104) remains blocked until an otelcol release carries all three fixed module versions.

Capture the effective builder dependencies and evaluate them without an allowlist:

```bash
go list -m -json all > go-modules.json
python scripts/check_otel_upgrade.py go-modules.json
```

Exit code `0` means every target is present at or above the required version. Exit code `2` means the upgrade must remain blocked. This gate does not replace `govulncheck`, SBOM, Trivy, Gitleaks, Docker or reproducible-build evidence required by #104.
