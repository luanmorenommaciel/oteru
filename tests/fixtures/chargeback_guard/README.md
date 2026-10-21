# Chargeback seed-guard fixtures (#46 task 3)

These files are **test fixtures**, not production dbt seeds. They are copied
under a temporary `seeds/chargeback_*` path by `tests/test_chargeback_seeds.py`.

| File | Purpose |
|---|---|
| `at_forbidden.csv` | Seed value containing `@` — the checker and gitleaks rule must fail. |
| `valid_hmac.csv` | Actor key `hmac:v1:` + 64 dummy hex `a`s — must pass. |
| `malformed_hmac.csv` | Actor key that does not match `^hmac:v1:[0-9a-f]{64}$` — must fail. |

## Fake email (exact-path exclude)

`at_forbidden.csv` contains the obviously fake RFC 2606 address
`user@example.invalid`. It is **not** a real mailbox. That string must not
appear anywhere except this file.

Gitleaks rule `chargeback-seed-at` is path-scoped to `seeds/chargeback_*`.
This fixture does not live under that glob, and is additionally excluded by
**exact path** in `.gitleaks.toml` (`[rules.allowlist]`, rule-scoped — not a
global allowlist):

```
tests/fixtures/chargeback_guard/at_forbidden.csv
```

Do not glob this exception. Do not put `@` in `oteru-dbt/seeds/chargeback_*`.

## Dummy HMAC

`valid_hmac.csv` uses `hmac:v1:` followed by 64 hex `a` characters. That is a
documented dummy, **not** an HMAC of any email or other identifier. Do not
replace it with a real hash.
