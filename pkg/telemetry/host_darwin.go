//go:build darwin

package telemetry

import (
	"bufio"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
	"time"
)

func runTrimmed(name string, args ...string) string {
	out, err := exec.Command(name, args...).Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

func (c *Collector) collectPlatformInfo() (string, string, uint64, map[string]interface{}) {
	ver := runTrimmed("sw_vers", "-productVersion")
	osName := "macOS"
	if ver != "" {
		osName = "macOS " + ver
	}
	kernel := runTrimmed("sysctl", "-n", "kern.osrelease")
	if kernel != "" {
		kernel = "Darwin " + kernel
	}

	var uptime uint64
	bootRaw := runTrimmed("sysctl", "-n", "kern.boottime")
	// Format: { sec = 1712345678, usec = 0 } ...
	if idx := strings.Index(bootRaw, "sec = "); idx >= 0 {
		rest := bootRaw[idx+6:]
		if comma := strings.IndexByte(rest, ','); comma > 0 {
			if bootSec, err := strconv.ParseInt(strings.TrimSpace(rest[:comma]), 10, 64); err == nil {
				nowSec := time.Now().Unix()
				if nowSec > bootSec {
					uptime = uint64(nowSec - bootSec)
				}
			}
		}
	}

	hw := map[string]interface{}{
		"vendor":    "Apple",
		"cpu_cores": runtime.NumCPU(),
	}
	if model := runTrimmed("sysctl", "-n", "hw.model"); model != "" {
		hw["model"] = model
	}
	if cpu := runTrimmed("sysctl", "-n", "machdep.cpu.brand_string"); cpu != "" {
		hw["cpu"] = cpu
	}

	return osName, kernel, uptime, hw
}

func (c *Collector) collectPlatformHostMetrics() (HostMetrics, map[string]float64) {
	var metrics HostMetrics
	extra := make(map[string]float64)

	metrics.Load1, metrics.Load5, metrics.Load15 = parseDarwinLoadAvg(
		runTrimmed("sysctl", "-n", "vm.loadavg"),
	)

	if totalRaw := runTrimmed("sysctl", "-n", "hw.memsize"); totalRaw != "" {
		if totalBytes, err := strconv.ParseUint(totalRaw, 10, 64); err == nil && totalBytes > 0 {
			_, availBytes := parseDarwinVmStat(runTrimmed("vm_stat"))
			if availBytes > totalBytes {
				availBytes = totalBytes
			}
			metrics.TotalRAMMB = totalBytes / (1024 * 1024)
			metrics.AvailRAMMB = availBytes / (1024 * 1024)
			metrics.UsedRAMMB = metrics.TotalRAMMB - metrics.AvailRAMMB
			metrics.RAMUsagePct = float64(metrics.UsedRAMMB) / float64(metrics.TotalRAMMB) * 100.0
		}
	}

	// Estimate aggregate CPU utilization from `ps -A -o %cpu` normalized by logical core count.
	if psOut := runTrimmed("ps", "-A", "-o", "%cpu"); psOut != "" {
		var sum float64
		scanner := bufio.NewScanner(strings.NewReader(psOut))
		for scanner.Scan() {
			if v, err := strconv.ParseFloat(strings.TrimSpace(scanner.Text()), 64); err == nil {
				sum += v
			}
		}
		cores := float64(runtime.NumCPU())
		if cores < 1 {
			cores = 1
		}
		pct := sum / cores
		if pct < 0 {
			pct = 0
		} else if pct > 100 {
			pct = 100
		}
		metrics.CPUUsagePct = pct
	}

	return metrics, extra
}

func (c *Collector) collectPlatformNetDisk() (*NetworkMetrics, *DiskIOMetrics, map[string]float64) {
	c.mu.Lock()
	defer c.mu.Unlock()

	raw := runTrimmed("netstat", "-ibn")
	if raw == "" {
		return nil, nil, nil
	}

	curr := make(map[string]netSnapshot)
	scanner := bufio.NewScanner(strings.NewReader(raw))
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) < 10 || fields[0] == "Name" || !strings.Contains(fields[2], "<Link#") {
			continue
		}
		iface := fields[0]
		if iface == "lo0" || strings.HasPrefix(iface, "utun") || strings.HasPrefix(iface, "awdl") {
			continue
		}
		rx, err1 := strconv.ParseUint(fields[6], 10, 64)
		tx, err2 := strconv.ParseUint(fields[9], 10, 64)
		if err1 != nil || err2 != nil {
			continue
		}
		curr[iface] = netSnapshot{rxBytes: rx, txBytes: tx}
	}

	now := time.Now()
	extra := make(map[string]float64)
	var netAgg *NetworkMetrics
	if !c.lastNetAt.IsZero() {
		dt := now.Sub(c.lastNetAt).Seconds()
		if dt > 0 {
			var totalRx, totalTx float64
			for iface, snap := range curr {
				prev, ok := c.lastNet[iface]
				if !ok || snap.rxBytes < prev.rxBytes || snap.txBytes < prev.txBytes {
					continue
				}
				rxBps := float64(snap.rxBytes-prev.rxBytes) / dt
				txBps := float64(snap.txBytes-prev.txBytes) / dt
				totalRx += rxBps
				totalTx += txBps
				extra["net.rx_bps:"+iface] = rxBps
				extra["net.tx_bps:"+iface] = txBps
			}
			netAgg = &NetworkMetrics{RxBps: totalRx, TxBps: totalTx}
		}
	}
	c.lastNet = curr
	c.lastNetAt = now
	return netAgg, nil, extra
}

func (c *Collector) collectPlatformBattery() *BatteryMetrics {
	raw := runTrimmed("pmset", "-g", "batt")
	if raw == "" {
		return nil
	}
	return parseDarwinPmsetBattery(raw)
}

func (c *Collector) collectPlatformExtra() ([]ZFSPoolHealth, []DiskSMART) {
	return nil, nil
}
