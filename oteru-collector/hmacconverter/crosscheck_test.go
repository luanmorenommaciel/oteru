package hmacconverter

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/open-telemetry/opentelemetry-collector-contrib/pkg/ottl"
)

// independentHMACv1 recomputes hmac:v1 with crypto/hmac directly, without
// going through Digest or the converter, as Sentinela's independent verifier.
func independentHMACv1(key, field, value string) string {
	mac := hmac.New(sha256.New, []byte(key))
	mac.Write([]byte("oteru-pii:v1|" + field + "|" + value))
	return "hmac:v1:" + hex.EncodeToString(mac.Sum(nil))
}

func runConverter(t *testing.T, message string) string {
	t.Helper()
	expr := hmacSHA256[any](
		&ottl.StandardStringGetter[any]{Getter: func(context.Context, any) (any, error) { return message, nil }},
	)
	got, err := expr(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	return "hmac:v1:" + got.(string)
}

// TestConverterMatchesIndependentHMAC binds a key generated in memory and
// compares the converter output for synthetic actor_ids, row by row, with an
// independent crypto/hmac computation. It also proves the output is not the
// plaintext and not a keyless SHA-256.
func TestConverterMatchesIndependentHMAC(t *testing.T) {
	key := randomKey(t)
	bindTestKey(t, key)
	for _, actor := range []string{"synthetic-actor-0001", "synthetic-actor-0002@example.invalid", ""} {
		msg := "oteru-pii:v1|actor_id|" + actor
		got := runConverter(t, msg)
		want := independentHMACv1(key, "actor_id", actor)
		if got != want {
			t.Fatalf("actor %q: converter %s != independent %s", actor, got, want)
		}
		if actor != "" && (got == "hmac:v1:"+actor) {
			t.Fatalf("actor %q passed through in plaintext", actor)
		}
		plain := sha256.Sum256([]byte(msg))
		if got == "hmac:v1:"+hex.EncodeToString(plain[:]) {
			t.Fatalf("actor %q hashed without key", actor)
		}
	}
	other := randomKey(t)
	if independentHMACv1(other, "actor_id", "synthetic-actor-0001") == runConverter(t, "oteru-pii:v1|actor_id|synthetic-actor-0001") {
		t.Fatal("digest does not depend on the key")
	}
}

type crosscheckFixture struct {
	KeyUnit   string `json:"public_test_pattern"`
	KeyRepeat int    `json:"public_test_repeat"`
	Cases     []struct {
		Field    string `json:"field"`
		Value    string `json:"value"`
		Message  string `json:"message"`
		Expected string `json:"expected"`
	} `json:"cases"`
}

// TestConverterMatchesPythonFixture checks the converter against values
// precomputed with Python hmac.new (testdata/hmac_v1_crosscheck.json, a
// public TEST-ONLY key). tests/test_hmac_v1_crosscheck.py re-derives the same
// values in Python, so Go and Python agree on the hmac:v1 format.
func TestConverterMatchesPythonFixture(t *testing.T) {
	raw, err := os.ReadFile("testdata/hmac_v1_crosscheck.json")
	if err != nil {
		t.Fatal(err)
	}
	var fx crosscheckFixture
	if err := json.Unmarshal(raw, &fx); err != nil {
		t.Fatal(err)
	}
	if len(fx.Cases) == 0 {
		t.Fatal("fixture has no cases")
	}
	key := strings.Repeat(fx.KeyUnit, fx.KeyRepeat)
	if fx.KeyUnit != "0123456789abcdef" || len(key) != ProductionKeyHexLen {
		t.Fatal("fixture key must stay the public TEST-ONLY key")
	}
	bindTestKey(t, key)
	for _, c := range fx.Cases {
		if c.Message != "oteru-pii:v1|"+c.Field+"|"+c.Value {
			t.Fatalf("fixture message mismatch for %q", c.Value)
		}
		if got := runConverter(t, c.Message); got != c.Expected {
			t.Fatalf("value %q: converter %s != python %s", c.Value, got, c.Expected)
		}
	}
}
