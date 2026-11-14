# Persistent compliance buffer reference

This package is a local reference implementation of ADR-002. It uses SQLite WAL plus `synchronous=FULL`, returns success only after the transaction commits, leases events for at-least-once delivery, rejects conflicting duplicate IDs and moves exhausted events to a DLQ state.

It is not yet wired into the Collector or production storage. Integration still requires capacity planning, encryption-at-rest controls, tenant credential derivation, metrics and the failure tests described by #6.
