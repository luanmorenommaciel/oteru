# hmacconverter

Custom OTTL converter `HMACSHA256(value)` plus a transform processor factory
that binds the key from `OTERU_PII_HMAC_KEY` when the Collector creates the
processor. The PII policy emits `hmac:v1:` + `HMACSHA256("oteru-pii:v1|<field>|<value>")`.

## Key

- Source: the `OTERU_PII_HMAC_KEY` environment variable only. There is no
  default, no config field, no flag.
- Format: exactly 64 lowercase hex characters. Empty, shorter, longer,
  uppercase or non-hex values make every `Create*` call fail, so the
  Collector does not start.
- Generate: `python -c 'import secrets;print(secrets.token_hex(32))'`
- Key material: the UTF-8 bytes of that 64-character string (the same bytes
  Python's `hmac.new(key.encode(), ...)` uses). This keeps `hmac:v1` stable.
- Errors and logs report only the failure kind and length, never the key.

## Tests

```sh
cd oteru-collector/hmacconverter && go vet ./... && go test ./...
```

Keys are generated in memory with `crypto/rand`. `testdata/hmac_v1_crosscheck.json`
holds a public TEST-ONLY key, stored split as `0123456789abcdef` x4 and values precomputed
with Python `hmac.new`; `../tests/test_hmac_v1_crosscheck.py` re-derives them.
