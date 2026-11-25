package hmacconverter

import (
	"bytes"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func decodeCiphertext(value string) ([]byte, error) {
	return base64.RawURLEncoding.DecodeString(value)
}

func encodeCiphertext(value []byte) string {
	return base64.RawURLEncoding.EncodeToString(value)
}

func testKeyringJSON(activeID string, entries map[string]string) []byte {
	var keys strings.Builder
	for keyID, key := range entries {
		if keys.Len() > 0 {
			keys.WriteByte(',')
		}
		fmt.Fprintf(&keys, "%q:%q", keyID, key)
	}
	return []byte(fmt.Sprintf(
		`{"version":1,"active_key_id":%q,"keys":{%s}}`, activeID, keys.String(),
	))
}

func TestEncryptValueUsesRandomNonceAndRoundTrips(t *testing.T) {
	keyHex := strings.Repeat("a", 64)
	keyring, err := ParseKeyring(testKeyringJSON("k1", map[string]string{"k1": keyHex}))
	if err != nil {
		t.Fatal(err)
	}
	plain := []byte("person@example.invalid")
	first, err := EncryptValue(keyring, "user.email", plain)
	if err != nil {
		t.Fatal(err)
	}
	second, err := EncryptValue(keyring, "user.email", plain)
	if err != nil {
		t.Fatal(err)
	}
	if first == second {
		t.Fatal("encrypting the same value twice reused the nonce or ciphertext")
	}
	got, keyID, err := DecryptValue(keyring, "user.email", first)
	if err != nil {
		t.Fatal(err)
	}
	if keyID != "k1" || !bytes.Equal(got, plain) {
		t.Fatalf("round trip got key_id=%q plaintext=%q", keyID, got)
	}
	if strings.Contains(first, string(plain)) {
		t.Fatal("ciphertext contains plaintext")
	}
}

func TestDecryptValueRejectsTamperingWrongFieldAndUnknownKey(t *testing.T) {
	keyring, err := ParseKeyring(testKeyringJSON("k1", map[string]string{"k1": strings.Repeat("b", 64)}))
	if err != nil {
		t.Fatal(err)
	}
	ciphertext, err := EncryptValue(keyring, "user.id", []byte("synthetic-id"))
	if err != nil {
		t.Fatal(err)
	}
	parts := strings.Split(ciphertext, ":")
	sealed, err := decodeCiphertext(parts[len(parts)-1])
	if err != nil {
		t.Fatal(err)
	}
	sealed[len(sealed)-1] ^= 0x01
	parts[len(parts)-1] = encodeCiphertext(sealed)
	tampered := strings.Join(parts, ":")
	for name, value := range map[string]struct {
		field string
		text  string
	}{
		"tampered":    {field: "user.id", text: tampered},
		"wrong field": {field: "user.email", text: ciphertext},
		"unknown key": {field: "user.id", text: strings.Replace(ciphertext, ":k1:", ":missing:", 1)},
		"truncated":   {field: "user.id", text: ciphertext[:len(ciphertext)-4]},
	} {
		t.Run(name, func(t *testing.T) {
			if plaintext, _, err := DecryptValue(keyring, value.field, value.text); err == nil || plaintext != nil {
				t.Fatalf("expected closed failure; plaintext=%q err=%v", plaintext, err)
			}
		})
	}
}

func TestKeyringRejectsMalformedAndAmbiguousInput(t *testing.T) {
	validKey := strings.Repeat("c", 64)
	for name, input := range map[string][]byte{
		"duplicate top-level field": []byte(fmt.Sprintf(`{"version":1,"version":1,"active_key_id":"k1","keys":{"k1":%q}}`, validKey)),
		"duplicate key id":          []byte(fmt.Sprintf(`{"version":1,"active_key_id":"k1","keys":{"k1":%q,"k1":%q}}`, validKey, validKey)),
		"unknown field":             []byte(fmt.Sprintf(`{"version":1,"active_key_id":"k1","keys":{"k1":%q},"extra":true}`, validKey)),
		"missing active key":        []byte(fmt.Sprintf(`{"version":1,"active_key_id":"missing","keys":{"k1":%q}}`, validKey)),
		"short key":                 []byte(`{"version":1,"active_key_id":"k1","keys":{"k1":"abcd"}}`),
		"uppercase key":             []byte(fmt.Sprintf(`{"version":1,"active_key_id":"k1","keys":{"k1":%q}}`, strings.Repeat("A", 64))),
		"invalid field key id":      []byte(fmt.Sprintf(`{"version":1,"active_key_id":"../k1","keys":{"../k1":%q}}`, validKey)),
		"oversized input":           bytes.Repeat([]byte(" "), maxKeyringBytes+1),
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := ParseKeyring(input); err == nil {
				t.Fatal("expected invalid keyring to be rejected")
			}
		})
	}
}

func TestLoadKeyringFileRejectsSymlinksAndBroadPermissions(t *testing.T) {
	dir := t.TempDir()
	content := testKeyringJSON("k1", map[string]string{"k1": strings.Repeat("f", 64)})
	file := filepath.Join(dir, "keyring.json")
	if err := os.WriteFile(file, content, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadKeyringFile(file); err != nil {
		t.Fatalf("protected regular keyring rejected: %v", err)
	}
	link := filepath.Join(dir, "keyring-link.json")
	if err := os.Symlink(file, link); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadKeyringFile(link); err == nil {
		t.Fatal("keyring symlink accepted")
	}
	if err := os.Chmod(file, 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadKeyringFile(file); err == nil {
		t.Fatal("broadly readable keyring accepted")
	}
}

func TestParseKeyringKeepsOldKeysForDecrypt(t *testing.T) {
	oldKey := strings.Repeat("d", 64)
	newKey := strings.Repeat("e", 64)
	keyring, err := ParseKeyring(testKeyringJSON("new", map[string]string{"old": oldKey, "new": newKey}))
	if err != nil {
		t.Fatal(err)
	}
	oldRing, err := ParseKeyring(testKeyringJSON("old", map[string]string{"old": oldKey}))
	if err != nil {
		t.Fatal(err)
	}
	ciphertext, err := EncryptValue(oldRing, "host.name", []byte("host.invalid"))
	if err != nil {
		t.Fatal(err)
	}
	plaintext, keyID, err := DecryptValue(keyring, "host.name", ciphertext)
	if err != nil || keyID != "old" || string(plaintext) != "host.invalid" {
		t.Fatalf("old ciphertext was not recoverable: key_id=%q plaintext=%q err=%v", keyID, plaintext, err)
	}
}

func TestEncryptionOnlyAcceptsCataloguedIdentityFields(t *testing.T) {
	keyring, err := ParseKeyring(testKeyringJSON("k1", map[string]string{"k1": hex.EncodeToString(bytes.Repeat([]byte{0xf}, 32))}))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := EncryptValue(keyring, "prompt.content", []byte("synthetic")); err == nil {
		t.Fatal("non-catalogued field must not be encrypted through this contract")
	}
}
