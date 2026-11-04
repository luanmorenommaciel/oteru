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
	HMACKeyEnv            = "OTERU_PII_HMAC_KEY"
	MinProductionKeyBytes = 32
)

var (
	ErrKeyUnset    = errors.New("hmac key is unset")
	ErrKeyEmpty    = errors.New("hmac key is empty")
	ErrKeyTooShort = errors.New("hmac key is shorter than 32 bytes")

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

// ValidateProductionKey rejects nil, empty, and keys shorter than 32 bytes.
// The error text never includes the key material.
func ValidateProductionKey(key []byte) error {
	if key == nil {
		return ErrKeyUnset
	}
	if len(key) == 0 {
		return ErrKeyEmpty
	}
	if len(key) < MinProductionKeyBytes {
		return fmt.Errorf("%w (got %d)", ErrKeyTooShort, len(key))
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
