package hmacconverter

import (
	"context"
	"strings"
	"testing"

	"github.com/open-telemetry/opentelemetry-collector-contrib/pkg/ottl"
)

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
