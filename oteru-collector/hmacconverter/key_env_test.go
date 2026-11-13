package hmacconverter

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"

	"go.opentelemetry.io/collector/consumer/consumertest"
	"go.opentelemetry.io/collector/processor"
	"go.opentelemetry.io/collector/processor/processortest"
	"go.opentelemetry.io/collector/processor/xprocessor"
	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
	"go.uber.org/zap/zaptest/observer"
)

// TestEnvKeyRejectionMatrix drives OTERU_PII_HMAC_KEY through the env-only
// path for every signal factory (logs, traces, metrics, profiles) and checks the key never reaches the error
// text or the Collector log output.
func TestEnvKeyRejectionMatrix(t *testing.T) {
	good := randomKey(t)
	cases := []struct {
		name    string
		key     string
		wantErr error
	}{
		{"empty", "", ErrKeyEmpty},
		{"short 31", good[:31], ErrKeyTooShort},
		{"short 63", good[:63], ErrKeyTooShort},
		{"long 65", good + "0", ErrKeyTooLong},
		{"non-hex 64", "z" + good[1:], ErrKeyNotHex},
		{"uppercase 64", "A" + good[1:], ErrKeyNotHex},
		{"trailing newline 64", good[:63] + "\n", ErrKeyNotHex},
	}
	f := NewFactory()
	cfg := f.CreateDefaultConfig()
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			core, logs := observer.New(zapcore.DebugLevel)
			set := processortest.NewNopSettings(f.Type())
			set.Logger = zap.New(core)
			t.Setenv(HMACKeyEnv, tc.key)
			ctx := context.Background()
			errs := []error{}
			_, err := f.CreateLogs(ctx, set, cfg, consumertest.NewNop())
			errs = append(errs, err)
			_, err = f.CreateTraces(ctx, set, cfg, consumertest.NewNop())
			errs = append(errs, err)
			_, err = f.CreateMetrics(ctx, set, cfg, consumertest.NewNop())
			errs = append(errs, err)
			_, err = profilesFactory(t, f).CreateProfiles(ctx, set, cfg, consumertest.NewNop())
			errs = append(errs, err)
			for _, err := range errs {
				if !errors.Is(err, tc.wantErr) {
					t.Fatalf("got %v want %v", err, tc.wantErr)
				}
				if tc.key != "" && strings.Contains(err.Error(), strings.TrimSpace(tc.key)) {
					t.Fatalf("error leaked key: %v", err)
				}
			}
			for _, e := range logs.All() {
				line := e.Message
				for k, v := range e.ContextMap() {
					line += " " + k + "=" + toString(v)
				}
				if tc.key != "" && strings.Contains(line, strings.TrimSpace(tc.key)) {
					t.Fatalf("log leaked key: %s", line)
				}
			}
		})
	}
}

// TestValidEnvKeyNeverLogged starts all factories with a valid key and checks
// no log entry carries it.
func TestValidEnvKeyNeverLogged(t *testing.T) {
	key := randomKey(t)
	t.Setenv(HMACKeyEnv, key)
	core, logs := observer.New(zapcore.DebugLevel)
	f := NewFactory()
	set := processortest.NewNopSettings(f.Type())
	set.Logger = zap.New(core)
	cfg := f.CreateDefaultConfig()
	ctx := context.Background()
	if _, err := f.CreateLogs(ctx, set, cfg, consumertest.NewNop()); err != nil {
		t.Fatal(err)
	}
	if _, err := f.CreateTraces(ctx, set, cfg, consumertest.NewNop()); err != nil {
		t.Fatal(err)
	}
	if _, err := f.CreateMetrics(ctx, set, cfg, consumertest.NewNop()); err != nil {
		t.Fatal(err)
	}
	if _, err := profilesFactory(t, f).CreateProfiles(ctx, set, cfg, consumertest.NewNop()); err != nil {
		t.Fatal(err)
	}
	for _, e := range logs.All() {
		line := e.Message
		for k, v := range e.ContextMap() {
			line += " " + k + "=" + toString(v)
		}
		if strings.Contains(line, key) {
			t.Fatalf("log leaked key: %s", line)
		}
	}
	if strings.Contains(ConfigString(cfg), key) {
		t.Fatal("config string leaked key")
	}
}

// profilesFactory asserts NewFactory also builds Profiles processors, so the
// key check covers that signal too.
func profilesFactory(t *testing.T, f processor.Factory) xprocessor.Factory {
	t.Helper()
	xf, ok := f.(xprocessor.Factory)
	if !ok {
		t.Fatal("NewFactory does not implement xprocessor.Factory")
	}
	return xf
}

func toString(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return strings.TrimSpace(strings.ReplaceAll(fmtAny(v), "\n", " "))
}

func fmtAny(v any) string { return fmt.Sprint(v) }
