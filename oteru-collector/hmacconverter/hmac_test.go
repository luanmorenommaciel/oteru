package hmacconverter

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"strings"
	"testing"
)

func randomKey(t *testing.T) string {
	t.Helper()
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		t.Fatal(err)
	}
	return hex.EncodeToString(b)
}

func randomShortKey(t *testing.T, n int) string {
	t.Helper()
	if n <= 0 {
		t.Fatal("short key length must be positive")
	}
	return randomKey(t)[:n]
}

// TestRFC4231HMACSHA256 checks HMAC-SHA256 against RFC 4231 test vectors
// (https://www.rfc-editor.org/rfc/rfc4231). These keys and messages are
// public RFC 4231 test vectors, not repository secrets.
func TestRFC4231HMACSHA256(t *testing.T) {
	mustHex := func(s string) []byte {
		b, err := hex.DecodeString(strings.ReplaceAll(s, " ", ""))
		if err != nil {
			t.Fatal(err)
		}
		return b
	}
	// RFC 4231 test vectors, cases 1–7 (HMAC-SHA-256).
	cases := []struct {
		name     string
		key      []byte
		data     []byte
		wantFull string
		trunc128 string
	}{
		{
			name:     "RFC 4231 test vector 1",
			key:      mustHex("0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b"),
			data:     []byte("Hi There"),
			wantFull: "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7",
		},
		{
			name:     "RFC 4231 test vector 2",
			key:      []byte("Jefe"),
			data:     []byte("what do ya want for nothing?"),
			wantFull: "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843",
		},
		{
			name:     "RFC 4231 test vector 3",
			key:      mustHex("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
			data:     mustHex("dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"),
			wantFull: "773ea91e36800e46854db8ebd09181a72959098b3ef8c122d9635514ced565fe",
		},
		{
			name:     "RFC 4231 test vector 4",
			key:      mustHex("0102030405060708090a0b0c0d0e0f10111213141516171819"),
			data:     mustHex("cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"),
			wantFull: "82558a389a443c0ea4cc819899f2083a85f0faa3e578f8077a2e3ff46729665b",
		},
		{
			name:     "RFC 4231 test vector 5 (128-bit truncation)",
			key:      mustHex("0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c"),
			data:     []byte("Test With Truncation"),
			wantFull: "a3b6167473100ee06e0c796c2955552bfa6f7c0a6a8aef8b93f860aab0cd20c5",
			trunc128: "a3b6167473100ee06e0c796c2955552b",
		},
		{
			name:     "RFC 4231 test vector 6",
			key:      mustHex(strings.Repeat("aa", 131)),
			data:     []byte("Test Using Larger Than Block-Size Key - Hash Key First"),
			wantFull: "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54",
		},
		{
			name:     "RFC 4231 test vector 7",
			key:      mustHex(strings.Repeat("aa", 131)),
			data:     []byte("This is a test using a larger than block-size key and a larger than block-size data. The key needs to be hashed before being used by the HMAC algorithm."),
			wantFull: "9b09ffa71b942fcb27635fbcd5b0e944bfdc63644f0713938a7f51535c3a35e2",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := Digest(tc.key, tc.data)
			if got != tc.wantFull {
				t.Fatalf("full digest mismatch\n got %s\nwant %s", got, tc.wantFull)
			}
			if tc.trunc128 != "" && got[:32] != tc.trunc128 {
				t.Fatalf("RFC 128-bit truncation mismatch got %s want %s", got[:32], tc.trunc128)
			}
			if got != strings.ToLower(got) {
				t.Fatal("digest is not lowercase hex")
			}
		})
	}
}

func TestDigestEmptyValue(t *testing.T) {
	key := []byte(randomKey(t))
	got := Digest(key, []byte{})
	want := Digest(key, []byte{})
	if got != want {
		t.Fatalf("got %s want %s", got, want)
	}
	if got == Digest(key, []byte("x")) {
		t.Fatal("empty and nonempty values produced the same digest")
	}
}

func TestValidateProductionKey(t *testing.T) {
	secret := randomKey(t)
	tests := []struct {
		name    string
		key     []byte
		wantErr error
	}{
		{name: "nil", key: nil, wantErr: ErrKeyUnset},
		{name: "empty", key: []byte{}, wantErr: ErrKeyEmpty},
		{name: "short 31", key: []byte(randomShortKey(t, 31)), wantErr: ErrKeyTooShort},
		{name: "ok 32", key: []byte(randomShortKey(t, 32))},
		{name: "ok long", key: []byte(secret)},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			err := ValidateProductionKey(tc.key)
			if tc.wantErr == nil {
				if err != nil {
					t.Fatalf("unexpected error: %v", err)
				}
				return
			}
			if !errors.Is(err, tc.wantErr) {
				t.Fatalf("got %v want %v", err, tc.wantErr)
			}
			if len(tc.key) > 0 && strings.Contains(err.Error(), string(tc.key)) {
				t.Fatalf("error leaked key material: %v", err)
			}
		})
	}
}

func unsetHMACKey(t *testing.T) {
	t.Helper()
	// t.Setenv cannot unset; register cleanup, then remove the variable.
	t.Setenv(HMACKeyEnv, randomKey(t))
	if err := os.Unsetenv(HMACKeyEnv); err != nil {
		t.Fatal(err)
	}
}

func TestLoadKeyFromEnvErrorsOmitSecret(t *testing.T) {
	secret := randomKey(t)
	t.Run("unset", func(t *testing.T) {
		unsetHMACKey(t)
		_, err := loadKeyFromEnv()
		if err == nil {
			t.Fatal("expected unset error")
		}
		if !strings.Contains(err.Error(), "unset") {
			t.Fatalf("got %v", err)
		}
		if strings.Contains(err.Error(), secret) {
			t.Fatalf("leaked: %v", err)
		}
	})
	t.Run("empty", func(t *testing.T) {
		t.Setenv(HMACKeyEnv, "")
		_, err := loadKeyFromEnv()
		if err == nil || !errors.Is(err, ErrKeyEmpty) {
			t.Fatalf("got %v", err)
		}
	})
	t.Run("short", func(t *testing.T) {
		short := randomShortKey(t, 31)
		t.Setenv(HMACKeyEnv, short)
		_, err := loadKeyFromEnv()
		if err == nil || !errors.Is(err, ErrKeyTooShort) {
			t.Fatalf("got %v", err)
		}
		if strings.Contains(err.Error(), short) {
			t.Fatalf("leaked key: %v", err)
		}
	})
	t.Run("ok", func(t *testing.T) {
		t.Setenv(HMACKeyEnv, secret)
		got, err := loadKeyFromEnv()
		if err != nil {
			t.Fatal(err)
		}
		if string(got) != secret {
			t.Fatal("key mismatch")
		}
	})
}

func TestErrorFormNeverContainsKey(t *testing.T) {
	secret := randomKey(t)
	err := fmt.Errorf("%w (got %d)", ErrKeyTooShort, 11)
	if strings.Contains(err.Error(), secret) {
		t.Fatal(err)
	}
}
