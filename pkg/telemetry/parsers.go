package telemetry

import (
	"bufio"
	"strconv"
	"strings"
)

// parseProcNetDev parses Linux /proc/net/dev content into per-interface byte counters.
func parseProcNetDev(raw string) map[string]netSnapshot {
	out := make(map[string]netSnapshot)
	scanner := bufio.NewScanner(strings.NewReader(raw))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		colon := strings.IndexByte(line, ':')
		if colon <= 0 {
			continue
		}
		iface := strings.TrimSpace(line[:colon])
		if iface == "" || iface == "lo" || strings.HasPrefix(iface, "veth") {
			continue
		}
		fields := strings.Fields(line[colon+1:])
		if len(fields) < 10 {
			continue
		}
		rx, err1 := strconv.ParseUint(fields[0], 10, 64)
		tx, err2 := strconv.ParseUint(fields[8], 10, 64)
		if err1 != nil || err2 != nil {
			continue
		}
		out[iface] = netSnapshot{rxBytes: rx, txBytes: tx}
	}
	return out
}

// parseProcDiskStats parses Linux /proc/diskstats for whole block devices (sd*, nvme*n*, vd*, mmcblk*).
func parseProcDiskStats(raw string) map[string]diskSnapshot {
	out := make(map[string]diskSnapshot)
	scanner := bufio.NewScanner(strings.NewReader(raw))
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) < 14 {
			continue
		}
		dev := fields[2]
		if !isTrackedBlockDevice(dev) {
			continue
		}
		readSectors, err1 := strconv.ParseUint(fields[5], 10, 64)
		writeSectors, err2 := strconv.ParseUint(fields[9], 10, 64)
		if err1 != nil || err2 != nil {
			continue
		}
		out[dev] = diskSnapshot{readSectors: readSectors, writeSectors: writeSectors}
	}
	return out
}

func isTrackedBlockDevice(name string) bool {
	if strings.HasPrefix(name, "loop") || strings.HasPrefix(name, "ram") || strings.HasPrefix(name, "dm-") {
		return false
	}
	// Whole SCSI/VirtIO disks like sda, vda (skip partitions sda1)
	if (strings.HasPrefix(name, "sd") || strings.HasPrefix(name, "vd") || strings.HasPrefix(name, "hd")) && len(name) >= 3 {
		last := name[len(name)-1]
		return last >= 'a' && last <= 'z'
	}
	// Whole NVMe namespaces like nvme0n1 (skip partitions nvme0n1p1)
	if strings.HasPrefix(name, "nvme") && !strings.Contains(name, "p") {
		return true
	}
	// Whole MMC/SD devices like mmcblk0 (skip mmcblk0p1)
	if strings.HasPrefix(name, "mmcblk") && !strings.Contains(name, "p") {
		return true
	}
	return false
}

// parseDarwinPmsetBattery extracts battery percentage and charging state from `pmset -g batt`.
func parseDarwinPmsetBattery(raw string) *BatteryMetrics {
	lines := strings.Split(raw, "\n")
	for _, line := range lines {
		if !strings.Contains(line, "%") {
			continue
		}
		fields := strings.Fields(line)
		for _, f := range fields {
			if strings.Contains(f, "%") {
				clean := strings.TrimRight(f, "%;")
				if pct, err := strconv.ParseFloat(clean, 64); err == nil && pct >= 0 && pct <= 100 {
					lower := strings.ToLower(line)
					charging := strings.Contains(lower, "charging") && !strings.Contains(lower, "discharging")
					if strings.Contains(lower, "charged") && strings.Contains(strings.ToLower(raw), "ac power") {
						charging = true
					}
					return &BatteryMetrics{
						Pct:      pct,
						Charging: charging,
					}
				}
			}
		}
	}
	return nil
}

// parseDarwinVmStat parses `vm_stat` output to estimate available RAM in bytes.
func parseDarwinVmStat(raw string) (pageSize uint64, availBytes uint64) {
	pageSize = 4096
	var freePages, inactivePages, speculativePages uint64
	scanner := bufio.NewScanner(strings.NewReader(raw))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if strings.Contains(line, "page size of") {
			for _, tok := range strings.Fields(line) {
				if n, err := strconv.ParseUint(tok, 10, 64); err == nil && n >= 4096 {
					pageSize = n
					break
				}
			}
			continue
		}
		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}
		key := strings.TrimSpace(parts[0])
		valStr := strings.TrimRight(strings.TrimSpace(parts[1]), ".")
		val, err := strconv.ParseUint(valStr, 10, 64)
		if err != nil {
			continue
		}
		switch key {
		case "Pages free":
			freePages = val
		case "Pages inactive":
			inactivePages = val
		case "Pages speculative":
			speculativePages = val
		}
	}
	return pageSize, (freePages + inactivePages + speculativePages) * pageSize
}

// parseDarwinLoadAvg parses `sysctl -n vm.loadavg` output like "{ 1.42 1.15 0.98 }".
func parseDarwinLoadAvg(raw string) (float64, float64, float64) {
	clean := strings.Trim(strings.TrimSpace(raw), "{}")
	fields := strings.Fields(clean)
	if len(fields) < 3 {
		return 0, 0, 0
	}
	l1, _ := strconv.ParseFloat(fields[0], 64)
	l5, _ := strconv.ParseFloat(fields[1], 64)
	l15, _ := strconv.ParseFloat(fields[2], 64)
	return l1, l5, l15
}
