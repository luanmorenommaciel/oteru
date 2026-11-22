package hmacconverter

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"sync/atomic"
	"syscall"
)

const (
	EncryptionKeyringPath = "/run/secrets/oteru_pii_keyring"
	maxKeyringBytes       = 64 * 1024
	maxKeyringEntries     = 32
	maxKeyringJSONDepth   = 8
	encryptionEnvelopeV1  = "oteru:enc:v1"
)

var (
	ErrKeyringUnavailable = errors.New("encryption keyring is unavailable")
	ErrInvalidCiphertext  = errors.New("invalid encrypted value")
	ErrUnsupportedField   = errors.New("field is not supported for encryption")

	encryptedIdentityFields = map[string]struct{}{
		"user.email":        {},
		"user.id":           {},
		"user.account_uuid": {},
		"user.account_id":   {},
		"organization.id":   {},
		"host.name":         {},
	}

	boundEncryptionKeyring atomic.Pointer[encryptionKeyringState]
)

type encryptionKeyringState struct {
	keyring *EncryptionKeyring
	err     error
}

// EncryptionKeyring is an installation-local keyring. The JSON source holds
// hex-encoded key material; callers must never log or format this value.
type EncryptionKeyring struct {
	activeKeyID string
	keys        map[string][]byte
}

// ParseKeyring validates the strict, bounded secret-file contract.
func ParseKeyring(raw []byte) (*EncryptionKeyring, error) {
	if len(raw) == 0 || len(raw) > maxKeyringBytes {
		return nil, ErrKeyringUnavailable
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	value, err := decodeKeyringJSON(decoder, 0)
	if err != nil {
		return nil, ErrKeyringUnavailable
	}
	if _, err := decoder.Token(); err != io.EOF {
		return nil, ErrKeyringUnavailable
	}
	root, ok := value.(map[string]any)
	if !ok || !hasOnlyKeys(root, "version", "active_key_id", "keys") {
		return nil, ErrKeyringUnavailable
	}
	version, ok := root["version"].(json.Number)
	if !ok || version.String() != "1" {
		return nil, ErrKeyringUnavailable
	}
	activeID, ok := root["active_key_id"].(string)
	if !ok || !validKeyID(activeID) {
		return nil, ErrKeyringUnavailable
	}
	keyValues, ok := root["keys"].(map[string]any)
	if !ok || len(keyValues) == 0 || len(keyValues) > maxKeyringEntries {
		return nil, ErrKeyringUnavailable
	}
	keys := make(map[string][]byte, len(keyValues))
	for keyID, value := range keyValues {
		encoded, ok := value.(string)
		if !validKeyID(keyID) || !ok || len(encoded) != 64 {
			return nil, ErrKeyringUnavailable
		}
		key, err := hex.DecodeString(encoded)
		if err != nil || len(key) != 32 || hex.EncodeToString(key) != encoded {
			return nil, ErrKeyringUnavailable
		}
		keys[keyID] = key
	}
	if _, ok := keys[activeID]; !ok {
		return nil, ErrKeyringUnavailable
	}
	return &EncryptionKeyring{activeKeyID: activeID, keys: keys}, nil
}

// LoadKeyringFile reads and validates the mounted installation keyring.
func LoadKeyringFile(path string) (*EncryptionKeyring, error) {
	fd, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return nil, errors.New("encryption keyring file is unavailable")
	}
	file := os.NewFile(uintptr(fd), "oteru-pii-keyring")
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return nil, errors.New("encryption keyring file is unavailable")
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0o077 != 0 {
		return nil, errors.New("encryption keyring file permissions are invalid")
	}
	raw, err := io.ReadAll(io.LimitReader(file, maxKeyringBytes+1))
	if err != nil {
		return nil, errors.New("encryption keyring file cannot be read")
	}
	if len(raw) > maxKeyringBytes {
		return nil, errors.New("encryption keyring contents are invalid")
	}
	keyring, err := ParseKeyring(raw)
	if err != nil {
		return nil, errors.New("encryption keyring contents are invalid")
	}
	return keyring, nil
}

// BindOptionalKeyringFile records the mounted keyring state without failing a
// Collector that has no Encrypt statements. An Encrypt converter then fails
// during processor creation if the secret is missing or invalid.
func BindOptionalKeyringFile(path string) {
	keyring, err := LoadKeyringFile(path)
	boundEncryptionKeyring.Store(&encryptionKeyringState{keyring: keyring, err: err})
}

// EncryptCurrent uses the active key in the mounted keyring.
func EncryptCurrent(field, value string) (string, error) {
	state := boundEncryptionKeyring.Load()
	if state == nil || state.err != nil || state.keyring == nil {
		return "", ErrKeyringUnavailable
	}
	return EncryptValue(state.keyring, field, []byte(value))
}

// EncryptionKeyringReady reports whether an active keyring has been bound.
func EncryptionKeyringReady() error {
	state := boundEncryptionKeyring.Load()
	if state == nil {
		return ErrKeyringUnavailable
	}
	if state.err != nil {
		return state.err
	}
	if state.keyring == nil {
		return ErrKeyringUnavailable
	}
	return nil
}

// EncryptValue seals one catalogued identity string with AES-256-GCM.
func EncryptValue(keyring *EncryptionKeyring, field string, plaintext []byte) (string, error) {
	if !supportedEncryptionField(field) {
		return "", ErrUnsupportedField
	}
	if keyring == nil {
		return "", ErrKeyringUnavailable
	}
	key, ok := keyring.keys[keyring.activeKeyID]
	if !ok {
		return "", ErrKeyringUnavailable
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return "", ErrKeyringUnavailable
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return "", ErrKeyringUnavailable
	}
	nonce := make([]byte, aead.NonceSize())
	if _, err := io.ReadFull(rand.Reader, nonce); err != nil {
		return "", errors.New("encryption random source unavailable")
	}
	sealed := aead.Seal(nil, nonce, plaintext, encryptionAAD(field))
	payload := append(nonce, sealed...)
	return fmt.Sprintf(
		"%s:%s:%s:%s",
		encryptionEnvelopeV1,
		keyring.activeKeyID,
		field,
		base64.RawURLEncoding.EncodeToString(payload),
	), nil
}

// DecryptValue authenticates an envelope and returns plaintext only when the
// caller's expected field matches the field bound into the authenticated data.
func DecryptValue(keyring *EncryptionKeyring, expectedField, envelope string) ([]byte, string, error) {
	if keyring == nil || !supportedEncryptionField(expectedField) {
		return nil, "", ErrInvalidCiphertext
	}
	parts := strings.Split(envelope, ":")
	if len(parts) != 6 || strings.Join(parts[:3], ":") != encryptionEnvelopeV1 {
		return nil, "", ErrInvalidCiphertext
	}
	keyID, field := parts[3], parts[4]
	if !validKeyID(keyID) || field != expectedField || !supportedEncryptionField(field) {
		return nil, "", ErrInvalidCiphertext
	}
	key, ok := keyring.keys[keyID]
	if !ok {
		return nil, "", ErrInvalidCiphertext
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[5])
	if err != nil {
		return nil, "", ErrInvalidCiphertext
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, "", ErrInvalidCiphertext
	}
	aead, err := cipher.NewGCM(block)
	if err != nil || len(payload) < aead.NonceSize()+aead.Overhead() {
		return nil, "", ErrInvalidCiphertext
	}
	nonce, sealed := payload[:aead.NonceSize()], payload[aead.NonceSize():]
	plaintext, err := aead.Open(nil, nonce, sealed, encryptionAAD(field))
	if err != nil {
		return nil, "", ErrInvalidCiphertext
	}
	return plaintext, keyID, nil
}

// KeyIDFromEnvelope returns a validated, non-secret key identifier for audit.
func KeyIDFromEnvelope(envelope string) string {
	parts := strings.Split(envelope, ":")
	if len(parts) != 6 || strings.Join(parts[:3], ":") != encryptionEnvelopeV1 || !validKeyID(parts[3]) {
		return "unknown"
	}
	return parts[3]
}

// CiphertextFingerprint is suitable for an audit record and does not disclose
// the ciphertext or plaintext.
func CiphertextFingerprint(envelope string) string {
	digest := sha256.Sum256([]byte(envelope))
	return hex.EncodeToString(digest[:])
}

func encryptionAAD(field string) []byte { return []byte("oteru-pii:enc:v1|" + field) }

func supportedEncryptionField(field string) bool {
	_, ok := encryptedIdentityFields[field]
	return ok
}

// SupportsEncryptionField reports whether field is in the V1 identity catalog.
func SupportsEncryptionField(field string) bool {
	return supportedEncryptionField(field)
}

func validKeyID(value string) bool {
	if len(value) == 0 || len(value) > 64 {
		return false
	}
	for _, char := range value {
		if (char < 'a' || char > 'z') && (char < 'A' || char > 'Z') &&
			(char < '0' || char > '9') && char != '-' && char != '_' && char != '.' {
			return false
		}
	}
	return true
}

func hasOnlyKeys(value map[string]any, keys ...string) bool {
	if len(value) != len(keys) {
		return false
	}
	for _, key := range keys {
		if _, ok := value[key]; !ok {
			return false
		}
	}
	return true
}

func decodeKeyringJSON(decoder *json.Decoder, depth int) (any, error) {
	if depth > maxKeyringJSONDepth {
		return nil, ErrKeyringUnavailable
	}
	token, err := decoder.Token()
	if err != nil {
		return nil, ErrKeyringUnavailable
	}
	delim, isDelim := token.(json.Delim)
	if !isDelim {
		return token, nil
	}
	switch delim {
	case '{':
		result := map[string]any{}
		for decoder.More() {
			keyToken, err := decoder.Token()
			key, ok := keyToken.(string)
			if err != nil || !ok {
				return nil, ErrKeyringUnavailable
			}
			if _, exists := result[key]; exists {
				return nil, ErrKeyringUnavailable
			}
			value, err := decodeKeyringJSON(decoder, depth+1)
			if err != nil {
				return nil, err
			}
			result[key] = value
		}
		end, err := decoder.Token()
		if err != nil || end != json.Delim('}') {
			return nil, ErrKeyringUnavailable
		}
		return result, nil
	case '[':
		for decoder.More() {
			if _, err := decodeKeyringJSON(decoder, depth+1); err != nil {
				return nil, err
			}
		}
		end, err := decoder.Token()
		if err != nil || end != json.Delim(']') {
			return nil, ErrKeyringUnavailable
		}
		return []any{}, nil
	default:
		return nil, ErrKeyringUnavailable
	}
}
