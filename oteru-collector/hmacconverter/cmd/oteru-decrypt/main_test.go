package main

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/luanmorenommaciel/oteru/oteru-collector/hmacconverter"
)

func makeFixture(t *testing.T) (string, string, string) {
	t.Helper()
	dir := t.TempDir()
	key := strings.Repeat("9", 64)
	keyringJSON := `{"version":1,"active_key_id":"test-v1","keys":{"test-v1":"` + key + `"}}`
	keyFile := filepath.Join(dir, "keyring.json")
	if err := os.WriteFile(keyFile, []byte(keyringJSON), 0o600); err != nil {
		t.Fatal(err)
	}
	keyring, err := hmacconverter.ParseKeyring([]byte(keyringJSON))
	if err != nil {
		t.Fatal(err)
	}
	ciphertext, err := hmacconverter.EncryptValue(keyring, "user.email", []byte("synthetic@example.invalid"))
	if err != nil {
		t.Fatal(err)
	}
	return keyFile, filepath.Join(dir, "audit.jsonl"), ciphertext
}

func invoke(t *testing.T, key, audit, ciphertext string, terminal bool, purpose string) (string, string, error) {
	t.Helper()
	var output, diagnostics bytes.Buffer
	err := run([]string{"--key-file", key, "--audit-file", audit, "--field", "user.email", "--purpose", purpose}, strings.NewReader(ciphertext+"\n"), &output, &diagnostics, func(int) bool { return terminal })
	return output.String(), diagnostics.String(), err
}

func readAudit(t *testing.T, path string) auditRecordForTest {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var record auditRecordForTest
	if err := json.Unmarshal(bytes.TrimSpace(data), &record); err != nil {
		t.Fatal(err)
	}
	return record
}

type auditRecordForTest struct {
	Outcome          string `json:"outcome"`
	ActorUID         string `json:"actor_uid"`
	Purpose          string `json:"purpose"`
	CiphertextSHA256 string `json:"ciphertext_sha256"`
}

func TestDecryptWritesAuditBeforeReleasingPlaintext(t *testing.T) {
	key, audit, ciphertext := makeFixture(t)
	output := &auditGateWriter{path: audit}
	err := run(
		[]string{"--key-file", key, "--audit-file", audit, "--field", "user.email", "--purpose", "audit-review"},
		strings.NewReader(ciphertext+"\n"),
		output,
		io.Discard,
		func(int) bool { return true },
	)
	if err != nil {
		t.Fatal(err)
	}
	if output.String() != "synthetic@example.invalid\n" {
		t.Fatalf("unexpected plaintext output %q", output.String())
	}
	record := readAudit(t, audit)
	if record.Outcome != "release-authorized" || record.ActorUID == "" || record.Purpose != "audit-review" {
		t.Fatalf("missing audit metadata: %#v", record)
	}
	if record.CiphertextSHA256 != hmacconverter.CiphertextFingerprint(ciphertext) {
		t.Fatal("audit record does not fingerprint the ciphertext")
	}
}

type auditGateWriter struct {
	path string
	bytes.Buffer
}

func (writer *auditGateWriter) Write(value []byte) (int, error) {
	content, err := os.ReadFile(writer.path)
	if err != nil {
		return 0, err
	}
	var record auditRecordForTest
	if err := json.Unmarshal(bytes.TrimSpace(content), &record); err != nil {
		return 0, err
	}
	if record.Outcome != "release-authorized" {
		return 0, errors.New("plaintext write occurred before release audit")
	}
	return writer.Buffer.Write(value)
}

func TestDecryptBlocksNonTerminalAndAuditsAttempt(t *testing.T) {
	key, audit, ciphertext := makeFixture(t)
	output, _, err := invoke(t, key, audit, ciphertext, false, "incident-response")
	if err == nil || output != "" {
		t.Fatalf("non-terminal decrypt was not blocked: output=%q err=%v", output, err)
	}
	if record := readAudit(t, audit); record.Outcome != "blocked-non-tty" {
		t.Fatalf("unexpected audit outcome: %#v", record)
	}
}

func TestDecryptFailsClosedWhenAuditCannotBeWritten(t *testing.T) {
	key, audit, ciphertext := makeFixture(t)
	if err := os.Mkdir(audit, 0o700); err != nil {
		t.Fatal(err)
	}
	output, _, err := invoke(t, key, audit, ciphertext, true, "customer-request")
	if err == nil || output != "" {
		t.Fatalf("plaintext escaped without a durable audit: output=%q err=%v", output, err)
	}
}

func TestDecryptFailsClosedWhenAuditChainIsCorrupt(t *testing.T) {
	key, audit, ciphertext := makeFixture(t)
	if err := hmacconverter.AppendDecryptAudit(audit, hmacconverter.DecryptAuditRequest{
		ActorUID:         "501",
		Purpose:          "audit-review",
		Field:            "user.email",
		KeyID:            hmacconverter.KeyIDFromEnvelope(ciphertext),
		CiphertextSHA256: hmacconverter.CiphertextFingerprint(ciphertext),
		Outcome:          "decrypt-failed",
	}); err != nil {
		t.Fatal(err)
	}
	content, err := os.ReadFile(audit)
	if err != nil {
		t.Fatal(err)
	}
	content = bytes.Replace(content, []byte("audit-review"), []byte("case-review"), 1)
	if err := os.WriteFile(audit, content, 0o600); err != nil {
		t.Fatal(err)
	}
	output, _, err := invoke(t, key, audit, ciphertext, true, "audit-review")
	if err == nil || output != "" {
		t.Fatalf("plaintext escaped with a corrupt audit: output=%q err=%v", output, err)
	}
}

func TestDecryptFailureIsGenericAndAudited(t *testing.T) {
	key, audit, ciphertext := makeFixture(t)
	badKeyring := `{"version":1,"active_key_id":"other-v1","keys":{"other-v1":"` + hex.EncodeToString(bytes.Repeat([]byte{0x33}, 32)) + `"}}`
	if err := os.WriteFile(key, []byte(badKeyring), 0o600); err != nil {
		t.Fatal(err)
	}
	output, diagnostics, err := invoke(t, key, audit, ciphertext, true, "legal-request")
	if err == nil || output != "" || strings.Contains(diagnostics+err.Error(), "synthetic@example.invalid") {
		t.Fatalf("decrypt failure leaked data: output=%q diagnostics=%q err=%v", output, diagnostics, err)
	}
	if record := readAudit(t, audit); record.Outcome != "decrypt-failed" {
		t.Fatalf("unexpected audit outcome: %#v", record)
	}
}

func TestDecryptRejectsBroadlyReadableKeyring(t *testing.T) {
	key, audit, ciphertext := makeFixture(t)
	if err := os.Chmod(key, 0o644); err != nil {
		t.Fatal(err)
	}
	output, _, err := invoke(t, key, audit, ciphertext, true, "audit-review")
	if err == nil || output != "" {
		t.Fatalf("broadly readable keyring accepted: output=%q err=%v", output, err)
	}
}

func TestReadCiphertextRequiresExactlyOneBoundedLine(t *testing.T) {
	for name, input := range map[string]string{
		"empty":           "",
		"multiple values": "ciphertext-one\nciphertext-two\n",
		"oversized":       strings.Repeat("x", maxCiphertextBytes+1),
	} {
		t.Run(name, func(t *testing.T) {
			if value, err := readCiphertext(strings.NewReader(input)); err == nil || value != "" {
				t.Fatalf("invalid input accepted: value=%q err=%v", value, err)
			}
		})
	}
	if value, err := readCiphertext(strings.NewReader("one-ciphertext\r\n")); err != nil || value != "one-ciphertext" {
		t.Fatalf("single CRLF-terminated value rejected: value=%q err=%v", value, err)
	}
}
