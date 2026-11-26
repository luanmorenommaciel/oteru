package hmacconverter

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

func testAuditRequest() DecryptAuditRequest {
	return DecryptAuditRequest{
		ActorUID:         "501",
		Purpose:          "audit-review",
		Field:            "user.email",
		KeyID:            "key-v1",
		CiphertextSHA256: CiphertextFingerprint("synthetic-ciphertext"),
		Outcome:          "release-authorized",
	}
}

func TestAppendDecryptAuditRecordsOnlyMetadataAndVerifiesChain(t *testing.T) {
	path := filepath.Join(t.TempDir(), "audit.jsonl")
	first := testAuditRequest()
	if err := AppendDecryptAudit(path, first); err != nil {
		t.Fatal(err)
	}
	if err := AppendDecryptAudit(path, first); err != nil {
		t.Fatal(err)
	}
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(content), "synthetic-ciphertext") {
		t.Fatal("audit contains ciphertext or key material")
	}
	var records []auditRecord
	for _, line := range strings.Split(strings.TrimSuffix(string(content), "\n"), "\n") {
		var record auditRecord
		if err := json.Unmarshal([]byte(line), &record); err != nil {
			t.Fatal(err)
		}
		records = append(records, record)
	}
	if len(records) != 2 || records[0].Sequence != 1 || records[1].Sequence != 2 {
		t.Fatalf("unexpected sequence: %#v", records)
	}
	if records[1].PreviousHash != records[0].EventHash {
		t.Fatal("audit entries are not linked")
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("audit file should be mode 0600: mode=%v", info.Mode().Perm())
	}
}

func TestAppendDecryptAuditRejectsCorruptExistingChain(t *testing.T) {
	for name, mutate := range map[string]func([]byte) []byte{
		"modified record": func(data []byte) []byte {
			return []byte(strings.Replace(string(data), "audit-review", "case-review", 1))
		},
		"truncated final line": func(data []byte) []byte { return data[:len(data)-1] },
	} {
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "audit.jsonl")
			if err := AppendDecryptAudit(path, testAuditRequest()); err != nil {
				t.Fatal(err)
			}
			data, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(path, mutate(data), 0o600); err != nil {
				t.Fatal(err)
			}
			if err := AppendDecryptAudit(path, testAuditRequest()); err == nil {
				t.Fatal("corrupt audit chain accepted")
			}
		})
	}
}

func TestAppendDecryptAuditRejectsSymlinksAndBroadPermissions(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "target.jsonl")
	link := filepath.Join(dir, "link.jsonl")
	if err := os.WriteFile(target, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, link); err != nil {
		t.Fatal(err)
	}
	if err := AppendDecryptAudit(link, testAuditRequest()); err == nil {
		t.Fatal("symlink audit path accepted")
	}
	if err := os.Chmod(target, 0o640); err != nil {
		t.Fatal(err)
	}
	if err := AppendDecryptAudit(target, testAuditRequest()); err == nil {
		t.Fatal("group-readable audit file accepted")
	}
}

func TestAppendDecryptAuditSerializesConcurrentRequests(t *testing.T) {
	path := filepath.Join(t.TempDir(), "audit.jsonl")
	const writers = 8
	var group sync.WaitGroup
	errCh := make(chan error, writers)
	for i := 0; i < writers; i++ {
		group.Add(1)
		go func() {
			defer group.Done()
			errCh <- AppendDecryptAudit(path, testAuditRequest())
		}()
	}
	group.Wait()
	close(errCh)
	for err := range errCh {
		if err != nil {
			t.Fatal(err)
		}
	}
	file, _, err := openAuditFile(path)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	sequence, _, err := verifyAuditChain(file)
	if err != nil || sequence != writers {
		t.Fatalf("concurrent audit sequence=%d err=%v", sequence, err)
	}
}
