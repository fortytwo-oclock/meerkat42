package telemetry

import "time"

// NodeTelemetry represents the 10-second outbound payload sent by the Edge Agent.
type NodeTelemetry struct {
	NodeID      string             `json:"node_id"`
	Hostname    string             `json:"hostname"`
	OS          string             `json:"os"`
	Kernel      string             `json:"kernel"`
	UptimeSec   uint64             `json:"uptime_sec"`
	Timestamp   time.Time          `json:"timestamp"`
	HostMetrics HostMetrics        `json:"host_metrics"`
	ZFS         []ZFSPoolHealth    `json:"zfs,omitempty"`
	Containers  []ContainerSummary `json:"containers,omitempty"`
	Disks       []DiskSMART        `json:"disks,omitempty"`
	AgentVer    string             `json:"agent_version"`
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
	NodeID      string `json:"node_id"`
	AuthToken   string `json:"auth_token"`
	AgentVer    string `json:"agent_version"`
	Hostname    string `json:"hostname"`
	ClientGroup string `json:"client_group,omitempty"`
}

// HubCommand is a message sent from the Hub down to the Agent over the persistent tunnel.
type HubCommand struct {
	CommandID string                 `json:"command_id"`
	Action    string                 `json:"action"` // "ping", "request_config_backup", "trigger_scrub"
	Payload   map[string]interface{} `json:"payload,omitempty"`
}
