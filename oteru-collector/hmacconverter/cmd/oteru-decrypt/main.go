// Command oteru-decrypt releases one catalogued identity value after recording
// a durable local audit event. It is intentionally interactive-only.
package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/luanmorenommaciel/oteru/oteru-collector/hmacconverter"
	"golang.org/x/term"
)

const maxCiphertextBytes = 64 * 1024

type terminalCheck func(int) bool

func main() {
	if err := run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr, term.IsTerminal); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run(args []string, input io.Reader, output, diagnostics io.Writer, isTerminal terminalCheck) error {
	flags := flag.NewFlagSet("oteru-decrypt", flag.ContinueOnError)
	flags.SetOutput(diagnostics)
	keyFile := flags.String("key-file", hmacconverter.EncryptionKeyringPath, "installation keyring file")
	auditFile := flags.String("audit-file", "./oteru-decrypt-audit.jsonl", "local chained audit file")
	field := flags.String("field", "", "catalogued identity field bound to the ciphertext")
	purpose := flags.String("purpose", "", "reason: audit-review, incident-response, customer-request, or legal-request")
	if err := flags.Parse(args); err != nil {
		return errors.New("invalid decrypt request")
	}
	if flags.NArg() != 0 || !hmacconverter.SupportsEncryptionField(*field) || *purpose == "" {
		return errors.New("field and purpose are required; ciphertext must be provided on stdin")
	}
	ciphertext, err := readCiphertext(input)
	if err != nil {
		return errors.New("invalid ciphertext input")
	}
	if !isTerminal(int(os.Stdout.Fd())) {
		if err := appendAudit(*auditFile, *field, *purpose, ciphertext, hmacconverter.KeyIDFromEnvelope(ciphertext), "blocked-non-tty"); err != nil {
			return errors.New("decrypt blocked: audit unavailable")
		}
		return errors.New("decrypt requires an interactive terminal")
	}
	keyring, err := hmacconverter.LoadKeyringFile(*keyFile)
	if err != nil {
		if auditErr := appendAudit(*auditFile, *field, *purpose, ciphertext, hmacconverter.KeyIDFromEnvelope(ciphertext), "decrypt-failed"); auditErr != nil {
			return errors.New("decrypt blocked: audit unavailable")
		}
		return errors.New("decrypt failed")
	}
	plaintext, keyID, err := hmacconverter.DecryptValue(keyring, *field, ciphertext)
	if err != nil {
		if auditErr := appendAudit(*auditFile, *field, *purpose, ciphertext, hmacconverter.KeyIDFromEnvelope(ciphertext), "decrypt-failed"); auditErr != nil {
			return errors.New("decrypt blocked: audit unavailable")
		}
		return errors.New("decrypt failed")
	}
	defer clear(plaintext)
	if err := appendAudit(*auditFile, *field, *purpose, ciphertext, keyID, "release-authorized"); err != nil {
		return errors.New("decrypt blocked: audit unavailable")
	}
	if _, err := output.Write(plaintext); err != nil {
		return errors.New("plaintext output failed after audit was recorded")
	}
	if _, err := io.WriteString(output, "\n"); err != nil {
		return errors.New("plaintext output failed after audit was recorded")
	}
	return nil
}

func readCiphertext(input io.Reader) (string, error) {
	data, err := io.ReadAll(io.LimitReader(input, maxCiphertextBytes+1))
	if err != nil {
		return "", err
	}
	if len(data) > maxCiphertextBytes {
		return "", errors.New("invalid ciphertext size")
	}
	value := strings.TrimSuffix(string(data), "\n")
	value = strings.TrimSuffix(value, "\r")
	if value == "" || strings.ContainsAny(value, "\r\n") {
		return "", errors.New("invalid ciphertext input")
	}
	return value, nil
}

func appendAudit(path, field, purpose, ciphertext, keyID, outcome string) error {
	return hmacconverter.AppendDecryptAudit(path, hmacconverter.DecryptAuditRequest{
		ActorUID:         fmt.Sprint(os.Geteuid()),
		Purpose:          purpose,
		Field:            field,
		KeyID:            keyID,
		CiphertextSHA256: hmacconverter.CiphertextFingerprint(ciphertext),
		Outcome:          outcome,
	})
}
