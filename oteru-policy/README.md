# Oteru policy decisions

Reference shadow decision API for issue #23 and ADR-004. Policies start in `warn`; `enforce` requires shadow evidence, security review and approval. Break Glass is scoped, expires within 60 minutes and never disables the content-free audit decision.

This package is not a provider gateway and does not put blocking in the request path.
