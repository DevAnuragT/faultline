# Remove simulated Redis RPC delay to reduce GetDriver latency (49.9% of critical path)

Target: `services/driver/redis.go` lines 65-90, `Redis.GetDriver` (https://github.com/jaegertracing/jaeger/blob/main/examples/hotrod/services/driver/redis.go#L65-L90).

**Why (from the trace graph):** Remove simulated Redis RPC delay to reduce GetDriver latency (49.9% of critical path)

**Current code**

```go
func (r *Redis) GetDriver(ctx context.Context, driverID string) (Driver, error) {
	ctx, span := r.tracer.Start(ctx, "GetDriver", trace.WithSpanKind(trace.SpanKindClient))
	span.SetAttributes(
		otelsemconv.DBSystemAttribute("redis"),
		otelsemconv.DBOperationNameAttribute("GET"),
		attribute.Key("param.driverID").String(driverID),
	)
	defer span.End()

	// simulate RPC delay
	delay.Sleep(config.RedisGetDelay, config.RedisGetDelayStdDev)
	if err := r.checkError(); err != nil {
		span.RecordError(err)
		span.SetStatus(codes.Error, "An error occurred")
		r.logger.For(ctx).Error("redis timeout", zap.String("driver_id", driverID), zap.Error(err))
		return Driver{}, err
	}

	r.logger.For(ctx).Info("Got driver's ID", zap.String("driverID", driverID))

	// #nosec
	return Driver{
		DriverID: driverID,
		Location: fmt.Sprintf("%d,%d", rand.Int()%1000, rand.Int()%1000),
	}, nil
}
```

**Proposed patch** (`redis.go.patch`)

The patch was drafted by the agent after reading this function, checked to touch only lines that exist in it, and approved by a person. Window `w-f2e4d5c4`, code hash `8df19b562248abb3`.
