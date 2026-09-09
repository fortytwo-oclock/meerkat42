package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"control-plane/pkg/telemetry"
	"control-plane/pkg/tunnel"
	"control-plane/pkg/vault"
)

func getEnv(key, defaultVal string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return defaultVal
}

// buildCommandHandler wires the downstream HubCommand actions the agent supports.
func buildCommandHandler() tunnel.CommandHandler {
	return func(action string, payload map[string]interface{}) (interface{}, error) {
		switch action {
		case "request_config_backup":
			keyB64 := os.Getenv("VAULT_KEY")
			if keyB64 == "" {
				return nil, fmt.Errorf("VAULT_KEY not configured on this agent")
			}
			roots := vault.SplitPaths(getEnv("VAULT_PATHS", "/host/compose"))
			maxBytes := 5 * 1024 * 1024
			if v := os.Getenv("VAULT_MAX_BYTES"); v != "" {
				if n, err := strconv.Atoi(v); err == nil && n > 0 {
					maxBytes = n
				}
			}
			log.Printf("[Vault] Config backup requested; scanning %v", roots)
			res, err := vault.Backup(roots, keyB64, maxBytes)
			if err != nil {
				return nil, err
			}
			log.Printf("[Vault] Encrypted %d file(s) for hub storage", len(res.Files))
			return res, nil
		case "ping":
			return map[string]string{"pong": "ok"}, nil
		default:
			return nil, fmt.Errorf("unsupported action %q", action)
		}
	}
}

func main() {
	log.Println("=== Starting Distributed Control Plane Edge Agent (v1.0.0-open-core) ===")

	hubURL := getEnv("HUB_URL", "ws://localhost:8080/api/v1/agent/stream")
	nodeID := getEnv("NODE_ID", "")
	if nodeID == "" {
		host, err := os.Hostname()
		if err != nil {
			nodeID = "node-unknown"
		} else {
			nodeID = host
		}
	}
	authToken := getEnv("AUTH_TOKEN", "secret-agent-token-demo")
	clientGroup := getEnv("CLIENT_GROUP", "default")

	intervalSec := 10
	if secStr := os.Getenv("PUSH_INTERVAL_SEC"); secStr != "" {
		if s, err := strconv.Atoi(secStr); err == nil && s > 0 {
			intervalSec = s
		}
	}

	log.Printf("[Init] Node ID:      %s", nodeID)
	log.Printf("[Init] Target Hub:   %s", hubURL)
	log.Printf("[Init] Client Group: %s", clientGroup)
	log.Printf("[Init] Push Cadence: Every %d seconds", intervalSec)

	collector := telemetry.NewCollector()

	cfg := tunnel.Config{
		HubURL:         hubURL,
		NodeID:         nodeID,
		AuthToken:      authToken,
		ClientGroup:    clientGroup,
		PushInterval:   time.Duration(intervalSec) * time.Second,
		CommandHandler: buildCommandHandler(),
	}

	client := tunnel.NewClient(cfg, collector)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		<-sigCh
		log.Println("[Agent] Interrupt received, graceful shutdown initiated...")
		cancel()
		client.Stop()
	}()

	client.Start(ctx)
	log.Println("[Agent] Process exited cleanly.")
}
