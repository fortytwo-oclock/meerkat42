// Package dockerctl implements tightly scoped Docker Engine update operations.
package dockerctl

import (
	"bufio"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"time"
)

var imagePattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}$`)

type Client struct {
	base    string
	http    *http.Client
	allowed map[string]struct{}
	auth    map[string]json.RawMessage
}

func NewFromEnv() (*Client, error) {
	allowed := make(map[string]struct{})
	rawAllowed := os.Getenv("IMAGE_UPDATE_REGISTRIES")
	if rawAllowed == "" {
		rawAllowed = "docker.io,registry-1.docker.io,ghcr.io,quay.io"
	}
	for _, value := range strings.Split(rawAllowed, ",") {
		if value = strings.ToLower(strings.TrimSpace(value)); value != "" {
			allowed[value] = struct{}{}
		}
	}
	auth, err := loadAuth(os.Getenv("DOCKER_CONFIG_FILE"))
	if err != nil {
		return nil, err
	}
	transport := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", "/var/run/docker.sock")
	}}
	return &Client{
		base: "http://localhost", allowed: allowed, auth: auth,
		http: &http.Client{Transport: transport, Timeout: 15 * time.Minute},
	}, nil
}

func loadAuth(filename string) (map[string]json.RawMessage, error) {
	result := make(map[string]json.RawMessage)
	filename = strings.TrimSpace(filename)
	if filename == "" {
		return result, nil
	}
	body, err := os.ReadFile(filename)
	if err != nil {
		return nil, fmt.Errorf("read DOCKER_CONFIG_FILE: %w", err)
	}
	if len(body) > 1024*1024 {
		return nil, errors.New("DOCKER_CONFIG_FILE exceeds 1 MiB")
	}
	var config struct {
		Auths map[string]json.RawMessage `json:"auths"`
	}
	if err := json.Unmarshal(body, &config); err != nil {
		return nil, errors.New("DOCKER_CONFIG_FILE is not valid JSON")
	}
	for host, value := range config.Auths {
		host = normalizeRegistry(host)
		if host != "" {
			result[host] = value
		}
	}
	return result, nil
}

func normalizeRegistry(value string) string {
	value = strings.TrimSpace(strings.ToLower(value))
	if parsed, err := url.Parse(value); err == nil && parsed.Host != "" {
		value = parsed.Host
	}
	return strings.TrimSuffix(value, "/v1/")
}

func registry(image string) string {
	name := strings.SplitN(image, "@", 2)[0]
	first := strings.SplitN(name, "/", 2)[0]
	if !strings.Contains(name, "/") || (!strings.Contains(first, ".") && !strings.Contains(first, ":") && first != "localhost") {
		return "docker.io"
	}
	return strings.ToLower(first)
}

func (c *Client) registryAuth(image string) string {
	host := registry(image)
	value := c.auth[host]
	if value == nil && host == "docker.io" {
		value = c.auth["index.docker.io"]
	}
	if value == nil {
		value = json.RawMessage(`{}`)
	}
	return base64.RawURLEncoding.EncodeToString(value)
}

// RegistryAuthFromEnv returns Docker Engine's base64url X-Registry-Auth value.
// It is intended for local Engine calls only; callers must never log or transmit it.
func RegistryAuthFromEnv(image string) (string, error) {
	auth, err := loadAuth(os.Getenv("DOCKER_CONFIG_FILE"))
	if err != nil {
		return "", err
	}
	return (&Client{auth: auth}).registryAuth(image), nil
}

func (c *Client) validate(image string) error {
	if !imagePattern.MatchString(image) || strings.Contains(image, "..") || strings.Contains(image, "@") {
		return errors.New("invalid or digest-pinned image reference")
	}
	host := registry(image)
	if _, ok := c.allowed[host]; !ok {
		if _, wildcard := c.allowed["*"]; !wildcard {
			return errors.New("image registry is not allowlisted")
		}
	}
	return nil
}

func (c *Client) imageIsRunning(image string) (bool, error) {
	response, err := c.http.Get(c.base + "/containers/json")
	if err != nil {
		return false, errors.New("Docker Engine is unavailable")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return false, fmt.Errorf("Docker Engine returned HTTP %d", response.StatusCode)
	}
	var containers []struct {
		Image string `json:"Image"`
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 2*1024*1024)).Decode(&containers); err != nil {
		return false, errors.New("invalid Docker Engine response")
	}
	for _, container := range containers {
		if container.Image == image {
			return true, nil
		}
	}
	return false, nil
}

// Pull fetches a newer image but deliberately does not restart or recreate a container.
func (c *Client) Pull(image string) (string, error) {
	if err := c.validate(image); err != nil {
		return "", err
	}
	running, err := c.imageIsRunning(image)
	if err != nil {
		return "", err
	}
	if !running {
		return "", errors.New("image is not used by a running container")
	}
	target := c.base + "/images/create?fromImage=" + url.QueryEscape(image)
	req, _ := http.NewRequest(http.MethodPost, target, nil)
	req.Header.Set("X-Registry-Auth", c.registryAuth(image))
	response, err := c.http.Do(req)
	if err != nil {
		return "", errors.New("Docker image pull failed")
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return "", fmt.Errorf("Docker image pull returned HTTP %d", response.StatusCode)
	}
	scanner := bufio.NewScanner(io.LimitReader(response.Body, 4*1024*1024))
	var lastStatus string
	for scanner.Scan() {
		var event struct{ Status, Error string }
		if json.Unmarshal(scanner.Bytes(), &event) == nil {
			if event.Error != "" {
				return "", errors.New("registry rejected the image pull")
			}
			if event.Status != "" {
				lastStatus = event.Status
			}
		}
	}
	if err := scanner.Err(); err != nil {
		return "", errors.New("Docker image pull response was invalid or too large")
	}
	return lastStatus, nil
}
