package hmacconverter

import (
	"context"
	"fmt"

	"github.com/open-telemetry/opentelemetry-collector-contrib/pkg/ottl"
	"github.com/open-telemetry/opentelemetry-collector-contrib/pkg/ottl/contexts/ottldatapoint"
	"github.com/open-telemetry/opentelemetry-collector-contrib/pkg/ottl/contexts/ottllog"
	"github.com/open-telemetry/opentelemetry-collector-contrib/pkg/ottl/contexts/ottlmetric"
	"github.com/open-telemetry/opentelemetry-collector-contrib/pkg/ottl/contexts/ottlprofile"
	"github.com/open-telemetry/opentelemetry-collector-contrib/pkg/ottl/contexts/ottlspan"
	"github.com/open-telemetry/opentelemetry-collector-contrib/pkg/ottl/contexts/ottlspanevent"
	"github.com/open-telemetry/opentelemetry-collector-contrib/processor/transformprocessor"
	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/consumer"
	"go.opentelemetry.io/collector/consumer/xconsumer"
	"go.opentelemetry.io/collector/processor"
	"go.opentelemetry.io/collector/processor/xprocessor"
)

// NewFactory is the OCB entry point: contrib transform plus Oteru's HMAC and
// field-bound AES-GCM converters. HMAC key validation remains required on
// Create*; encryption keyring is required only when an Encrypt statement exists.
func NewFactory() processor.Factory {
	// OTTL config parsing can construct converter functions before Create* runs.
	// Bind the mounted secret as soon as the component factory is constructed.
	BindOptionalKeyringFile(EncryptionKeyringPath)
	inner := transformprocessor.NewFactoryWithOptions(
		transformprocessor.WithLogFunctions(transformprocessor.DefaultLogFunctions()),
		transformprocessor.WithLogFunctions([]ottl.Factory[*ottllog.TransformContext]{
			NewHMACSHA256Factory[*ottllog.TransformContext](),
			NewAESGCMEncryptFactory[*ottllog.TransformContext](),
		}),
		transformprocessor.WithSpanFunctions(transformprocessor.DefaultSpanFunctions()),
		transformprocessor.WithSpanFunctions([]ottl.Factory[*ottlspan.TransformContext]{
			NewHMACSHA256Factory[*ottlspan.TransformContext](),
			NewAESGCMEncryptFactory[*ottlspan.TransformContext](),
		}),
		transformprocessor.WithSpanEventFunctions(transformprocessor.DefaultSpanEventFunctions()),
		transformprocessor.WithSpanEventFunctions([]ottl.Factory[*ottlspanevent.TransformContext]{
			NewHMACSHA256Factory[*ottlspanevent.TransformContext](),
			NewAESGCMEncryptFactory[*ottlspanevent.TransformContext](),
		}),
		transformprocessor.WithMetricFunctions(transformprocessor.DefaultMetricFunctions()),
		transformprocessor.WithMetricFunctions([]ottl.Factory[*ottlmetric.TransformContext]{NewHMACSHA256Factory[*ottlmetric.TransformContext]()}),
		transformprocessor.WithDataPointFunctions(transformprocessor.DefaultDataPointFunctions()),
		transformprocessor.WithDataPointFunctions([]ottl.Factory[*ottldatapoint.TransformContext]{NewHMACSHA256Factory[*ottldatapoint.TransformContext]()}),
		transformprocessor.WithProfileFunctions(transformprocessor.DefaultProfileFunctions()),
		transformprocessor.WithProfileFunctions([]ottl.Factory[*ottlprofile.TransformContext]{NewHMACSHA256Factory[*ottlprofile.TransformContext]()}),
	)
	xp := inner.(xprocessor.Factory)
	return wrappingFactory{Factory: xp}
}

type wrappingFactory struct {
	xprocessor.Factory
}

func (f wrappingFactory) CreateTraces(
	ctx context.Context,
	set processor.Settings,
	cfg component.Config,
	next consumer.Traces,
) (processor.Traces, error) {
	if err := bindKeyFromEnv(); err != nil {
		return nil, err
	}
	BindOptionalKeyringFile(EncryptionKeyringPath)
	return f.Factory.CreateTraces(ctx, set, cfg, next)
}

func (f wrappingFactory) CreateMetrics(
	ctx context.Context,
	set processor.Settings,
	cfg component.Config,
	next consumer.Metrics,
) (processor.Metrics, error) {
	if err := bindKeyFromEnv(); err != nil {
		return nil, err
	}
	return f.Factory.CreateMetrics(ctx, set, cfg, next)
}

func (f wrappingFactory) CreateLogs(
	ctx context.Context,
	set processor.Settings,
	cfg component.Config,
	next consumer.Logs,
) (processor.Logs, error) {
	if err := bindKeyFromEnv(); err != nil {
		return nil, err
	}
	BindOptionalKeyringFile(EncryptionKeyringPath)
	return f.Factory.CreateLogs(ctx, set, cfg, next)
}

func (f wrappingFactory) CreateProfiles(
	ctx context.Context,
	set processor.Settings,
	cfg component.Config,
	next xconsumer.Profiles,
) (xprocessor.Profiles, error) {
	if err := bindKeyFromEnv(); err != nil {
		return nil, err
	}
	return f.Factory.CreateProfiles(ctx, set, cfg, next)
}

// ConfigString reports a stable representation of the default transform config
// for leak tests. It must never contain the HMAC key.
func ConfigString(cfg component.Config) string {
	return fmt.Sprintf("%#v", cfg)
}
