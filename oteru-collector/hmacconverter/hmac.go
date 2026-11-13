// Package hmacconverter is a custom OTTL HMAC-SHA256 converter and a transform
// processor factory that binds OTERU_PII_HMAC_KEY at Create time.
package hmacconverter

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"sync/atomic"
)

const (
	HMACKeyEnv = "OTERU_PII_HMAC_KEY"
	// ProductionKeyHexLen is the exact length of a production key: 32 random
	// bytes rendered as lowercase hex, e.g.
	//   python -c 'import secrets;print(secrets.token_hex(32))'
	// The HMAC key material is the UTF-8 bytes of that 64-character string
	// (not the decoded bytes), so the hmac:v1 format stays unchanged.
	ProductionKeyHexLen = 64
)

var (
	ErrKeyUnset    = errors.New("hmac key is unset")
	ErrKeyEmpty    = errors.New("hmac key is empty")
	ErrKeyTooShort = errors.New("hmac key is shorter than 64 hex characters")
	ErrKeyTooLong  = errors.New("hmac key is longer than 64 hex characters")
	ErrKeyNotHex   = errors.New("hmac key is not lowercase hex")

	boundKey atomic.Pointer[[]byte]
)

// Digest returns lowercase hex HMAC-SHA256 of value keyed by key.
// This is crypto/hmac, not SHA256(key||value). Short keys are accepted so RFC
// 4231 cases 1-7 can be verified; production Create* calls ValidateProductionKey.
func Digest(key, value []byte) string {
	mac := hmac.New(sha256.New, key)
	_, _ = mac.Write(value)
	return hex.EncodeToString(mac.Sum(nil))
}

// ValidateProductionKey accepts only exactly 64 lowercase hex characters and
// rejects nil, empty, short, long and non-hex keys. The error text never
// includes the key material (only its length).
func ValidateProductionKey(key []byte) error {
	if key == nil {
		return ErrKeyUnset
	}
	if len(key) == 0 {
		return ErrKeyEmpty
	}
	if len(key) < ProductionKeyHexLen {
		return fmt.Errorf("%w (got %d)", ErrKeyTooShort, len(key))
	}
	if len(key) > ProductionKeyHexLen {
		return fmt.Errorf("%w (got %d)", ErrKeyTooLong, len(key))
	}
	for _, c := range key {
		if (c < '0' || c > '9') && (c < 'a' || c > 'f') {
			return ErrKeyNotHex
		}
	}
	return nil
}

func loadKeyFromEnv() ([]byte, error) {
	val, ok := os.LookupEnv(HMACKeyEnv)
	if !ok {
		return nil, fmt.Errorf("environment variable %s is unset", HMACKeyEnv)
	}
	key := []byte(val)
	if err := ValidateProductionKey(key); err != nil {
		return nil, fmt.Errorf("environment variable %s: %w", HMACKeyEnv, err)
	}
	return append([]byte(nil), key...), nil
}

// bindKeyFromEnv copies OTERU_PII_HMAC_KEY into process-local memory.
// Called from CreateLogs/Traces/Metrics/Profiles. Errors never include the key.
func bindKeyFromEnv() error {
	key, err := loadKeyFromEnv()
	if err != nil {
		return err
	}
	boundKey.Store(&key)
	return nil
}

func currentKey() ([]byte, error) {
	p := boundKey.Load()
	if p == nil {
		return nil, ErrKeyUnset
	}
	if err := ValidateProductionKey(*p); err != nil {
		return nil, err
	}
	return *p, nil
}
