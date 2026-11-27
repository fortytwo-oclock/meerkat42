package tunnel

import (
	"testing"

	"control-plane/pkg/telemetry"
)

func TestTelemetryBufferEvictsOldestAndDrainsFIFO(t *testing.T) {
	buf := NewTelemetryBuffer(3)
	for i := 1; i <= 5; i++ {
		buf.Push(&telemetry.NodeTelemetry{
			NodeID:    "laptop-1",
			UptimeSec: uint64(i),
		})
	}
	if buf.Len() != 3 {
		t.Fatalf("expected len 3, got %d", buf.Len())
	}
	drained := buf.Drain()
	if len(drained) != 3 {
		t.Fatalf("expected 3 drained items, got %d", len(drained))
	}
	if drained[0].UptimeSec != 3 || drained[1].UptimeSec != 4 || drained[2].UptimeSec != 5 {
		t.Fatalf("unexpected FIFO order: %d, %d, %d", drained[0].UptimeSec, drained[1].UptimeSec, drained[2].UptimeSec)
	}
	if buf.Len() != 0 {
		t.Fatalf("expected empty buffer after drain, got %d", buf.Len())
	}
}
