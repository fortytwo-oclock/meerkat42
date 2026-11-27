package telemetry

import (
	"context"
	"encoding/json"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"runtime"
	"strings"
	"sync"
	"time"

	"control-plane/pkg/proxmox"
)

const AgentVersion = "2.0.0-open-core"

// Collector pulls cross-platform system, network, disk, battery, storage, and
// container metrics. OS-specific collection is delegated to host_*.go files.
type Collector struct {
	procPath     string
	sysPath      string
	dockerSock   string
	hostname     string
	mu           sync.Mutex
	lastCPU      cpuSnapshot
	lastCores    map[string]cpuSnapshot
	lastNet      map[string]netSnapshot
	lastNetAt    time.Time
	lastDisk     map[string]diskSnapshot
	lastDiskAt   time.Time
	imageUpdates *imageUpdateChecker
	proxmox      *proxmox.Client
}

type cpuSnapshot struct {
	idle   uint64
	iowait uint64
	total  uint64
}

type netSnapshot struct {
	rxBytes uint64
	txBytes uint64
}

type diskSnapshot struct {
	readSectors  uint64
	writeSectors uint64
}

// NewCollector initializes a collector with host-path awareness.
func NewCollector(proxmoxClients ...*proxmox.Client) *Collector {
	proc := "/proc"
	if _, err := os.Stat("/host/proc"); err == nil {
		proc = "/host/proc"
	}
	sys := "/sys"
	if _, err := os.Stat("/host/sys"); err == nil {
		sys = "/host/sys"
	}
	dockerSock := "/var/run/docker.sock"
	if envSock := strings.TrimSpace(os.Getenv("DOCKER_SOCK")); envSock != "" {
		dockerSock = envSock
	}
	hostname := strings.TrimSpace(os.Getenv("HOSTNAME_OVERRIDE"))
	if hostname == "" {
		hostname, _ = os.Hostname()
	}

	var proxmoxClient *proxmox.Client
	if len(proxmoxClients) > 0 {
		proxmoxClient = proxmoxClients[0]
	} else {
		var err error
		proxmoxClient, err = proxmox.NewFromEnv()
		if err != nil {
			log.Printf("[Proxmox] Integration disabled: %v", err)
		}
	}
	return &Collector{
		procPath:     proc,
		sysPath:      sys,
		dockerSock:   dockerSock,
		hostname:     hostname,
		lastCores:    make(map[string]cpuSnapshot),
		lastNet:      make(map[string]netSnapshot),
		lastDisk:     make(map[string]diskSnapshot),
		imageUpdates: newImageUpdateCheckerFromEnv(),
		proxmox:      proxmoxClient,
	}
}

// Hostname returns the operator override or the local system hostname used in
// handshake and telemetry frames.
func (c *Collector) Hostname() string {
	return c.hostname
}

// HardwareInfo returns static/slow-changing hardware inventory for handshakes.
func (c *Collector) HardwareInfo() map[string]interface{} {
	_, _, _, hw := c.collectPlatformInfo()
	return hw
}

// NormalizedPlatform maps runtime.GOOS to Meerkat42's canonical platform names.
func NormalizedPlatform() string {
	switch runtime.GOOS {
	case "darwin":
		return "macos"
	case "windows":
		return "windows"
	case "android":
		return "android"
	case "ios":
		return "ios"
	default:
		return "linux"
	}
}

// DefaultDeviceClass returns DEVICE_CLASS if set, otherwise infers a sensible
// default from the host OS (workstation on Windows/macOS, server on Linux).
func DefaultDeviceClass() string {
	if envClass := strings.TrimSpace(strings.ToLower(os.Getenv("DEVICE_CLASS"))); envClass != "" {
		return envClass
	}
	switch runtime.GOOS {
	case "darwin", "windows":
		return "workstation"
	case "android", "ios":
		return "mobile"
	default:
		return "server"
	}
}

// CollectMetrics gathers complete cross-platform system telemetry.
func (c *Collector) CollectMetrics(nodeID string) (*NodeTelemetry, error) {
	osName, kernel, uptime, hardware := c.collectPlatformInfo()
	hostMetrics, hostExtra := c.collectPlatformHostMetrics()
	netMetrics, diskIO, ioMetrics := c.collectPlatformNetDisk()
	battery := c.collectPlatformBattery()
	zfsPools, disks := c.collectPlatformExtra()
	containers := c.getDockerContainers()

	var guests []proxmox.Guest
	if c.proxmox != nil {
		var err error
		guests, err = c.proxmox.Guests()
		if err != nil {
			log.Printf("[Proxmox] Inventory unavailable: %v", err)
		}
	}

	dynamicMetrics := make(map[string]float64)
	dynamicMetrics["cpu.cores"] = float64(runtime.NumCPU())
	for k, v := range hostExtra {
		dynamicMetrics[k] = v
	}
	for k, v := range ioMetrics {
		dynamicMetrics[k] = v
	}

	return &NodeTelemetry{
		NodeID:      nodeID,
		Hostname:    c.hostname,
		OS:          osName,
		Platform:    NormalizedPlatform(),
		DeviceClass: DefaultDeviceClass(),
		Arch:        runtime.GOARCH,
		Kernel:      kernel,
		UptimeSec:   uptime,
		Timestamp:   time.Now().UTC(),
		HostMetrics: hostMetrics,
		Battery:     battery,
		Network:     netMetrics,
		DiskIO:      diskIO,
		Hardware:    hardware,
		Metrics:     dynamicMetrics,
		ZFS:         zfsPools,
		Containers:  containers,
		Disks:       disks,
		Proxmox:     guests,
		AgentVer:    AgentVersion,
	}, nil
}

// getDockerContainers queries docker engine over local unix domain socket without external SDKs.
func (c *Collector) getDockerContainers() []ContainerSummary {
	if runtime.GOOS == "windows" || c.dockerSock == "" {
		return nil
	}
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

func (c *Collector) readFirstLine(path string) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	lines := strings.SplitN(string(data), "\n", 2)
	return strings.TrimSpace(lines[0])
}
