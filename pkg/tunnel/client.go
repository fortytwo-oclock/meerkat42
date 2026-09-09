package tunnel

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"math/rand"
	"net/http"
	"net/url"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"control-plane/pkg/telemetry"
)

// Config holds connection parameters for the outbound tunnel.
type Config struct {
	HubURL       string        // e.g. "wss://control.example.com/api/v1/agent/stream" or "ws://localhost:8080/api/v1/agent/stream"
	NodeID       string
	AuthToken    string
	ClientGroup  string
	PushInterval time.Duration // default: 10s
}

// Client manages the persistent outbound WebSocket to the Control Plane Hub.
type Client struct {
	cfg       Config
	collector *telemetry.Collector
	conn      *websocket.Conn
	mu        sync.Mutex
	stopCh    chan struct{}
}

// NewClient creates a new outbound tunnel client.
func NewClient(cfg Config, col *telemetry.Collector) *Client {
	if cfg.PushInterval <= 0 {
		cfg.PushInterval = 10 * time.Second
	}
	return &Client{
		cfg:       cfg,
		collector: col,
		stopCh:    make(chan struct{}),
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
		AuthToken:   c.cfg.AuthToken,
		AgentVer:    "1.0.0-open-core",
		ClientGroup: c.cfg.ClientGroup,
	}
	if err := conn.WriteJSON(handshake); err != nil {
		c.closeConnection()
		return fmt.Errorf("send handshake: %w", err)
	}

	ticker := time.NewTicker(c.cfg.PushInterval)
	defer ticker.Stop()

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
				c.closeConnection()
				return fmt.Errorf("telemetry write failed: %w", err)
			}
			log.Printf("[Tunnel] Pushed telemetry: Host Load=%.2f, RAM=%.1f%%, Containers=%d, ZFS Pools=%d",
				data.HostMetrics.Load1, data.HostMetrics.RAMUsagePct, len(data.Containers), len(data.ZFS))
		}
	}
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
