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

// AESGCMEncryptArguments is the OTTL pack for AESGCMEncrypt(value, field).
type AESGCMEncryptArguments[K any] struct {
	Target ottl.StringGetter[K]
	Field  ottl.StringGetter[K]
}

// NewAESGCMEncryptFactory registers field-bound AES-256-GCM encryption.
func NewAESGCMEncryptFactory[K any]() ottl.Factory[K] {
	return ottl.NewFactory("AESGCMEncrypt", &AESGCMEncryptArguments[K]{}, createAESGCMEncryptFunction[K])
}

func createHMACSHA256Function[K any](_ ottl.FunctionContext, oArgs ottl.Arguments) (ottl.ExprFunc[K], error) {
	args, ok := oArgs.(*HMACSHA256Arguments[K])
	if !ok {
		return nil, errors.New("HMACSHA256Factory args must be of type *HMACSHA256Arguments[K]")
	}
	return hmacSHA256[K](args.Target), nil
}

func createAESGCMEncryptFunction[K any](_ ottl.FunctionContext, oArgs ottl.Arguments) (ottl.ExprFunc[K], error) {
	args, ok := oArgs.(*AESGCMEncryptArguments[K])
	if !ok {
		return nil, errors.New("AESGCMEncryptFactory args must be of type *AESGCMEncryptArguments[K]")
	}
	if err := EncryptionKeyringReady(); err != nil {
		return nil, err
	}
	return aesGCMEncrypt[K](args.Target, args.Field), nil
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

func aesGCMEncrypt[K any](target, field ottl.StringGetter[K]) ottl.ExprFunc[K] {
	return func(ctx context.Context, tCtx K) (any, error) {
		value, err := target.Get(ctx, tCtx)
		if err != nil {
			return nil, err
		}
		fieldName, err := field.Get(ctx, tCtx)
		if err != nil {
			return nil, err
		}
		return EncryptCurrent(fieldName, value)
	}
}
