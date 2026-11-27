//go:build !linux && !darwin && !windows

package telemetry

import "runtime"

func (c *Collector) collectPlatformInfo() (string, string, uint64, map[string]interface{}) {
	return runtime.GOOS, runtime.GOOS + "/" + runtime.GOARCH, 0, map[string]interface{}{
		"cpu_cores": runtime.NumCPU(),
	}
}

func (c *Collector) collectPlatformHostMetrics() (HostMetrics, map[string]float64) {
	return HostMetrics{}, nil
}

func (c *Collector) collectPlatformNetDisk() (*NetworkMetrics, *DiskIOMetrics, map[string]float64) {
	return nil, nil, nil
}

func (c *Collector) collectPlatformBattery() *BatteryMetrics {
	return nil
}

func (c *Collector) collectPlatformExtra() ([]ZFSPoolHealth, []DiskSMART) {
	return nil, nil
}
