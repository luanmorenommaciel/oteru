package privacyprocessor

import (
	"strings"
	"testing"

	"github.com/luanmorenommaciel/oteru/oteru-collector/hmacconverter"
	"go.opentelemetry.io/collector/pdata/pcommon"
)

func TestHashAttributesUsesFieldSeparatedHMAC(t *testing.T) {
	t.Setenv(hmacconverter.HMACKeyEnv, strings.Repeat("a", hmacconverter.ProductionKeyHexLen))
	if err := hmacconverter.InitializeKeyFromEnv(); err != nil {
		t.Fatal(err)
	}
	attrs := pcommon.NewMap()
	attrs.PutStr("user.email", "person@example.com")
	attrs.PutStr("user.id", "person@example.com")
	if err := hashAttributes(attrs, []string{"user.email", "user.id"}); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"user.email", "user.id"} {
		value, ok := attrs.Get(name)
		if !ok || !strings.HasPrefix(value.Str(), "hmac:v1:") {
			t.Fatalf("%s was not pseudonymized: %q", name, value.Str())
		}
	}
	email, _ := attrs.Get("user.email")
	id, _ := attrs.Get("user.id")
	if email.Str() == id.Str() {
		t.Fatal("field domain separation should produce distinct pseudonyms")
	}
}

func TestHashAttributesLeavesAbsentAndNonStringValuesUntouched(t *testing.T) {
	t.Setenv(hmacconverter.HMACKeyEnv, strings.Repeat("b", hmacconverter.ProductionKeyHexLen))
	if err := hmacconverter.InitializeKeyFromEnv(); err != nil {
		t.Fatal(err)
	}
	attrs := pcommon.NewMap()
	attrs.PutInt("user.id", 42)
	if err := hashAttributes(attrs, []string{"user.email", "user.id"}); err != nil {
		t.Fatal(err)
	}
	value, ok := attrs.Get("user.id")
	if !ok || value.Int() != 42 {
		t.Fatalf("non-string value changed: %v", value)
	}
}

func TestConfigRejectsEmptyAndDuplicateFields(t *testing.T) {
	if err := (&Config{}).Validate(); err != nil {
		t.Fatalf("empty field list should be a valid no-op: %v", err)
	}
	for _, cfg := range []*Config{{Fields: []string{""}}, {Fields: []string{"user.id", "user.id"}}} {
		if err := cfg.Validate(); err == nil {
			t.Fatalf("expected invalid config: %#v", cfg)
		}
	}
}

func TestConfigValidatesEncryptionFieldsAndOverlap(t *testing.T) {
	for _, cfg := range []*Config{
		{EncryptFields: []string{""}},
		{EncryptFields: []string{"prompt.content"}},
		{Fields: []string{"user.email"}, EncryptFields: []string{"user.email"}},
	} {
		if err := cfg.Validate(); err == nil {
			t.Fatalf("expected invalid config: %#v", cfg)
		}
	}
	if err := (&Config{EncryptFields: []string{"user.email", "host.name"}}).Validate(); err != nil {
		t.Fatalf("catalogued encryption fields should validate: %v", err)
	}
}

func TestTransformAttributesEncryptsCataloguedFieldOnly(t *testing.T) {
	keyringJSON := `{"version":1,"active_key_id":"k1","keys":{"k1":"` + strings.Repeat("c", 64) + `"}}`
	keyring, err := hmacconverter.ParseKeyring([]byte(keyringJSON))
	if err != nil {
		t.Fatal(err)
	}
	attrs := pcommon.NewMap()
	attrs.PutStr("user.email", "person@example.invalid")
	attrs.PutStr("service.name", "synthetic-service")
	if err := transformAttributes(attrs, nil, []string{"user.email"}, keyring); err != nil {
		t.Fatal(err)
	}
	sealed, _ := attrs.Get("user.email")
	if !strings.HasPrefix(sealed.Str(), "oteru:enc:v1:k1:user.email:") {
		t.Fatalf("identity field was not encrypted: %q", sealed.Str())
	}
	if strings.Contains(sealed.Str(), "person@example.invalid") {
		t.Fatal("plaintext survived encryption")
	}
	service, _ := attrs.Get("service.name")
	if service.Str() != "synthetic-service" {
		t.Fatalf("benign attribute changed: %q", service.Str())
	}
	plaintext, keyID, err := hmacconverter.DecryptValue(keyring, "user.email", sealed.Str())
	if err != nil || keyID != "k1" || string(plaintext) != "person@example.invalid" {
		t.Fatalf("decrypt got key_id=%q plaintext=%q err=%v", keyID, plaintext, err)
	}
}

func TestTransformAttributesRejectsInvalidEncryptedValue(t *testing.T) {
	keyring, err := hmacconverter.ParseKeyring([]byte(`{"version":1,"active_key_id":"k1","keys":{"k1":"` + strings.Repeat("d", 64) + `"}}`))
	if err != nil {
		t.Fatal(err)
	}
	for _, value := range []struct {
		name string
		set  func(pcommon.Map)
	}{
		{name: "non-string", set: func(attrs pcommon.Map) { attrs.PutInt("user.id", 1) }},
		{name: "empty", set: func(attrs pcommon.Map) { attrs.PutStr("user.id", "") }},
	} {
		t.Run(value.name, func(t *testing.T) {
			attrs := pcommon.NewMap()
			value.set(attrs)
			if err := transformAttributes(attrs, nil, []string{"user.id"}, keyring); err == nil {
				t.Fatal("expected invalid encrypted value to fail")
			}
		})
	}
}

func TestEncryptionErrorDoesNotEchoPII(t *testing.T) {
	attrs := pcommon.NewMap()
	attrs.PutStr("user.email", "private@example.invalid")
	err := transformAttributes(attrs, nil, []string{"user.email"}, nil)
	if err == nil || strings.Contains(err.Error(), "private@example.invalid") {
		t.Fatalf("unexpected encryption error: %v", err)
	}
}
