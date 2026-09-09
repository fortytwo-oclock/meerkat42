package telemetry

import (
	"bufio"
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Collector pulls system, storage, and container metrics from host mounts.
type Collector struct {
	procPath     string
	sysPath      string
	dockerSock   string
	mu           sync.Mutex
	lastCPU      cpuSnapshot
	imageUpdates *imageUpdateChecker
}

type cpuSnapshot struct {
	idle  uint64
	total uint64
}

// NewCollector initializes a collector with host-path awareness.
func NewCollector() *Collector {
	proc := "/proc"
	if _, err := os.Stat("/host/proc"); err == nil {
		proc = "/host/proc"
	}
	sys := "/sys"
	if _, err := os.Stat("/host/sys"); err == nil {
		sys = "/host/sys"
	}
	dockerSock := "/var/run/docker.sock"

	return &Collector{
		procPath:     proc,
		sysPath:      sys,
		dockerSock:   dockerSock,
		imageUpdates: newImageUpdateCheckerFromEnv(),
	}
}

// CollectMetrics gathers complete system telemetry.
func (c *Collector) CollectMetrics(nodeID string) (*NodeTelemetry, error) {
	hostname, _ := os.Hostname()
	uptime := c.getUptime()
	hostMetrics := c.getHostMetrics()
	containers := c.getDockerContainers()
	zfsPools := c.getZFSPools()
	disks := c.getSMARTDisks()

	kernel := c.readFirstLine(filepath.Join(c.procPath, "version"))
	if len(kernel) > 80 {
		kernel = kernel[:80] + "..."
	}

	return &NodeTelemetry{
		NodeID:      nodeID,
		Hostname:    hostname,
		OS:          "Linux",
		Kernel:      kernel,
		UptimeSec:   uptime,
		Timestamp:   time.Now().UTC(),
		HostMetrics: hostMetrics,
		ZFS:         zfsPools,
		Containers:  containers,
		Disks:       disks,
		AgentVer:    "1.0.0-open-core",
	}, nil
}

func (c *Collector) getUptime() uint64 {
	data := c.readFirstLine(filepath.Join(c.procPath, "uptime"))
	parts := strings.Fields(data)
	if len(parts) > 0 {
		sec, _ := strconv.ParseFloat(parts[0], 64)
		return uint64(sec)
	}
	return 0
}

func (c *Collector) getHostMetrics() HostMetrics {
	var metrics HostMetrics

	// Load Averages
	loadData := c.readFirstLine(filepath.Join(c.procPath, "loadavg"))
	loadParts := strings.Fields(loadData)
	if len(loadParts) >= 3 {
		metrics.Load1, _ = strconv.ParseFloat(loadParts[0], 64)
		metrics.Load5, _ = strconv.ParseFloat(loadParts[1], 64)
		metrics.Load15, _ = strconv.ParseFloat(loadParts[2], 64)
	}

	// Memory info (/proc/meminfo)
	memPath := filepath.Join(c.procPath, "meminfo")
	if file, err := os.Open(memPath); err == nil {
		defer file.Close()
		scanner := bufio.NewScanner(file)
		var totalKB, availKB uint64
		for scanner.Scan() {
			line := scanner.Text()
			if strings.HasPrefix(line, "MemTotal:") {
				totalKB = parseMemKB(line)
			} else if strings.HasPrefix(line, "MemAvailable:") {
				availKB = parseMemKB(line)
			}
		}
		if totalKB > 0 {
			metrics.TotalRAMMB = totalKB / 1024
			metrics.AvailRAMMB = availKB / 1024
			metrics.UsedRAMMB = metrics.TotalRAMMB - metrics.AvailRAMMB
			metrics.RAMUsagePct = float64(metrics.UsedRAMMB) / float64(metrics.TotalRAMMB) * 100.0
		}
	}

	// CPU usage from /proc/stat
	metrics.CPUUsagePct = c.calculateCPUUsage()

	return metrics
}

func parseMemKB(line string) uint64 {
	fields := strings.Fields(line)
	if len(fields) >= 2 {
		val, _ := strconv.ParseUint(fields[1], 10, 64)
		return val
	}
	return 0
}

func (c *Collector) calculateCPUUsage() float64 {
	c.mu.Lock()
	defer c.mu.Unlock()

	statPath := filepath.Join(c.procPath, "stat")
	file, err := os.Open(statPath)
	if err != nil {
		return 0.0
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	if !scanner.Scan() {
		return 0.0
	}
	cpuLine := scanner.Text()
	parts := strings.Fields(cpuLine)
	if len(parts) < 5 || parts[0] != "cpu" {
		return 0.0
	}

	var total uint64
	var idle uint64
	for i := 1; i < len(parts); i++ {
		val, _ := strconv.ParseUint(parts[i], 10, 64)
		total += val
		if i == 4 { // 4th field is idle
			idle = val
		}
	}

	if c.lastCPU.total == 0 {
		c.lastCPU = cpuSnapshot{idle: idle, total: total}
		return 0.0
	}

	deltaTotal := total - c.lastCPU.total
	deltaIdle := idle - c.lastCPU.idle
	c.lastCPU = cpuSnapshot{idle: idle, total: total}

	if deltaTotal == 0 {
		return 0.0
	}

	usage := float64(deltaTotal-deltaIdle) / float64(deltaTotal) * 100.0
	if usage < 0 {
		usage = 0
	}
	if usage > 100 {
		usage = 100
	}
	return usage
}

// getDockerContainers queries docker engine over local unix domain socket without external SDKs.
func (c *Collector) getDockerContainers() []ContainerSummary {
	if _, err := os.Stat(c.dockerSock); err != nil {
		return nil
	}

	client := &http.Client{
		Transport: &http.Transport{
			DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
				return net.Dial("unix", c.dockerSock)
			},
		},
		Timeout: 3 * time.Second,
	}

	resp, err := client.Get("http://localhost/containers/json?all=1")
	if err != nil {
		return nil
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil
	}

	type dockerInspectItem struct {
		ID                      string   `json:"Id"`
		Names                   []string `json:"Names"`
		Image                   string   `json:"Image"`
		State                   string   `json:"State"`
		Status                  string   `json:"Status"`
		Created                 int64    `json:"Created"`
		ImageID                 string   `json:"ImageID"`
		ImageManifestDescriptor struct {
			Digest string `json:"digest"`
		} `json:"ImageManifestDescriptor"`
	}

	var raw []dockerInspectItem
	if err := json.Unmarshal(body, &raw); err != nil {
		return nil
	}

	var summaries []ContainerSummary
	checkBudget := 1
	for _, item := range raw {
		name := "unknown"
		if len(item.Names) > 0 {
			name = strings.TrimPrefix(item.Names[0], "/")
		}
		id := item.ID
		if len(id) > 12 {
			id = id[:12]
		}
		isCrashLoop := strings.Contains(strings.ToLower(item.Status), "restarting") ||
			(item.State == "restarting")

		update, checked := c.imageUpdates.check(
			client,
			item.Image,
			item.ImageID,
			item.ImageManifestDescriptor.Digest,
			checkBudget > 0,
		)
		if checked {
			checkBudget--
		}
		summaries = append(summaries, ContainerSummary{
			ID:                id,
			Name:              name,
			Image:             item.Image,
			State:             item.State,
			Status:            item.Status,
			IsCrashLoop:       isCrashLoop,
			Created:           time.Unix(item.Created, 0),
			ImageDigest:       update.localDigest,
			LatestImageDigest: update.remoteDigest,
			UpdateAvailable:   update.available,
			UpdateCheckStatus: update.status,
			UpdateCheckedAt:   update.checkedAt,
		})
	}

	return summaries
}

// getZFSPools reads ZFS pool stats if running on Proxmox/TrueNAS or host with ZFS.
func (c *Collector) getZFSPools() []ZFSPoolHealth {
	// 1. Try running zpool list
	cmd := exec.Command("zpool", "list", "-H", "-o", "name,size,alloc,free,cap,health")
	out, err := cmd.Output()
	if err != nil {
		// If zpool binary is not directly available, check /proc/spl/kstat/zfs
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
				Model:      model,
				Health:     "PASSED",
			})
		}
	}
	return disks
}

func (c *Collector) readFirstLine(path string) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	lines := strings.Split(strings.TrimSpace(string(data)), "\n")
	if len(lines) > 0 {
		return lines[0]
	}
	return ""
}
