package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

func renderServiceDefinition(binPath, hubURL, authToken, deviceClass string) (string, string) {
	switch runtime.GOOS {
	case "darwin":
		plist := fmt.Sprintf(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.meerkat42.agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>%s</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HUB_URL</key>
    <string>%s</string>
    <key>AUTH_TOKEN</key>
    <string>%s</string>
    <key>DEVICE_CLASS</key>
    <string>%s</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/var/log/meerkat42-agent.log</string>
  <key>StandardErrorPath</key>
  <string>/var/log/meerkat42-agent.err.log</string>
</dict>
</plist>
`, binPath, hubURL, authToken, deviceClass)
		return "/Library/LaunchDaemons/com.meerkat42.agent.plist", plist
	case "windows":
		script := fmt.Sprintf(
			`$env:HUB_URL="%s"; $env:AUTH_TOKEN="%s"; $env:DEVICE_CLASS="%s"
# Register as a scheduled startup task (runs under SYSTEM):
$action = New-ScheduledTaskAction -Execute "%s"
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
Register-ScheduledTask -TaskName "Meerkat42Agent" -Action $action -Trigger $trigger -Principal $principal -Force
Start-ScheduledTask -TaskName "Meerkat42Agent"
`, hubURL, authToken, deviceClass, binPath)
		return "Meerkat42Agent (Windows Task/Service)", script
	default:
		unit := fmt.Sprintf(`[Unit]
Description=Meerkat42 Edge Monitoring Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=HUB_URL=%s
Environment=AUTH_TOKEN=%s
Environment=DEVICE_CLASS=%s
ExecStart=%s
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
`, hubURL, authToken, deviceClass, binPath)
		return "/etc/systemd/system/meerkat42-agent.service", unit
	}
}

func runServiceCommand(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("usage: agent service <render|install|uninstall>")
	}
	sub := strings.ToLower(args[0])
	exePath, err := os.Executable()
	if err != nil {
		exePath = "/usr/local/bin/meerkat42-agent"
	} else if resolved, err := filepath.EvalSymlinks(exePath); err == nil {
		exePath = resolved
	}
	hubURL := getEnv("HUB_URL", "wss://hub.example.com/api/v1/agent/stream")
	authToken := getEnv("AUTH_TOKEN", "<your-enrollment-token>")
	deviceClass := getEnv("DEVICE_CLASS", "server")
	if runtime.GOOS == "darwin" || runtime.GOOS == "windows" {
		deviceClass = getEnv("DEVICE_CLASS", "workstation")
	}

	destPath, body := renderServiceDefinition(exePath, hubURL, authToken, deviceClass)

	switch sub {
	case "render":
		fmt.Printf("# Target: %s\n%s", destPath, body)
		return nil
	case "install":
		switch runtime.GOOS {
		case "linux":
			if err := os.WriteFile(destPath, []byte(body), 0o600); err != nil {
				return fmt.Errorf("write %s (run as root): %w", destPath, err)
			}
			_ = exec.Command("systemctl", "daemon-reload").Run()
			if err := exec.Command("systemctl", "enable", "--now", "meerkat42-agent.service").Run(); err != nil {
				return fmt.Errorf("systemctl enable failed: %w", err)
			}
			fmt.Printf("Installed and started %s\n", destPath)
			return nil
		case "darwin":
			if err := os.WriteFile(destPath, []byte(body), 0o600); err != nil {
				return fmt.Errorf("write %s (run with sudo): %w", destPath, err)
			}
			_ = exec.Command("launchctl", "unload", destPath).Run()
			if err := exec.Command("launchctl", "load", "-w", destPath).Run(); err != nil {
				return fmt.Errorf("launchctl load failed: %w", err)
			}
			fmt.Printf("Installed and loaded %s\n", destPath)
			return nil
		default:
			fmt.Printf("Run the following PowerShell snippet as Administrator:\n\n%s\n", body)
			return nil
		}
	case "uninstall":
		switch runtime.GOOS {
		case "linux":
			_ = exec.Command("systemctl", "disable", "--now", "meerkat42-agent.service").Run()
			_ = os.Remove(destPath)
			_ = exec.Command("systemctl", "daemon-reload").Run()
			fmt.Printf("Removed %s\n", destPath)
			return nil
		case "darwin":
			_ = exec.Command("launchctl", "unload", "-w", destPath).Run()
			_ = os.Remove(destPath)
			fmt.Printf("Removed %s\n", destPath)
			return nil
		default:
			fmt.Println(`Run as Administrator: Unregister-ScheduledTask -TaskName "Meerkat42Agent" -Confirm:$false`)
			return nil
		}
	default:
		return fmt.Errorf("unknown service subcommand %q (expected render, install, uninstall)", sub)
	}
}
