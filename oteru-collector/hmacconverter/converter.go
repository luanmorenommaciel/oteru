package hmacconverter

import (
	"context"
	"errors"

	"github.com/open-telemetry/opentelemetry-collector-contrib/pkg/ottl"
)

// HMACSHA256Arguments is the OTTL pack for HMACSHA256(value). The key is
// factory-bound from OTERU_PII_HMAC_KEY and never appears in OTTL text.
type HMACSHA256Arguments[K any] struct {
	Target ottl.StringGetter[K]
}

// NewHMACSHA256Factory registers converter HMACSHA256.
func NewHMACSHA256Factory[K any]() ottl.Factory[K] {
	return ottl.NewFactory("HMACSHA256", &HMACSHA256Arguments[K]{}, createHMACSHA256Function[K])
}

func createHMACSHA256Function[K any](_ ottl.FunctionContext, oArgs ottl.Arguments) (ottl.ExprFunc[K], error) {
	args, ok := oArgs.(*HMACSHA256Arguments[K])
	if !ok {
		return nil, errors.New("HMACSHA256Factory args must be of type *HMACSHA256Arguments[K]")
	}
	return hmacSHA256[K](args.Target), nil
}

func hmacSHA256[K any](target ottl.StringGetter[K]) ottl.ExprFunc[K] {
	return func(ctx context.Context, tCtx K) (any, error) {
		key, err := currentKey()
		if err != nil {
			return nil, err
		}
		val, err := target.Get(ctx, tCtx)
		if err != nil {
			return nil, err
		}
		return Digest(key, []byte(val)), nil
	}
}
