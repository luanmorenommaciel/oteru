package hmacconverter

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/open-telemetry/opentelemetry-collector-contrib/pkg/ottl"
)

func bindTestEncryptionKeyring(t *testing.T) {
	t.Helper()
	previous := boundEncryptionKeyring.Load()
	t.Cleanup(func() { boundEncryptionKeyring.Store(previous) })
	keyring, err := ParseKeyring([]byte(`{"version":1,"active_key_id":"test-v1","keys":{"test-v1":"` + strings.Repeat("a", 64) + `"}}`))
	if err != nil {
		t.Fatal(err)
	}
	boundEncryptionKeyring.Store(&encryptionKeyringState{keyring: keyring})
}

func bindTestKey(t *testing.T, key string) {
	t.Helper()
	previous := boundKey.Load()
	t.Cleanup(func() { boundKey.Store(previous) })
	t.Setenv(HMACKeyEnv, key)
	if err := bindKeyFromEnv(); err != nil {
		t.Fatal(err)
	}
}

func TestHMACSHA256OTTLEmptyValue(t *testing.T) {
	key := randomKey(t)
	bindTestKey(t, key)
	expr := hmacSHA256[any](
		&ottl.StandardStringGetter[any]{Getter: func(context.Context, any) (any, error) { return "", nil }},
	)
	got, err := expr(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	want := Digest([]byte(key), []byte(""))
	if got != want {
		t.Fatalf("got %v want %s", got, want)
	}
}

func TestHMACSHA256OTTLNilValue(t *testing.T) {
	key := randomKey(t)
	bindTestKey(t, key)
	expr := hmacSHA256[any](
		&ottl.StandardStringGetter[any]{Getter: func(context.Context, any) (any, error) { return nil, nil }},
	)
	_, err := expr(context.Background(), nil)
	if err == nil {
		t.Fatal("expected error for nil value")
	}
	if strings.Contains(err.Error(), key) {
		t.Fatalf("error leaked key: %v", err)
	}
}

func TestHMACSHA256OTTLRejectsUnboundKey(t *testing.T) {
	boundKey.Store(nil)
	probe := randomKey(t)
	expr := hmacSHA256[any](
		&ottl.StandardStringGetter[any]{Getter: func(context.Context, any) (any, error) {
			return "user@example.com", nil
		}},
	)
	_, err := expr(context.Background(), nil)
	if err == nil {
		t.Fatal("expected error")
	}
	if strings.Contains(err.Error(), probe) {
		t.Fatalf("error leaked key: %v", err)
	}
}

func TestNewHMACSHA256FactoryName(t *testing.T) {
	if NewHMACSHA256Factory[any]().Name() != "HMACSHA256" {
		t.Fatal("unexpected name")
	}
}

func TestAESGCMEncryptOTTLFunctionReturnsAuthenticatedCiphertext(t *testing.T) {
	bindTestEncryptionKeyring(t)
	expr := aesGCMEncrypt[any](
		&ottl.StandardStringGetter[any]{Getter: func(context.Context, any) (any, error) {
			return "person@example.invalid", nil
		}},
		&ottl.StandardStringGetter[any]{Getter: func(context.Context, any) (any, error) {
			return "user.email", nil
		}},
	)
	got, err := expr(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	ciphertext, ok := got.(string)
	if !ok || strings.Contains(ciphertext, "person@example.invalid") {
		t.Fatalf("expected ciphertext, got %#v", got)
	}
	keyring, err := ParseKeyring([]byte(`{"version":1,"active_key_id":"test-v1","keys":{"test-v1":"` + strings.Repeat("a", 64) + `"}}`))
	if err != nil {
		t.Fatal(err)
	}
	plaintext, _, err := DecryptValue(keyring, "user.email", ciphertext)
	if err != nil || string(plaintext) != "person@example.invalid" {
		t.Fatalf("decrypt got %q: %v", plaintext, err)
	}
}

func TestAESGCMEncryptFactoryFailsWithoutKeyring(t *testing.T) {
	previous := boundEncryptionKeyring.Load()
	boundEncryptionKeyring.Store(nil)
	t.Cleanup(func() { boundEncryptionKeyring.Store(previous) })
	_, err := createAESGCMEncryptFunction[any](ottl.FunctionContext{}, &AESGCMEncryptArguments[any]{})
	if err == nil || !strings.Contains(err.Error(), "keyring") {
		t.Fatalf("expected missing keyring error, got %v", err)
	}
}

func TestCiphertextFingerprintDoesNotExposeCiphertext(t *testing.T) {
	ciphertext := "oteru:enc:v1:test-v1:user.email:ZmFrZQ"
	fingerprint := CiphertextFingerprint(ciphertext)
	if fingerprint == ciphertext || json.Valid([]byte(fingerprint)) || len(fingerprint) != 64 {
		t.Fatalf("unexpected fingerprint %q", fingerprint)
	}
}
