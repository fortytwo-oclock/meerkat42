package tunnel

import (
	"sync"

	"control-plane/pkg/telemetry"
)

// TelemetryBuffer is a thread-safe bounded ring buffer that retains recent
// telemetry samples while a roaming workstation or server is disconnected.
type TelemetryBuffer struct {
	mu       sync.Mutex
	capacity int
	items    []*telemetry.NodeTelemetry
}

// NewTelemetryBuffer creates a bounded ring buffer with the given max capacity.
func NewTelemetryBuffer(capacity int) *TelemetryBuffer {
	if capacity <= 0 {
		capacity = 120
	}
	return &TelemetryBuffer{
		capacity: capacity,
		items:    make([]*telemetry.NodeTelemetry, 0, capacity),
	}
}

// Push appends a sample, evicting the oldest sample if the buffer is full.
func (b *TelemetryBuffer) Push(sample *telemetry.NodeTelemetry) {
	if sample == nil {
		return
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	if len(b.items) >= b.capacity {
		copy(b.items, b.items[1:])
		b.items[len(b.items)-1] = sample
		return
	}
	b.items = append(b.items, sample)
}

// Drain removes and returns all buffered samples in FIFO order.
func (b *TelemetryBuffer) Drain() []*telemetry.NodeTelemetry {
	b.mu.Lock()
	defer b.mu.Unlock()
	if len(b.items) == 0 {
		return nil
	}
	out := make([]*telemetry.NodeTelemetry, len(b.items))
	copy(out, b.items)
	b.items = b.items[:0]
	return out
}

// Len returns the current number of buffered samples.
func (b *TelemetryBuffer) Len() int {
	b.mu.Lock()
	defer b.mu.Unlock()
	return len(b.items)
}
