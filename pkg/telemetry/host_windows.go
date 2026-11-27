//go:build windows

package telemetry

import (
	"os"
	"runtime"
	"syscall"
	"unsafe"
)

var (
	kernel32                 = syscall.NewLazyDLL("kernel32.dll")
	procGlobalMemoryStatusEx = kernel32.NewProc("GlobalMemoryStatusEx")
	procGetTickCount64       = kernel32.NewProc("GetTickCount64")
	procGetSystemTimes       = kernel32.NewProc("GetSystemTimes")
	procGetSystemPowerStatus = kernel32.NewProc("GetSystemPowerStatus")
)

type memoryStatusEx struct {
	Length               uint32
	MemoryLoad           uint32
	TotalPhys            uint64
	AvailPhys            uint64
	TotalPageFile        uint64
	AvailPageFile        uint64
	TotalVirtual         uint64
	AvailVirtual         uint64
	AvailExtendedVirtual uint64
}

type fileTime struct {
	LowDateTime  uint32
	HighDateTime uint32
}

func (ft fileTime) uint64() uint64 {
	return uint64(ft.HighDateTime)<<32 | uint64(ft.LowDateTime)
}

type systemPowerStatus struct {
	ACLineStatus        byte
	BatteryFlag         byte
	BatteryLifePercent  byte
	SystemStatusFlag    byte
	BatteryLifeTime     uint32
	BatteryFullLifeTime uint32
}

func (c *Collector) collectPlatformInfo() (string, string, uint64, map[string]interface{}) {
	var uptime uint64
	r1, _, _ := procGetTickCount64.Call()
	if r1 > 0 {
		uptime = uint64(r1) / 1000
	}

	hw := map[string]interface{}{
		"cpu_cores": runtime.NumCPU(),
	}
	if procIdent := os.Getenv("PROCESSOR_IDENTIFIER"); procIdent != "" {
		hw["cpu"] = procIdent
	}
	if compName := os.Getenv("COMPUTERNAME"); compName != "" {
		hw["computer_name"] = compName
	}

	return "Windows", "Windows NT (" + runtime.GOARCH + ")", uptime, hw
}

func (c *Collector) collectPlatformHostMetrics() (HostMetrics, map[string]float64) {
	var metrics HostMetrics
	extra := make(map[string]float64)

	var mem memoryStatusEx
	mem.Length = uint32(unsafe.Sizeof(mem))
	if r1, _, _ := procGlobalMemoryStatusEx.Call(uintptr(unsafe.Pointer(&mem))); r1 != 0 && mem.TotalPhys > 0 {
		metrics.TotalRAMMB = mem.TotalPhys / (1024 * 1024)
		metrics.AvailRAMMB = mem.AvailPhys / (1024 * 1024)
		metrics.UsedRAMMB = metrics.TotalRAMMB - metrics.AvailRAMMB
		metrics.RAMUsagePct = float64(metrics.UsedRAMMB) / float64(metrics.TotalRAMMB) * 100.0
	}

	metrics.CPUUsagePct = c.calculateWindowsCPU()
	return metrics, extra
}

func (c *Collector) calculateWindowsCPU() float64 {
	c.mu.Lock()
	defer c.mu.Unlock()

	var idle, kernel, user fileTime
	r1, _, _ := procGetSystemTimes.Call(
		uintptr(unsafe.Pointer(&idle)),
		uintptr(unsafe.Pointer(&kernel)),
		uintptr(unsafe.Pointer(&user)),
	)
	if r1 == 0 {
		return 0
	}
	idleTicks := idle.uint64()
	// Windows kernel time includes idle time.
	totalTicks := kernel.uint64() + user.uint64()

	if c.lastCPU.total == 0 {
		c.lastCPU = cpuSnapshot{idle: idleTicks, total: totalTicks}
		return 0
	}

	dTotal := totalTicks - c.lastCPU.total
	dIdle := idleTicks - c.lastCPU.idle
	c.lastCPU = cpuSnapshot{idle: idleTicks, total: totalTicks}

	if dTotal == 0 {
		return 0
	}
	usage := float64(dTotal-dIdle) / float64(dTotal) * 100.0
	if usage < 0 {
		return 0
	}
	if usage > 100 {
		return 100
	}
	return usage
}

func (c *Collector) collectPlatformNetDisk() (*NetworkMetrics, *DiskIOMetrics, map[string]float64) {
	return nil, nil, nil
}

func (c *Collector) collectPlatformBattery() *BatteryMetrics {
	var status systemPowerStatus
	r1, _, _ := procGetSystemPowerStatus.Call(uintptr(unsafe.Pointer(&status)))
	if r1 == 0 {
		return nil
	}
	// BatteryFlag 128 = no system battery, 255 = unknown.
	if status.BatteryFlag&128 != 0 || status.BatteryLifePercent > 100 {
		return nil
	}
	return &BatteryMetrics{
		Pct:      float64(status.BatteryLifePercent),
		Charging: status.ACLineStatus == 1,
	}
}

func (c *Collector) collectPlatformExtra() ([]ZFSPoolHealth, []DiskSMART) {
	return nil, nil
}
