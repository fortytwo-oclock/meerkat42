//go:build linux

package telemetry

import (
	"bufio"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

func (c *Collector) collectPlatformInfo() (string, string, uint64, map[string]interface{}) {
	kernel := c.readFirstLine(filepath.Join(c.procPath, "version"))
	if len(kernel) > 80 {
		kernel = kernel[:80] + "..."
	}

	var uptime uint64
	data := c.readFirstLine(filepath.Join(c.procPath, "uptime"))
	parts := strings.Fields(data)
	if len(parts) > 0 {
		sec, _ := strconv.ParseFloat(parts[0], 64)
		uptime = uint64(sec)
	}

	hw := map[string]interface{}{
		"cpu_cores": runtime.NumCPU(),
	}
	if vendor := c.readFirstLine(filepath.Join(c.sysPath, "devices/virtual/dmi/id/sys_vendor")); vendor != "" {
		hw["vendor"] = vendor
	}
	if model := c.readFirstLine(filepath.Join(c.sysPath, "devices/virtual/dmi/id/product_name")); model != "" {
		hw["model"] = model
	}

	return "Linux", kernel, uptime, hw
}

func (c *Collector) collectPlatformHostMetrics() (HostMetrics, map[string]float64) {
	var metrics HostMetrics
	extra := make(map[string]float64)

	// Load Averages
	loadData := c.readFirstLine(filepath.Join(c.procPath, "loadavg"))
	loadParts := strings.Fields(loadData)
	if len(loadParts) >= 3 {
		metrics.Load1, _ = strconv.ParseFloat(loadParts[0], 64)
		metrics.Load5, _ = strconv.ParseFloat(loadParts[1], 64)
		metrics.Load15, _ = strconv.ParseFloat(loadParts[2], 64)
	}

	// Memory & Swap info (/proc/meminfo)
	memPath := filepath.Join(c.procPath, "meminfo")
	if file, err := os.Open(memPath); err == nil {
		defer file.Close()
		scanner := bufio.NewScanner(file)
		var totalKB, availKB, swapTotalKB, swapFreeKB uint64
		for scanner.Scan() {
			line := scanner.Text()
			switch {
			case strings.HasPrefix(line, "MemTotal:"):
				totalKB = parseMemKB(line)
			case strings.HasPrefix(line, "MemAvailable:"):
				availKB = parseMemKB(line)
			case strings.HasPrefix(line, "SwapTotal:"):
				swapTotalKB = parseMemKB(line)
			case strings.HasPrefix(line, "SwapFree:"):
				swapFreeKB = parseMemKB(line)
			}
		}
		if totalKB > 0 {
			metrics.TotalRAMMB = totalKB / 1024
			metrics.AvailRAMMB = availKB / 1024
			metrics.UsedRAMMB = metrics.TotalRAMMB - metrics.AvailRAMMB
			metrics.RAMUsagePct = float64(metrics.UsedRAMMB) / float64(metrics.TotalRAMMB) * 100.0
		}
		if swapTotalKB > 0 {
			swapUsedKB := swapTotalKB - swapFreeKB
			extra["swap.usage_pct"] = float64(swapUsedKB) / float64(swapTotalKB) * 100.0
		}
	}

	// CPU total, iowait, and per-core breakdown from /proc/stat
	totalPct, iowaitPct, perCore := c.calculateLinuxCPU()
	metrics.CPUUsagePct = totalPct
	extra["cpu.iowait_pct"] = iowaitPct
	for core, pct := range perCore {
		extra["cpu.core_pct:"+core] = pct
	}

	// Thermal zones (/sys/class/thermal/thermal_zone*)
	if tempC, ok := c.readLinuxThermalC(); ok {
		extra["thermal.cpu_c"] = tempC
	}

	return metrics, extra
}

func parseMemKB(line string) uint64 {
	fields := strings.Fields(line)
	if len(fields) >= 2 {
		val, _ := strconv.ParseUint(fields[1], 10, 64)
		return val
	}
	return 0
}

func (c *Collector) calculateLinuxCPU() (float64, float64, map[string]float64) {
	c.mu.Lock()
	defer c.mu.Unlock()

	statPath := filepath.Join(c.procPath, "stat")
	file, err := os.Open(statPath)
	if err != nil {
		return 0.0, 0.0, nil
	}
	defer file.Close()

	var totalPct, iowaitPct float64
	perCore := make(map[string]float64)
	coreCount := 0

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := scanner.Text()
		if !strings.HasPrefix(line, "cpu") {
			break
		}
		parts := strings.Fields(line)
		if len(parts) < 5 {
			continue
		}
		var total, idle, iowait uint64
		for i := 1; i < len(parts); i++ {
			val, _ := strconv.ParseUint(parts[i], 10, 64)
			total += val
			if i == 4 {
				idle = val
			} else if i == 5 {
				iowait = val
			}
		}

		snap := cpuSnapshot{idle: idle, iowait: iowait, total: total}
		if parts[0] == "cpu" {
			if c.lastCPU.total > 0 && total > c.lastCPU.total {
				dTotal := total - c.lastCPU.total
				dIdle := idle - c.lastCPU.idle
				dIowait := iowait - c.lastCPU.iowait
				totalPct = clampPct(float64(dTotal-dIdle) / float64(dTotal) * 100.0)
				iowaitPct = clampPct(float64(dIowait) / float64(dTotal) * 100.0)
			}
			c.lastCPU = snap
		} else if coreCount < 32 {
			// Cap per-core series at 32 cores to keep frame cardinality bounded.
			prev, seen := c.lastCores[parts[0]]
			if seen && total > prev.total {
				dTotal := total - prev.total
				dIdle := idle - prev.idle
				perCore[parts[0]] = clampPct(float64(dTotal-dIdle) / float64(dTotal) * 100.0)
			}
			c.lastCores[parts[0]] = snap
			coreCount++
		}
	}

	return totalPct, iowaitPct, perCore
}

func clampPct(v float64) float64 {
	if v < 0 {
		return 0
	}
	if v > 100 {
		return 100
	}
	return v
}

func (c *Collector) collectPlatformNetDisk() (*NetworkMetrics, *DiskIOMetrics, map[string]float64) {
	c.mu.Lock()
	defer c.mu.Unlock()

	now := time.Now()
	extra := make(map[string]float64)
	var netAgg *NetworkMetrics
	var diskAgg *DiskIOMetrics

	if rawNet, err := os.ReadFile(filepath.Join(c.procPath, "net/dev")); err == nil {
		curr := parseProcNetDev(string(rawNet))
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
	}

	if rawDisk, err := os.ReadFile(filepath.Join(c.procPath, "diskstats")); err == nil {
		curr := parseProcDiskStats(string(rawDisk))
		if !c.lastDiskAt.IsZero() {
			dt := now.Sub(c.lastDiskAt).Seconds()
			if dt > 0 {
				var totalRead, totalWrite float64
				for dev, snap := range curr {
					prev, ok := c.lastDisk[dev]
					if !ok || snap.readSectors < prev.readSectors || snap.writeSectors < prev.writeSectors {
						continue
					}
					// Linux diskstats sectors are 512 bytes.
					readBps := float64(snap.readSectors-prev.readSectors) * 512.0 / dt
					writeBps := float64(snap.writeSectors-prev.writeSectors) * 512.0 / dt
					totalRead += readBps
					totalWrite += writeBps
					extra["disk.read_bps:"+dev] = readBps
					extra["disk.write_bps:"+dev] = writeBps
				}
				diskAgg = &DiskIOMetrics{ReadBps: totalRead, WriteBps: totalWrite}
			}
		}
		c.lastDisk = curr
		c.lastDiskAt = now
	}

	return netAgg, diskAgg, extra
}

func (c *Collector) collectPlatformBattery() *BatteryMetrics {
	powerDir := filepath.Join(c.sysPath, "class/power_supply")
	entries, err := os.ReadDir(powerDir)
	if err != nil {
		return nil
	}
	for _, entry := range entries {
		base := filepath.Join(powerDir, entry.Name())
		typ := strings.ToLower(c.readFirstLine(filepath.Join(base, "type")))
		if typ != "battery" {
			continue
		}
		capStr := c.readFirstLine(filepath.Join(base, "capacity"))
		pct, err := strconv.ParseFloat(capStr, 64)
		if err != nil {
			continue
		}
		status := strings.ToLower(c.readFirstLine(filepath.Join(base, "status")))
		batt := &BatteryMetrics{
			Pct:      clampPct(pct),
			Charging: status == "charging" || status == "full",
		}
		if tempRaw := c.readFirstLine(filepath.Join(base, "temp")); tempRaw != "" {
			if t, err := strconv.ParseFloat(tempRaw, 64); err == nil && t > 0 {
				// sysfs power_supply temp is in tenths of a degree C.
				batt.TemperatureC = t / 10.0
			}
		}
		return batt
	}
	return nil
}

func (c *Collector) readLinuxThermalC() (float64, bool) {
	thermalDir := filepath.Join(c.sysPath, "class/thermal")
	entries, err := os.ReadDir(thermalDir)
	if err != nil {
		return 0, false
	}
	var fallback float64
	hasFallback := false
	for _, entry := range entries {
		if !strings.HasPrefix(entry.Name(), "thermal_zone") {
			continue
		}
		base := filepath.Join(thermalDir, entry.Name())
		tempRaw := c.readFirstLine(filepath.Join(base, "temp"))
		milliC, err := strconv.ParseFloat(tempRaw, 64)
		if err != nil || milliC <= 0 {
			continue
		}
		tempC := milliC / 1000.0
		zoneType := strings.ToLower(c.readFirstLine(filepath.Join(base, "type")))
		if strings.Contains(zoneType, "pkg") || strings.Contains(zoneType, "cpu") || strings.Contains(zoneType, "x86") || strings.Contains(zoneType, "soc") {
			return tempC, true
		}
		if !hasFallback {
			fallback = tempC
			hasFallback = true
		}
	}
	return fallback, hasFallback
}

func (c *Collector) collectPlatformExtra() ([]ZFSPoolHealth, []DiskSMART) {
	return c.getZFSPools(), c.getSMARTDisks()
}

// getZFSPools reads ZFS pool stats if running on Proxmox/TrueNAS or host with ZFS.
func (c *Collector) getZFSPools() []ZFSPoolHealth {
	cmd := exec.Command("zpool", "list", "-H", "-o", "name,size,alloc,free,cap,health")
	out, err := cmd.Output()
	if err != nil {
		return c.getZFSPoolsFromProc()
	}

	var pools []ZFSPoolHealth
	scanner := bufio.NewScanner(strings.NewReader(string(out)))
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) >= 6 {
			capStr := strings.TrimSuffix(fields[4], "%")
			capPct, _ := strconv.Atoi(capStr)
			pools = append(pools, ZFSPoolHealth{
				PoolName:    fields[0],
				State:       fields[5],
				CapacityPct: capPct,
			})
		}
	}
	return pools
}

func (c *Collector) getZFSPoolsFromProc() []ZFSPoolHealth {
	zfsProcDir := filepath.Join(c.procPath, "spl/kstat/zfs")
	entries, err := os.ReadDir(zfsProcDir)
	if err != nil {
		return nil
	}

	var pools []ZFSPoolHealth
	for _, entry := range entries {
		if entry.IsDir() {
			pools = append(pools, ZFSPoolHealth{
				PoolName: entry.Name(),
				State:    "ONLINE",
			})
		}
	}
	return pools
}

// getSMARTDisks queries disk health status or sysfs devices.
func (c *Collector) getSMARTDisks() []DiskSMART {
	blockDir := filepath.Join(c.sysPath, "block")
	entries, err := os.ReadDir(blockDir)
	if err != nil {
		return nil
	}

	var disks []DiskSMART
	for _, entry := range entries {
		name := entry.Name()
		if strings.HasPrefix(name, "sd") || strings.HasPrefix(name, "nvme") || strings.HasPrefix(name, "vd") {
			modelPath := filepath.Join(blockDir, name, "device", "model")
			model := c.readFirstLine(modelPath)
			disks = append(disks, DiskSMART{
				DeviceName: "/dev/" + name,
				Model:      strings.TrimSpace(model),
				Health:     "UNKNOWN",
			})
		}
	}
	return disks
}
