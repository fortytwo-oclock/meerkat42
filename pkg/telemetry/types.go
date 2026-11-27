package telemetry

import (
	"time"

	"control-plane/pkg/proxmox"
)

// NodeTelemetry represents the outbound telemetry payload sent by the Edge Agent.
type NodeTelemetry struct {
	NodeID      string                 `json:"node_id"`
	Hostname    string                 `json:"hostname"`
	OS          string                 `json:"os"`
	Platform    string                 `json:"platform,omitempty"`
	DeviceClass string                 `json:"device_class,omitempty"`
	Arch        string                 `json:"arch"`
	Kernel      string                 `json:"kernel"`
	UptimeSec   uint64                 `json:"uptime_sec"`
	Timestamp   time.Time              `json:"timestamp"`
	HostMetrics HostMetrics            `json:"host_metrics"`
	Battery     *BatteryMetrics        `json:"battery,omitempty"`
	Network     *NetworkMetrics        `json:"network,omitempty"`
	DiskIO      *DiskIOMetrics         `json:"disk_io,omitempty"`
	Location    *DeviceLocation        `json:"location,omitempty"`
	Hardware    map[string]interface{} `json:"hardware,omitempty"`
	Metrics     map[string]float64     `json:"metrics,omitempty"`
	ZFS         []ZFSPoolHealth        `json:"zfs,omitempty"`
	Containers  []ContainerSummary     `json:"containers,omitempty"`
	Disks       []DiskSMART            `json:"disks,omitempty"`
	Proxmox     []proxmox.Guest        `json:"proxmox_guests,omitempty"`
	AgentVer    string                 `json:"agent_version"`
}

// BatteryMetrics captures workstation or mobile battery telemetry.
type BatteryMetrics struct {
	Pct          float64 `json:"pct"`
	Charging     bool    `json:"charging"`
	TemperatureC float64 `json:"temperature_c,omitempty"`
}

// NetworkMetrics captures aggregate network throughput.
type NetworkMetrics struct {
	RxBps float64 `json:"rx_bps"`
	TxBps float64 `json:"tx_bps"`
}

// DiskIOMetrics captures aggregate disk throughput.
type DiskIOMetrics struct {
	ReadBps  float64 `json:"read_bps"`
	WriteBps float64 `json:"write_bps"`
}

// DeviceLocation represents opt-in device tracking coordinates.
type DeviceLocation struct {
	Lat       float64 `json:"lat"`
	Lon       float64 `json:"lon"`
	AccuracyM float64 `json:"accuracy_m,omitempty"`
	AltitudeM float64 `json:"altitude_m,omitempty"`
	SpeedMps  float64 `json:"speed_mps,omitempty"`
	Zone      string  `json:"zone,omitempty"`
	Source    string  `json:"source,omitempty"`
	UpdatedAt string  `json:"updated_at,omitempty"`
}

// HostMetrics captures CPU, RAM, and load averages.
type HostMetrics struct {
	Load1       float64 `json:"load_1"`
	Load5       float64 `json:"load_5"`
	Load15      float64 `json:"load_15"`
	CPUUsagePct float64 `json:"cpu_usage_pct"`
	TotalRAMMB  uint64  `json:"total_ram_mb"`
	AvailRAMMB  uint64  `json:"avail_ram_mb"`
	UsedRAMMB   uint64  `json:"used_ram_mb"`
	RAMUsagePct float64 `json:"ram_usage_pct"`
}

// ZFSPoolHealth contains pool capacity, state, and error counters.
type ZFSPoolHealth struct {
	PoolName    string `json:"pool_name"`
	State       string `json:"state"` // ONLINE, DEGRADED, FAULTED
	AllocatedMB uint64 `json:"allocated_mb"`
	SizeMB      uint64 `json:"size_mb"`
	FreeMB      uint64 `json:"free_mb"`
	CapacityPct int    `json:"capacity_pct"`
	ReadErrors  uint64 `json:"read_errors"`
	WriteErrors uint64 `json:"write_errors"`
	CksumErrors uint64 `json:"cksum_errors"`
}

// ContainerSummary captures docker container status, health, and restart churn.
type ContainerSummary struct {
	ID                string     `json:"id"`
	Name              string     `json:"name"`
	Image             string     `json:"image"`
	State             string     `json:"state"`  // running, exited, restarting
	Status            string     `json:"status"` // e.g. "Up 4 days", "Restarting (1) 20 secs ago"
	RestartCount      int        `json:"restart_count"`
	IsCrashLoop       bool       `json:"is_crash_loop"`
	Created           time.Time  `json:"created"`
	ImageDigest       string     `json:"image_digest,omitempty"`
	LatestImageDigest string     `json:"latest_image_digest,omitempty"`
	UpdateAvailable   bool       `json:"update_available"`
	UpdateCheckStatus string     `json:"update_check_status,omitempty"`
	UpdateCheckedAt   *time.Time `json:"update_checked_at,omitempty"`
}

// DiskSMART represents storage drive forensic summary.
type DiskSMART struct {
	DeviceName  string `json:"device_name"` // e.g. /dev/sda, nvme0n1
	Model       string `json:"model"`
	Serial      string `json:"serial"`
	Temperature int    `json:"temperature_c"`
	Health      string `json:"health"` // PASSED, FAILING, UNKNOWN
	PowerOnHrs  uint64 `json:"power_on_hours"`
	Reallocated uint64 `json:"reallocated_sectors"`
}

// AuthHandshake is sent by the edge agent upon establishing the outbound WebSocket.
type AuthHandshake struct {
	NodeID      string                 `json:"node_id"`
	AuthToken   string                 `json:"auth_token"`
	AgentVer    string                 `json:"agent_version"`
	Hostname    string                 `json:"hostname"`
	Platform    string                 `json:"platform,omitempty"`
	DeviceClass string                 `json:"device_class,omitempty"`
	Arch        string                 `json:"arch"`
	ClientGroup string                 `json:"client_group,omitempty"`
	Hardware    map[string]interface{} `json:"hardware,omitempty"`
}

// HubCommand is a message sent from the Hub down to the Agent over the persistent tunnel.
type HubCommand struct {
	CommandID string                 `json:"command_id"`
	Action    string                 `json:"action"` // "ping", "request_config_backup", "set_stream_rate"
	Payload   map[string]interface{} `json:"payload,omitempty"`
}
