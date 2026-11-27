package telemetry

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

func TestParseProcNetDev(t *testing.T) {
	sample := `Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 1000       10    0    0    0     0          0         0     1000       10    0    0    0     0       0          0
  eth0: 500000    400    0    0    0     0          0         0   250000      200    0    0    0     0       0          0
 veth9:  99999     50    0    0    0     0          0         0    88888       40    0    0    0     0       0          0
 wlan0: 123456    100    0    0    0     0          0         0    65432       80    0    0    0     0       0          0
`
	res := parseProcNetDev(sample)
	if _, hasLo := res["lo"]; hasLo {
		t.Fatal("expected loopback interface lo to be excluded")
	}
	if _, hasVeth := res["veth9"]; hasVeth {
		t.Fatal("expected veth interface to be excluded")
	}
	if res["eth0"].rxBytes != 500000 || res["eth0"].txBytes != 250000 {
		t.Fatalf("unexpected eth0 counters: %+v", res["eth0"])
	}
	if res["wlan0"].rxBytes != 123456 || res["wlan0"].txBytes != 65432 {
		t.Fatalf("unexpected wlan0 counters: %+v", res["wlan0"])
	}
}

func TestParseProcDiskStats(t *testing.T) {
	sample := `   8       0 sda 100 0 2000 0 50 0 1000 0 0 0 0 0 0 0
   8       1 sda1 100 0 2000 0 50 0 1000 0 0 0 0 0 0 0
 259       0 nvme0n1 300 0 8000 0 200 0 4000 0 0 0 0 0 0 0
 259       1 nvme0n1p1 300 0 8000 0 200 0 4000 0 0 0 0 0 0 0
   7       0 loop0 10 0 20 0 0 0 0 0 0 0 0 0 0 0
`
	res := parseProcDiskStats(sample)
	if len(res) != 2 {
		t.Fatalf("expected 2 whole block devices, got %d: %+v", len(res), res)
	}
	if res["sda"].readSectors != 2000 || res["sda"].writeSectors != 1000 {
		t.Fatalf("unexpected sda stats: %+v", res["sda"])
	}
	if res["nvme0n1"].readSectors != 8000 || res["nvme0n1"].writeSectors != 4000 {
		t.Fatalf("unexpected nvme0n1 stats: %+v", res["nvme0n1"])
	}
}

func TestDarwinParsers(t *testing.T) {
	pmsetOut := `Now drawing from 'Battery Power'
 -InternalBattery-0 (id=1234567)	78%; discharging; 4:12 remaining present: true`
	batt := parseDarwinPmsetBattery(pmsetOut)
	if batt == nil || batt.Pct != 78 || batt.Charging {
		t.Fatalf("unexpected discharging battery parse: %+v", batt)
	}

	pmsetCharging := `Now drawing from 'AC Power'
 -InternalBattery-0 (id=1234567)	94%; charging; 0:18 remaining present: true`
	batt2 := parseDarwinPmsetBattery(pmsetCharging)
	if batt2 == nil || batt2.Pct != 94 || !batt2.Charging {
		t.Fatalf("unexpected charging battery parse: %+v", batt2)
	}

	vmStat := `Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               10000.
Pages active:                             50000.
Pages inactive:                           20000.
Pages speculative:                         5000.`
	ps, avail := parseDarwinVmStat(vmStat)
	if ps != 16384 || avail != (10000+20000+5000)*16384 {
		t.Fatalf("unexpected vm_stat parse: pageSize=%d avail=%d", ps, avail)
	}

	l1, l5, l15 := parseDarwinLoadAvg("{ 2.45 1.80 1.20 }")
	if l1 != 2.45 || l5 != 1.80 || l15 != 1.20 {
		t.Fatalf("unexpected loadavg: %v %v %v", l1, l5, l15)
	}
}

func TestLinuxCollectorWithFixtures(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("linux fixture test runs on linux")
	}
	tmp := t.TempDir()
	proc := filepath.Join(tmp, "proc")
	sys := filepath.Join(tmp, "sys")

	mustWrite := func(rel, content string) {
		full := filepath.Join(tmp, rel)
		if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(full, []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	mustWrite("proc/version", "Linux version 6.8.0-meerkat")
	mustWrite("proc/uptime", "3600.50 7200.00")
	mustWrite("proc/loadavg", "0.75 0.50 0.25 1/200 1234")
	mustWrite("proc/meminfo", "MemTotal:       16777216 kB\nMemAvailable:    8388608 kB\nSwapTotal:       2097152 kB\nSwapFree:        1048576 kB\n")
	mustWrite("proc/stat", "cpu  100 0 50 800 50 0 0 0\ncpu0 50 0 25 400 25 0 0 0\ncpu1 50 0 25 400 25 0 0 0\n")
	mustWrite("proc/net/dev", "Inter-|\n face |\neth0: 10000 10 0 0 0 0 0 0 5000 5 0 0 0 0 0 0\n")
	mustWrite("proc/diskstats", "8 0 sda 10 0 100 0 10 0 200 0 0 0 0 0 0 0\n")
	mustWrite("sys/class/power_supply/BAT0/type", "Battery\n")
	mustWrite("sys/class/power_supply/BAT0/capacity", "84\n")
	mustWrite("sys/class/power_supply/BAT0/status", "Charging\n")
	mustWrite("sys/class/power_supply/BAT0/temp", "325\n")
	mustWrite("sys/class/thermal/thermal_zone0/type", "x86_pkg_temp\n")
	mustWrite("sys/class/thermal/thermal_zone0/temp", "54500\n")
	mustWrite("sys/devices/virtual/dmi/id/sys_vendor", "Framework\n")
	mustWrite("sys/devices/virtual/dmi/id/product_name", "Laptop 13\n")

	c := &Collector{
		procPath:  proc,
		sysPath:   sys,
		hostname:  "dev-laptop",
		lastCores: make(map[string]cpuSnapshot),
		lastNet:   make(map[string]netSnapshot),
		lastDisk:  make(map[string]diskSnapshot),
	}

	// First sample primes delta counters
	if _, err := c.CollectMetrics("dev-laptop"); err != nil {
		t.Fatal(err)
	}
	time.Sleep(20 * time.Millisecond)

	// Advance counters for second sample
	mustWrite("proc/stat", "cpu  150 0 75 850 75 0 0 0\ncpu0 75 0 40 425 35 0 0 0\ncpu1 75 0 35 425 40 0 0 0\n")
	mustWrite("proc/net/dev", "Inter-|\n face |\neth0: 20000 20 0 0 0 0 0 0 15000 15 0 0 0 0 0 0\n")
	mustWrite("proc/diskstats", "8 0 sda 20 0 300 0 20 0 500 0 0 0 0 0 0 0\n")

	snap, err := c.CollectMetrics("dev-laptop")
	if err != nil {
		t.Fatal(err)
	}
	if snap.Platform != "linux" || snap.UptimeSec != 3600 {
		t.Fatalf("unexpected platform/uptime: %s / %d", snap.Platform, snap.UptimeSec)
	}
	if snap.HostMetrics.RAMUsagePct != 50.0 {
		t.Fatalf("expected 50%% RAM usage, got %v", snap.HostMetrics.RAMUsagePct)
	}
	if snap.Battery == nil || snap.Battery.Pct != 84 || !snap.Battery.Charging || snap.Battery.TemperatureC != 32.5 {
		t.Fatalf("unexpected battery: %+v", snap.Battery)
	}
	if snap.Network == nil || snap.Network.RxBps <= 0 || snap.Network.TxBps <= 0 {
		t.Fatalf("expected positive network throughput, got %+v", snap.Network)
	}
	if snap.DiskIO == nil || snap.DiskIO.ReadBps <= 0 || snap.DiskIO.WriteBps <= 0 {
		t.Fatalf("expected positive disk throughput, got %+v", snap.DiskIO)
	}
	if snap.Metrics["thermal.cpu_c"] != 54.5 {
		t.Fatalf("expected thermal.cpu_c=54.5, got %v", snap.Metrics["thermal.cpu_c"])
	}
	if snap.Metrics["swap.usage_pct"] != 50.0 {
		t.Fatalf("expected swap.usage_pct=50.0, got %v", snap.Metrics["swap.usage_pct"])
	}
	if snap.Metrics["net.rx_bps:eth0"] <= 0 || snap.Metrics["disk.read_bps:sda"] <= 0 {
		t.Fatalf("expected per-interface & per-disk dynamic metrics, got %+v", snap.Metrics)
	}
	if snap.Hardware["vendor"] != "Framework" || snap.Hardware["model"] != "Laptop 13" {
		t.Fatalf("unexpected hardware info: %+v", snap.Hardware)
	}
}
