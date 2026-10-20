package hmacconverter

import (
	"context"
	"errors"
	"strings"
	"testing"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/consumer/consumertest"
	"go.opentelemetry.io/collector/processor"
	"go.opentelemetry.io/collector/processor/processortest"
)

func TestNewFactoryType(t *testing.T) {
	f := NewFactory()
	if f.Type().String() != "transform" {
		t.Fatalf("type %s", f.Type())
	}
}

func TestCreateFailsWithoutKey(t *testing.T) {
	f := NewFactory()
	set := processortest.NewNopSettings(f.Type())
	cfg := f.CreateDefaultConfig()
	ctx := context.Background()

	t.Run("unset", func(t *testing.T) {
		unsetHMACKey(t)
		_, err := f.CreateLogs(ctx, set, cfg, consumertest.NewNop())
		if err == nil {
			t.Fatal("expected error")
		}
		if !strings.Contains(err.Error(), "unset") {
			t.Fatalf("got %v", err)
		}
		if strings.Contains(err.Error(), publicTestKey) {
			t.Fatalf("error leaked key: %v", err)
		}
	})
	t.Run("empty", func(t *testing.T) {
		t.Setenv(HMACKeyEnv, "")
		_, err := f.CreateLogs(ctx, set, cfg, consumertest.NewNop())
		if err == nil || !errors.Is(err, ErrKeyEmpty) {
			t.Fatalf("got %v", err)
		}
	})
	t.Run("short", func(t *testing.T) {
		secret := "short-key-value"
		t.Setenv(HMACKeyEnv, secret)
		_, err := f.CreateLogs(ctx, set, cfg, consumertest.NewNop())
		if err == nil || !errors.Is(err, ErrKeyTooShort) {
			t.Fatalf("got %v", err)
		}
		if strings.Contains(err.Error(), secret) {
			t.Fatalf("error leaked key: %v", err)
		}
	})
	t.Run("traces and metrics also fail closed", func(t *testing.T) {
		unsetHMACKey(t)
		if _, err := f.CreateTraces(ctx, set, cfg, consumertest.NewNop()); err == nil {
			t.Fatal("expected traces error")
		}
		if _, err := f.CreateMetrics(ctx, set, cfg, consumertest.NewNop()); err == nil {
			t.Fatal("expected metrics error")
		}
	})
}

func TestCreateSucceedsWithValidKey(t *testing.T) {
	t.Setenv(HMACKeyEnv, publicTestKey)
	f := NewFactory()
	set := processortest.NewNopSettings(f.Type())
	cfg := f.CreateDefaultConfig()
	_, err := f.CreateLogs(context.Background(), set, cfg, consumertest.NewNop())
	if err != nil {
		t.Fatal(err)
	}
}

func TestKeyNeverAppearsInErrorOrConfigString(t *testing.T) {
	t.Setenv(HMACKeyEnv, publicTestKey)
	f := NewFactory()
	cfg := f.CreateDefaultConfig()
	form := ConfigString(cfg)
	if strings.Contains(form, publicTestKey) {
		t.Fatalf("config string leaked key: %s", form)
	}
	t.Setenv(HMACKeyEnv, "too-short")
	_, err := f.CreateLogs(
		context.Background(),
		processortest.NewNopSettings(f.Type()),
		cfg,
		consumertest.NewNop(),
	)
	if err == nil {
		t.Fatal("expected error")
	}
	if strings.Contains(err.Error(), "too-short") {
		t.Fatalf("error leaked key: %v", err)
	}
	if strings.Contains(err.Error(), publicTestKey) {
		t.Fatalf("error leaked prior key: %v", err)
	}
}

func TestFactoryImplementsXProcessor(t *testing.T) {
	var _ processor.Factory = NewFactory()
	var _ component.Factory = NewFactory()
}
