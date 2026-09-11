package main

import (
	"context"
	"fmt"
	"log"
	"math"
	"os"
	"os/signal"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"

	"control-plane/pkg/diagnostics"
	"control-plane/pkg/dockerctl"
	"control-plane/pkg/proxmox"
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
func buildCommandHandler(proxmoxClient *proxmox.Client, dockerClient *dockerctl.Client) tunnel.CommandHandler {
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
		case "proxmox_start", "proxmox_shutdown", "proxmox_reboot":
			if proxmoxClient == nil {
				return nil, fmt.Errorf("Proxmox integration is not configured on this agent")
			}
			node, nodeOK := payload["node"].(string)
			guestType, typeOK := payload["guest_type"].(string)
			vmidRaw, vmidOK := payload["vmid"].(float64)
			if !nodeOK || !typeOK || !vmidOK || math.IsNaN(vmidRaw) || math.IsInf(vmidRaw, 0) ||
				vmidRaw < 100 || vmidRaw > 999999999 || vmidRaw != math.Trunc(vmidRaw) {
				return nil, fmt.Errorf("node, guest_type, and integer vmid are required")
			}
			op := strings.TrimPrefix(action, "proxmox_")
			upid, err := proxmoxClient.GuestAction(node, guestType, int(vmidRaw), op)
			if err != nil {
				return nil, err
			}
			return map[string]string{"task_id": upid}, nil
		case "docker_pull_image":
			if dockerClient == nil {
				return nil, fmt.Errorf("Docker update management is not configured on this agent")
			}
			image, ok := payload["image"].(string)
			if !ok {
				return nil, fmt.Errorf("image is required")
			}
			status, err := dockerClient.Pull(image)
			if err != nil {
				return nil, err
			}
			return map[string]string{"image": image, "status": status}, nil
		case "docker_restart_container", "docker_stop_container", "docker_update_container":
			if dockerClient == nil {
				return nil, fmt.Errorf("Docker remote management is not configured on this agent")
			}
			container, ok := payload["container"].(string)
			if !ok {
				return nil, fmt.Errorf("container is required")
			}
			var status string
			var err error
			if action == "docker_restart_container" {
				status, err = dockerClient.Restart(container)
			} else if action == "docker_update_container" {
				status, err = dockerClient.UpdateAndRecreate(container)
			} else {
				status, err = dockerClient.Stop(container)
			}
			if err != nil {
				return nil, err
			}
			return map[string]string{"container": container, "status": status}, nil
		case "diagnostic_run":
			if len(payload) != 1 {
				return nil, fmt.Errorf("diagnostic payload has unexpected fields")
			}
			name, ok := payload["diagnostic"].(string)
			if !ok {
				return nil, fmt.Errorf("diagnostic is required")
			}
			output, err := diagnostics.Run(name)
			if err != nil {
				return nil, err
			}
			return map[string]string{"diagnostic": name, "output": output}, nil
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

	proxmoxClient, err := proxmox.NewFromEnv()
	if err != nil {
		log.Printf("[Proxmox] Remote actions disabled: %v", err)
	}
	collector := telemetry.NewCollector(proxmoxClient)
	dockerClient, err := dockerctl.NewFromEnv()
	if err != nil {
		log.Printf("[Docker] Update management disabled: %v", err)
	}

	cfg := tunnel.Config{
		HubURL:         hubURL,
		NodeID:         nodeID,
		Hostname:       collector.Hostname(),
		Arch:           runtime.GOARCH,
		AuthToken:      authToken,
		ClientGroup:    clientGroup,
		PushInterval:   time.Duration(intervalSec) * time.Second,
		CommandHandler: buildCommandHandler(proxmoxClient, dockerClient),
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
