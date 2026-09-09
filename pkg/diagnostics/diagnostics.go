// Package diagnostics exposes fixed, bounded, read-only break-glass checks.
package diagnostics

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os/exec"
	"strings"
	"time"
)

const maxOutput = 48 * 1024

var commands = map[string][]string{
	"uptime":          {"uptime"},
	"disk_usage":      {"df", "-hP"},
	"memory":          {"free", "-m"},
	"network":         {"ip", "-brief", "address"},
	"failed_services": {"systemctl", "--failed", "--no-pager", "--plain"},
	"recent_errors":   {"journalctl", "-p", "err", "-n", "100", "--no-pager"},
}

type limitedBuffer struct {
	buffer bytes.Buffer
	limit  int
}

func (w *limitedBuffer) Write(value []byte) (int, error) {
	remaining := w.limit - w.buffer.Len()
	if remaining <= 0 {
		return 0, errors.New("diagnostic output limit exceeded")
	}
	if len(value) > remaining {
		w.buffer.Write(value[:remaining])
		return remaining, errors.New("diagnostic output limit exceeded")
	}
	return w.buffer.Write(value)
}

func Run(name string) (string, error) {
	spec, ok := commands[name]
	if !ok {
		return "", errors.New("diagnostic is not allowlisted")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, spec[0], spec[1:]...)
	command.Env = []string{"LANG=C", "LC_ALL=C", "PATH=/usr/sbin:/usr/bin:/sbin:/bin"}
	command.Dir = "/"
	output := &limitedBuffer{limit: maxOutput}
	command.Stdout, command.Stderr = output, output
	if err := command.Run(); err != nil {
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return "", errors.New("diagnostic timed out")
		}
		return "", fmt.Errorf("diagnostic failed: %w", err)
	}
	return strings.TrimSpace(output.buffer.String()), nil
}
