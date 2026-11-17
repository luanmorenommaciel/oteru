# Persistent compliance buffer reference

This package is a local reference implementation of ADR-002. It uses SQLite WAL
plus `synchronous=FULL`, returns success only after the transaction commits,
leases events for at-least-once delivery, scopes idempotency by tenant, and moves
exhausted events to a DLQ state. Every lease carries an opaque token; ACK and
NACK reject stale or expired consumers. Acknowledgement tombstones prevent an
already accepted event from being inserted again.

Payloads are encrypted with AES-256-GCM before SQLite receives them. Callers must
provide a 32-byte key and keep it stable for the lifetime of the database; key
storage, rotation and recovery remain deployment responsibilities.

It is not yet wired into the Collector or production storage. Integration still
requires capacity planning, tenant credential derivation, metrics, key custody,
rotation and the operational failure tests described by #6.
