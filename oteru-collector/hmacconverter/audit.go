package hmacconverter

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const maxAuditLineBytes = 16 * 1024

var allowedDecryptPurposes = map[string]struct{}{
	"audit-review":      {},
	"incident-response": {},
	"customer-request":  {},
	"legal-request":     {},
}

// DecryptAuditRequest contains metadata only. Never add plaintext or ciphertext.
type DecryptAuditRequest struct {
	ActorUID         string
	Purpose          string
	Field            string
	KeyID            string
	CiphertextSHA256 string
	Outcome          string
}

type auditUnsignedRecord struct {
	Version          int    `json:"version"`
	Sequence         uint64 `json:"sequence"`
	Timestamp        string `json:"timestamp"`
	ActorUID         string `json:"actor_uid"`
	Purpose          string `json:"purpose"`
	Field            string `json:"field"`
	KeyID            string `json:"key_id"`
	CiphertextSHA256 string `json:"ciphertext_sha256"`
	Outcome          string `json:"outcome"`
	PreviousHash     string `json:"previous_hash"`
}

type auditRecord struct {
	auditUnsignedRecord
	EventHash string `json:"event_hash"`
}

// AppendDecryptAudit validates the existing chain and durably appends one
// metadata-only audit entry. It holds an exclusive file lock across both steps.
func AppendDecryptAudit(path string, request DecryptAuditRequest) error {
	if err := validateAuditRequest(request); err != nil {
		return err
	}
	file, created, err := openAuditFile(path)
	if err != nil {
		return errors.New("decrypt audit is unavailable")
	}
	defer file.Close()
	if err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX); err != nil {
		return errors.New("decrypt audit is unavailable")
	}
	defer syscall.Flock(int(file.Fd()), syscall.LOCK_UN) //nolint:errcheck
	if created {
		if err := syncDirectory(filepath.Dir(path)); err != nil {
			return errors.New("decrypt audit is unavailable")
		}
	}
	sequence, previousHash, err := verifyAuditChain(file)
	if err != nil {
		return errors.New("decrypt audit chain is invalid")
	}
	unsigned := auditUnsignedRecord{
		Version:          1,
		Sequence:         sequence + 1,
		Timestamp:        time.Now().UTC().Format(time.RFC3339Nano),
		ActorUID:         request.ActorUID,
		Purpose:          request.Purpose,
		Field:            request.Field,
		KeyID:            request.KeyID,
		CiphertextSHA256: request.CiphertextSHA256,
		Outcome:          request.Outcome,
		PreviousHash:     previousHash,
	}
	eventHash, err := hashAuditRecord(unsigned)
	if err != nil {
		return errors.New("decrypt audit is unavailable")
	}
	record := auditRecord{auditUnsignedRecord: unsigned, EventHash: eventHash}
	encoded, err := json.Marshal(record)
	if err != nil {
		return errors.New("decrypt audit is unavailable")
	}
	if _, err := file.Seek(0, io.SeekEnd); err != nil {
		return errors.New("decrypt audit is unavailable")
	}
	if _, err := file.Write(append(encoded, '\n')); err != nil {
		return errors.New("decrypt audit is unavailable")
	}
	if err := file.Sync(); err != nil {
		return errors.New("decrypt audit is unavailable")
	}
	return nil
}

func validateAuditRequest(request DecryptAuditRequest) error {
	if request.ActorUID == "" {
		return errors.New("invalid actor")
	}
	uid, err := strconv.ParseUint(request.ActorUID, 10, 32)
	if err != nil || strconv.FormatUint(uid, 10) != request.ActorUID {
		return errors.New("invalid actor")
	}
	if _, ok := allowedDecryptPurposes[request.Purpose]; !ok {
		return errors.New("invalid purpose")
	}
	if !supportedEncryptionField(request.Field) {
		return errors.New("invalid field")
	}
	if request.KeyID != "unknown" && !validKeyID(request.KeyID) {
		return errors.New("invalid key id")
	}
	if len(request.CiphertextSHA256) != 64 {
		return errors.New("invalid ciphertext fingerprint")
	}
	fingerprint, err := hex.DecodeString(request.CiphertextSHA256)
	if err != nil || hex.EncodeToString(fingerprint) != request.CiphertextSHA256 {
		return errors.New("invalid ciphertext fingerprint")
	}
	if request.Outcome != "release-authorized" && request.Outcome != "decrypt-failed" && request.Outcome != "blocked-non-tty" {
		return errors.New("invalid outcome")
	}
	return nil
}

func openAuditFile(path string) (*os.File, bool, error) {
	if path == "" || strings.IndexByte(path, 0) >= 0 {
		return nil, false, errors.New("invalid audit path")
	}
	fd, err := syscall.Open(path, syscall.O_CREAT|syscall.O_EXCL|syscall.O_RDWR|syscall.O_APPEND|syscall.O_NOFOLLOW, 0o600)
	created := err == nil
	if err != nil {
		if err != syscall.EEXIST {
			return nil, false, err
		}
		fd, err = syscall.Open(path, syscall.O_RDWR|syscall.O_APPEND|syscall.O_NOFOLLOW, 0)
		if err != nil {
			return nil, false, err
		}
	}
	file := os.NewFile(uintptr(fd), path)
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0o077 != 0 {
		file.Close()
		return nil, false, errors.New("audit file permissions are too broad")
	}
	return file, created, nil
}

func syncDirectory(path string) error {
	directory, err := os.Open(path)
	if err != nil {
		return err
	}
	defer directory.Close()
	return directory.Sync()
}

func verifyAuditChain(file *os.File) (uint64, string, error) {
	info, err := file.Stat()
	if err != nil {
		return 0, "", err
	}
	if info.Size() > 0 {
		last := []byte{0}
		if _, err := file.ReadAt(last, info.Size()-1); err != nil || last[0] != '\n' {
			return 0, "", errors.New("audit log has a partial final record")
		}
	}
	if _, err := file.Seek(0, io.SeekStart); err != nil {
		return 0, "", err
	}
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 1024), maxAuditLineBytes)
	var sequence uint64
	previousHash := ""
	for scanner.Scan() {
		line := append([]byte(nil), scanner.Bytes()...)
		var record auditRecord
		decoder := json.NewDecoder(strings.NewReader(string(line)))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&record); err != nil {
			return 0, "", errors.New("invalid audit record")
		}
		canonical, err := json.Marshal(record)
		if err != nil || string(canonical) != string(line) {
			return 0, "", errors.New("non-canonical audit record")
		}
		if record.Version != 1 || record.Sequence != sequence+1 || record.PreviousHash != previousHash {
			return 0, "", errors.New("broken audit chain")
		}
		if !strings.HasSuffix(record.Timestamp, "Z") {
			return 0, "", errors.New("invalid audit timestamp")
		}
		if _, err := time.Parse(time.RFC3339Nano, record.Timestamp); err != nil {
			return 0, "", errors.New("invalid audit timestamp")
		}
		want, err := hashAuditRecord(record.auditUnsignedRecord)
		if err != nil || want != record.EventHash {
			return 0, "", errors.New("audit hash mismatch")
		}
		if err := validateAuditRequest(DecryptAuditRequest{
			ActorUID: record.ActorUID, Purpose: record.Purpose, Field: record.Field,
			KeyID: record.KeyID, CiphertextSHA256: record.CiphertextSHA256, Outcome: record.Outcome,
		}); err != nil {
			return 0, "", errors.New("invalid audit metadata")
		}
		sequence = record.Sequence
		previousHash = record.EventHash
	}
	if err := scanner.Err(); err != nil {
		return 0, "", err
	}
	return sequence, previousHash, nil
}

func hashAuditRecord(record auditUnsignedRecord) (string, error) {
	encoded, err := json.Marshal(record)
	if err != nil {
		return "", fmt.Errorf("cannot encode audit record")
	}
	digest := sha256.Sum256(encoded)
	return hex.EncodeToString(digest[:]), nil
}
