# Compliance Pack evidence contract

**Issue:** [#5](https://github.com/luanmorenommaciel/oteru/issues/5)
**Primary source:** [Regulation (EU) 2024/1689](https://eur-lex.europa.eu/eli/reg/2024/1689/oj)

This contract structures technical evidence for review. It is not a legal opinion and does not assert that telemetry alone establishes compliance.

## Evidence matrix

| Provision | Technical question | Minimum evidence | Explicit limitation |
| --- | --- | --- | --- |
| Article 12 — record-keeping | Can the system produce logs over its lifetime appropriate to its intended purpose? | period, system/version, event timeline, provenance, integrity digest, retention policy and gaps | Oteru cannot prove events it never received |
| Article 12 — traceability | Can a reviewer reconstruct the relevant operation and changes? | immutable references, declared intent, decisions, overrides, results and source records | inference must not be presented as declared fact |
| Article 13 — transparency | Can a deployer understand capabilities, limits and expected operation? | coverage manifest, intended purpose, known limitations, accuracy/robustness evidence and operating instructions | product documentation and human review remain required |
| Article 13 — human oversight inputs | Are operational signals available to the people responsible for oversight? | exceptions, owner, SLA, escalation and resolution history | an alert does not prove that effective oversight occurred |

## Required package sections

1. Manifest: version, snapshot, period, scope and generator.
2. Requirement matrix: claim, status, evidence references and limitations.
3. Artifact inventory: media type, size and SHA-256 digest.
4. Coverage statement: real, synthetic, inferred, conceptual or unavailable.
5. Exceptions and unresolved gaps.
6. Verification instructions that do not require administrative access.

Statuses are `supported`, `partially_supported`, `not_supported` and `not_applicable`. A positive status without evidence is invalid.
