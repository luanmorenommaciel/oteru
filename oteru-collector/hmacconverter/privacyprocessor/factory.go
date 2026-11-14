// Package privacyprocessor hashes identity attributes shared by a resource or
// instrumentation scope. The transformprocessor OTTL contexts in Collector
// v0.155.0 do not expose resource/scope function registration.
package privacyprocessor

import (
	"context"
	"errors"
	"fmt"

	"github.com/luanmorenommaciel/oteru/oteru-collector/hmacconverter"
	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/consumer"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.opentelemetry.io/collector/pdata/pmetric"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/processor"
	"go.opentelemetry.io/collector/processor/processorhelper"
)

type Config struct {
	Fields []string `mapstructure:"fields"`
}

func (c *Config) Validate() error {
	seen := make(map[string]struct{}, len(c.Fields))
	for _, field := range c.Fields {
		if field == "" {
			return errors.New("field names must not be empty")
		}
		if _, ok := seen[field]; ok {
			return fmt.Errorf("duplicate field %q", field)
		}
		seen[field] = struct{}{}
	}
	return nil
}

func NewFactory() processor.Factory {
	return processor.NewFactory(component.MustNewType("privacy"), func() component.Config {
		return &Config{Fields: []string{}}
	}, processor.WithLogs(createLogs, component.StabilityLevelDevelopment),
		processor.WithTraces(createTraces, component.StabilityLevelDevelopment),
		processor.WithMetrics(createMetrics, component.StabilityLevelDevelopment))
}

func bindKey() error { return hmacconverter.InitializeKeyFromEnv() }

func fields(cfg component.Config) []string { return cfg.(*Config).Fields }

func createLogs(ctx context.Context, set processor.Settings, cfg component.Config, next consumer.Logs) (processor.Logs, error) {
	if err := bindKey(); err != nil {
		return nil, err
	}
	return processorhelper.NewLogs(ctx, set, cfg, next, func(_ context.Context, data plog.Logs) (plog.Logs, error) {
		for i := 0; i < data.ResourceLogs().Len(); i++ {
			resource := data.ResourceLogs().At(i)
			if err := hashAttributes(resource.Resource().Attributes(), fields(cfg)); err != nil {
				return plog.Logs{}, err
			}
			for j := 0; j < resource.ScopeLogs().Len(); j++ {
				if err := hashAttributes(resource.ScopeLogs().At(j).Scope().Attributes(), fields(cfg)); err != nil {
					return plog.Logs{}, err
				}
			}
		}
		return data, nil
	})
}

func createTraces(ctx context.Context, set processor.Settings, cfg component.Config, next consumer.Traces) (processor.Traces, error) {
	if err := bindKey(); err != nil {
		return nil, err
	}
	return processorhelper.NewTraces(ctx, set, cfg, next, func(_ context.Context, data ptrace.Traces) (ptrace.Traces, error) {
		for i := 0; i < data.ResourceSpans().Len(); i++ {
			resource := data.ResourceSpans().At(i)
			if err := hashAttributes(resource.Resource().Attributes(), fields(cfg)); err != nil {
				return ptrace.Traces{}, err
			}
			for j := 0; j < resource.ScopeSpans().Len(); j++ {
				if err := hashAttributes(resource.ScopeSpans().At(j).Scope().Attributes(), fields(cfg)); err != nil {
					return ptrace.Traces{}, err
				}
			}
		}
		return data, nil
	})
}

func createMetrics(ctx context.Context, set processor.Settings, cfg component.Config, next consumer.Metrics) (processor.Metrics, error) {
	if err := bindKey(); err != nil {
		return nil, err
	}
	return processorhelper.NewMetrics(ctx, set, cfg, next, func(_ context.Context, data pmetric.Metrics) (pmetric.Metrics, error) {
		for i := 0; i < data.ResourceMetrics().Len(); i++ {
			resource := data.ResourceMetrics().At(i)
			if err := hashAttributes(resource.Resource().Attributes(), fields(cfg)); err != nil {
				return pmetric.Metrics{}, err
			}
			for j := 0; j < resource.ScopeMetrics().Len(); j++ {
				if err := hashAttributes(resource.ScopeMetrics().At(j).Scope().Attributes(), fields(cfg)); err != nil {
					return pmetric.Metrics{}, err
				}
			}
		}
		return data, nil
	})
}

func hashAttributes(attrs pcommon.Map, names []string) error {
	for _, name := range names {
		value, ok := attrs.Get(name)
		if !ok {
			continue
		}
		if value.Type() != pcommon.ValueTypeStr {
			continue
		} // filter/privacy_invalid rejects this batch first
		digest, err := hmacconverter.HashCurrent([]byte("oteru-pii:v1|" + name + "|" + value.Str()))
		if err != nil {
			return err
		}
		value.SetStr("hmac:v1:" + digest)
	}
	return nil
}
