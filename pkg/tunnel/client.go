package tunnel

import (
	"context"
	"fmt"
	"log"
	"math/rand"
	"net/http"
	"net/url"
	"sync"
	"time"

	"control-plane/pkg/telemetry"
	"github.com/gorilla/websocket"
)

// CommandHandler executes a HubCommand and returns a JSON-serialisable result.
// A non-nil error is reported back to the hub as a failed command_result.
type CommandHandler func(action string, payload map[string]interface{}) (interface{}, error)

// Config holds connection parameters for the outbound tunnel.
type Config struct {
	HubURL         string // "wss://hub.example.com/api/v1/agent/stream" or "ws://localhost:8080/..."
	NodeID         string
	Hostname       string
	Platform       string
	DeviceClass    string
	Arch           string
	AuthToken      string
	ClientGroup    string
	Hardware       map[string]interface{}
	BufferSize     int           // default: 120 samples
	PushInterval   time.Duration // default: 10s
	CommandHandler CommandHandler
}

// streamRateUpdate signals the push loop to temporarily switch telemetry cadence.
type streamRateUpdate struct {
	interval time.Duration
	ttl      time.Duration
}

// commandResult is the frame sent upstream in reply to a HubCommand.
type commandResult struct {
	Type      string      `json:"type"`
	CommandID string      `json:"command_id"`
	Action    string      `json:"action"`
	OK        bool        `json:"ok"`
	Error     string      `json:"error,omitempty"`
	Result    interface{} `json:"result,omitempty"`
}

// Client manages the persistent outbound WebSocket to the Control Plane Hub.
type Client struct {
	cfg          Config
	collector    *telemetry.Collector
	buffer       *TelemetryBuffer
	conn         *websocket.Conn
	mu           sync.Mutex
	stopCh       chan struct{}
	rateUpdateCh chan streamRateUpdate
}

// NewClient creates a new outbound tunnel client.
func NewClient(cfg Config, col *telemetry.Collector) *Client {
	if cfg.PushInterval <= 0 {
		cfg.PushInterval = 10 * time.Second
	}
	return &Client{
		cfg:          cfg,
		collector:    col,
		buffer:       NewTelemetryBuffer(cfg.BufferSize),
		stopCh:       make(chan struct{}),
		rateUpdateCh: make(chan streamRateUpdate, 4),
	}
}

// Start runs the persistent reconnecting loop.
func (c *Client) Start(ctx context.Context) {
	backoff := 1 * time.Second
	maxBackoff := 30 * time.Second

	for {
		select {
		case <-ctx.Done():
			log.Println("[Tunnel] Agent shutting down, stopping tunnel loop.")
			c.closeConnection()
			return
		case <-c.stopCh:
			return
		default:
		}

		err := c.connectAndStream(ctx)
		if err != nil {
			log.Printf("[Tunnel] Connection lost or failed: %v. Reconnecting in %v...", err, backoff)
		}

		// Exponential backoff with jitter
		jitter := time.Duration(rand.Int63n(int64(backoff / 2)))
		select {
		case <-time.After(backoff + jitter):
		case <-ctx.Done():
			return
		}

		backoff *= 2
		if backoff > maxBackoff {
			backoff = maxBackoff
		}
	}
}

// connectAndStream establishes WebSocket connection, authenticates, and streams telemetry.
func (c *Client) connectAndStream(ctx context.Context) error {
	u, err := url.Parse(c.cfg.HubURL)
	if err != nil {
		return fmt.Errorf("invalid Hub URL: %w", err)
	}

	header := http.Header{}
	header.Set("X-Node-ID", c.cfg.NodeID)
	header.Set("X-Auth-Token", c.cfg.AuthToken)

	dialer := websocket.Dialer{
		HandshakeTimeout: 10 * time.Second,
	}

	conn, resp, err := dialer.DialContext(ctx, u.String(), header)
	if err != nil {
		if resp != nil {
			return fmt.Errorf("dial failed with HTTP %d: %w", resp.StatusCode, err)
		}
		return fmt.Errorf("dial failed: %w", err)
	}

	c.mu.Lock()
	c.conn = conn
	c.mu.Unlock()
	log.Printf("[Tunnel] Connected successfully to Hub at %s (Node: %s)", c.cfg.HubURL, c.cfg.NodeID)

	// Send initial Handshake
	handshake := telemetry.AuthHandshake{
		NodeID:      c.cfg.NodeID,
		Hostname:    c.cfg.Hostname,
		Platform:    c.cfg.Platform,
		DeviceClass: c.cfg.DeviceClass,
		Arch:        c.cfg.Arch,
		AuthToken:   c.cfg.AuthToken,
		AgentVer:    telemetry.AgentVersion,
		ClientGroup: c.cfg.ClientGroup,
		Hardware:    c.cfg.Hardware,
	}
	if err := conn.WriteJSON(handshake); err != nil {
		c.closeConnection()
		return fmt.Errorf("send handshake: %w", err)
	}

	// Flush any offline-buffered samples collected before reconnect.
	if pending := c.buffer.Drain(); len(pending) > 0 {
		log.Printf("[Tunnel] Flushing %d offline-buffered telemetry sample(s)", len(pending))
		for idx, sample := range pending {
			c.mu.Lock()
			conn.SetWriteDeadline(time.Now().Add(5 * time.Second))
			err := conn.WriteJSON(sample)
			c.mu.Unlock()
			if err != nil {
				for _, rem := range pending[idx:] {
					c.buffer.Push(rem)
				}
				c.closeConnection()
				return fmt.Errorf("buffer flush failed: %w", err)
			}
		}
	}

	ticker := time.NewTicker(c.cfg.PushInterval)
	defer ticker.Stop()

	var revertTimer *time.Timer
	var revertCh <-chan time.Time
	defer func() {
		if revertTimer != nil {
			revertTimer.Stop()
		}
	}()

	// Downstream command listener in background
	errChan := make(chan error, 2)
	go func() {
		for {
			var cmd telemetry.HubCommand
			err := conn.ReadJSON(&cmd)
			if err != nil {
				errChan <- fmt.Errorf("read error: %w", err)
				return
			}
			log.Printf("[Tunnel] Received hub command: %s (ID: %s)", cmd.Action, cmd.CommandID)
			go c.handleCommand(cmd)
		}
	}()

	// Outbound telemetry push loop
	for {
		select {
		case <-ctx.Done():
			return nil
		case err := <-errChan:
			c.closeConnection()
			return err
		case upd := <-c.rateUpdateCh:
			ticker.Reset(upd.interval)
			if revertTimer != nil {
				revertTimer.Stop()
			}
			if upd.interval != c.cfg.PushInterval && upd.ttl > 0 {
				revertTimer = time.NewTimer(upd.ttl)
				revertCh = revertTimer.C
			} else {
				revertCh = nil
			}
			log.Printf("[Tunnel] Adjusted push cadence to %v (ttl=%v)", upd.interval, upd.ttl)
		case <-revertCh:
			ticker.Reset(c.cfg.PushInterval)
			revertCh = nil
			log.Printf("[Tunnel] Live-tail TTL expired; reverted push cadence to %v", c.cfg.PushInterval)
		case <-ticker.C:
			data, err := c.collector.CollectMetrics(c.cfg.NodeID)
			if err != nil {
				log.Printf("[Collector] Error gathering metrics: %v", err)
				continue
			}

			c.mu.Lock()
			conn.SetWriteDeadline(time.Now().Add(5 * time.Second))
			err = conn.WriteJSON(data)
			c.mu.Unlock()

			if err != nil {
				c.buffer.Push(data)
				c.closeConnection()
				return fmt.Errorf("telemetry write failed: %w", err)
			}
			log.Printf("[Tunnel] Pushed telemetry: Host CPU=%.1f%%, RAM=%.1f%%, Containers=%d, Metrics=%d",
				data.HostMetrics.CPUUsagePct, data.HostMetrics.RAMUsagePct, len(data.Containers), len(data.Metrics))
		}
	}
}

func (c *Client) applyStreamRate(payload map[string]interface{}) (interface{}, error) {
	rawInterval, ok := payload["interval_sec"].(float64)
	if !ok || rawInterval < 1 || rawInterval > 300 {
		return nil, fmt.Errorf("interval_sec must be between 1 and 300")
	}
	ttlSec := 60.0
	if rawTTL, hasTTL := payload["ttl_sec"].(float64); hasTTL {
		if rawTTL < 10 || rawTTL > 600 {
			return nil, fmt.Errorf("ttl_sec must be between 10 and 600")
		}
		ttlSec = rawTTL
	}
	upd := streamRateUpdate{
		interval: time.Duration(int(rawInterval)) * time.Second,
		ttl:      time.Duration(int(ttlSec)) * time.Second,
	}
	select {
	case c.rateUpdateCh <- upd:
	default:
		// Drop stale queued update and enqueue the latest one.
		select {
		case <-c.rateUpdateCh:
		default:
		}
		c.rateUpdateCh <- upd
	}
	return map[string]int{
		"interval_sec": int(rawInterval),
		"ttl_sec":      int(ttlSec),
	}, nil
}

// handleCommand runs a HubCommand via the configured handler and writes the
// result back over the same tunnel.
func (c *Client) handleCommand(cmd telemetry.HubCommand) {
	res := commandResult{Type: "command_result", CommandID: cmd.CommandID, Action: cmd.Action}

	if cmd.Action == "set_stream_rate" {
		if out, err := c.applyStreamRate(cmd.Payload); err != nil {
			res.Error = err.Error()
		} else {
			res.OK = true
			res.Result = out
		}
	} else if c.cfg.CommandHandler == nil {
		res.Error = "agent has no command handler configured"
	} else if out, err := c.cfg.CommandHandler(cmd.Action, cmd.Payload); err != nil {
		res.Error = err.Error()
	} else {
		res.OK = true
		res.Result = out
	}

	c.mu.Lock()
	conn := c.conn
	if conn != nil {
		conn.SetWriteDeadline(time.Now().Add(15 * time.Second))
		if err := conn.WriteJSON(res); err != nil {
			log.Printf("[Tunnel] Failed to send command_result for %s: %v", cmd.CommandID, err)
		}
	}
	c.mu.Unlock()
}

func (c *Client) closeConnection() {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.conn != nil {
		c.conn.Close()
		c.conn = nil
	}
}

// Stop terminates the client.
func (c *Client) Stop() {
	close(c.stopCh)
	c.closeConnection()
}
