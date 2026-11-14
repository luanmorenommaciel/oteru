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
