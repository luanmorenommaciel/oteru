# Declared component inventory (#66)

This stacked PR adds exact inventory comparison to the shared risk contract
introduced by #63. Review its diff against `operational-risk-64`. Inventory
and allowlist are supplied explicitly; this evaluator does not discover or
attest installed MCP servers, plugins, or skills.

The bundle may include `inventory` with `snapshot_id`, `observed_at_ns`, and
`components`. Each component declares `kind` (`mcp`, `plugin`, or `skill`),
opaque `id`, `version`, and lowercase 64-character SHA-256 `digest`. When
inventory is supplied, policy must include a versioned `allowlist` with
`allowlist_id`, `allowlist_version`, and entries in the same schema. Missing
policy, malformed metadata, and conflicting declarations fail with exit 2.
An explicit empty inventory is evaluated; an absent inventory with a
configured allowlist is `not_evaluable`.

Comparison is exact. `report.json` lists each declared component's projected
identity and one status: `allowed`, `unknown`, `version_mismatch`, or
`digest_mismatch`. The latter three each emit a
`declared_component_unapproved` finding with reason and allowlist version.
Approved components do not create risk findings. The digest is a declaration
from the caller; it is not recomputed from an installed component.

## Definition of done

```bash
make test-risk             # schema, 3 kinds × 4 outcomes, duplicates pass
make demo-risk-inventory   # 1 allowed and 3 unapproved synthetic components
make lint-risk             # #66 code, tests and E2E script pass Ruff
make pii-guard             # inventory fixture and output layers are scanned
make dry-run               # existing sample still parses
make e2e-risk-inventory    # Docker: 3 logical findings, 6 rows after replay
```

An inventory producer, completeness guarantee, and allowlist owner remain
production decisions. No host scan, continuous monitor, notification route,
or enforcement is added. Rollback is to stop invoking this opt-in evaluator
and revert this branch; already stored findings retain their IDs and scope.
