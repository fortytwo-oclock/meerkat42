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
var containerPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`)

type Client struct {
	base              string
	http              *http.Client
	allowed           map[string]struct{}
	allowedContainers map[string]struct{}
	auth              map[string]json.RawMessage
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
	allowedContainers := make(map[string]struct{})
	for _, value := range strings.Split(os.Getenv("DOCKER_ACTION_CONTAINERS"), ",") {
		if value = strings.TrimSpace(value); value != "" {
			allowedContainers[value] = struct{}{}
		}
	}
	transport := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", "/var/run/docker.sock")
	}}
	return &Client{
		base: "http://localhost", allowed: allowed, allowedContainers: allowedContainers, auth: auth,
		http: &http.Client{Transport: transport, Timeout: 15 * time.Minute},
	}, nil
}

func (c *Client) validateContainer(name string) error {
	if !containerPattern.MatchString(name) {
		return errors.New("invalid container name")
	}
	if _, ok := c.allowedContainers[name]; !ok {
		return errors.New("container is not allowlisted for remote actions")
	}
	return nil
}

func (c *Client) containerAction(name, action string, timeoutSeconds int) (string, error) {
	if err := c.validateContainer(name); err != nil {
		return "", err
	}
	target := fmt.Sprintf("%s/containers/%s/%s?t=%d", c.base, url.PathEscape(name), action, timeoutSeconds)
	req, _ := http.NewRequest(http.MethodPost, target, nil)
	response, err := c.http.Do(req)
	if err != nil {
		return "", errors.New("Docker Engine is unavailable")
	}
	defer response.Body.Close()
	if response.StatusCode == http.StatusNotModified && action == "stop" {
		return "already stopped", nil
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return "", fmt.Errorf("Docker container %s returned HTTP %d", action, response.StatusCode)
	}
	return action + " requested", nil
}

// Restart restarts one explicitly allowlisted container with a bounded grace period.
func (c *Client) Restart(name string) (string, error) {
	return c.containerAction(name, "restart", 10)
}

// Stop gracefully stops one explicitly allowlisted container.
func (c *Client) Stop(name string) (string, error) {
	return c.containerAction(name, "stop", 30)
}

type inspectedContainer struct {
	ID         string                 `json:"Id"`
	Name       string                 `json:"Name"`
	Config     map[string]interface{} `json:"Config"`
	HostConfig map[string]interface{} `json:"HostConfig"`
	State      struct {
		Running bool `json:"Running"`
		Health  *struct {
			Status string `json:"Status"`
		} `json:"Health"`
	} `json:"State"`
	NetworkSettings struct {
		Networks map[string]struct {
			IPAMConfig map[string]interface{} `json:"IPAMConfig"`
			Links      []string               `json:"Links"`
			Aliases    []string               `json:"Aliases"`
			DriverOpts map[string]string      `json:"DriverOpts"`
			MacAddress string                 `json:"MacAddress"`
		} `json:"Networks"`
	} `json:"NetworkSettings"`
}

func (c *Client) waitForReplacement(id string, hasHealthcheck bool) error {
	deadline := time.Now().Add(60 * time.Second)
	stableChecks := 0
	for time.Now().Before(deadline) {
		response, err := c.engineRequest(http.MethodGet, "/containers/"+url.PathEscape(id)+"/json", nil)
		if err != nil {
			return err
		}
		if response.StatusCode != http.StatusOK {
			response.Body.Close()
			return fmt.Errorf("replacement inspection returned HTTP %d", response.StatusCode)
		}
		var replacement inspectedContainer
		decodeErr := json.NewDecoder(io.LimitReader(response.Body, 4*1024*1024)).Decode(&replacement)
		response.Body.Close()
		if decodeErr != nil {
			return errors.New("replacement inspection was invalid")
		}
		if !replacement.State.Running {
			return errors.New("replacement container exited during startup")
		}
		if hasHealthcheck {
			if replacement.State.Health != nil && replacement.State.Health.Status == "healthy" {
				return nil
			}
			if replacement.State.Health != nil && replacement.State.Health.Status == "unhealthy" {
				return errors.New("replacement container became unhealthy")
			}
		} else {
			stableChecks++
			if stableChecks >= 3 {
				return nil
			}
		}
		time.Sleep(time.Second)
	}
	return errors.New("replacement container did not become ready before timeout")
}

func (c *Client) engineRequest(method, target string, body io.Reader) (*http.Response, error) {
	req, err := http.NewRequest(method, c.base+target, body)
	if err != nil {
		return nil, err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	response, err := c.http.Do(req)
	if err != nil {
		return nil, errors.New("Docker Engine is unavailable")
	}
	return response, nil
}

func (c *Client) simpleEngineAction(method, target string, accepted ...int) error {
	response, err := c.engineRequest(method, target, nil)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	for _, status := range accepted {
		if response.StatusCode == status {
			return nil
		}
	}
	return fmt.Errorf("Docker Engine returned HTTP %d", response.StatusCode)
}

// UpdateAndRecreate pulls the configured image tag and replaces one running,
// allowlisted container while preserving its Docker configuration. The stopped
// original is retained until the replacement starts, then removed. Any create
// or start failure attempts to restore and restart the original container.
func (c *Client) UpdateAndRecreate(name string) (string, error) {
	if err := c.validateContainer(name); err != nil {
		return "", err
	}

	response, err := c.engineRequest(http.MethodGet, "/containers/"+url.PathEscape(name)+"/json", nil)
	if err != nil {
		return "", err
	}
	if response.StatusCode != http.StatusOK {
		response.Body.Close()
		return "", fmt.Errorf("Docker container inspect returned HTTP %d", response.StatusCode)
	}
	var current inspectedContainer
	err = json.NewDecoder(io.LimitReader(response.Body, 4*1024*1024)).Decode(&current)
	response.Body.Close()
	if err != nil || current.ID == "" || current.Config == nil || current.HostConfig == nil {
		return "", errors.New("Docker container inspection was invalid")
	}
	if !current.State.Running {
		return "", errors.New("container must be running before it can be updated")
	}
	image, ok := current.Config["Image"].(string)
	if !ok || image == "" {
		return "", errors.New("container has no updateable image reference")
	}
	if _, err := c.Pull(image); err != nil {
		return "", err
	}

	if _, err := c.Stop(name); err != nil {
		return "", err
	}
	rollbackName := fmt.Sprintf("%s.meerkat42-rollback-%d", name, time.Now().Unix())
	if err := c.simpleEngineAction(
		http.MethodPost,
		"/containers/"+url.PathEscape(current.ID)+"/rename?name="+url.QueryEscape(rollbackName),
		http.StatusNoContent,
	); err != nil {
		if startErr := c.simpleEngineAction(http.MethodPost, "/containers/"+url.PathEscape(current.ID)+"/start", http.StatusNoContent, http.StatusNotModified); startErr != nil {
			return "", fmt.Errorf("could not reserve the original container for rollback and could not restart it: %v", startErr)
		}
		return "", errors.New("could not reserve the original container for rollback")
	}

	rollback := func(newID string) error {
		var failures []string
		if newID != "" {
			if err := c.simpleEngineAction(http.MethodDelete, "/containers/"+url.PathEscape(newID)+"?force=1&v=0", http.StatusNoContent, http.StatusNotFound); err != nil {
				failures = append(failures, "remove replacement: "+err.Error())
			}
		}
		if err := c.simpleEngineAction(
			http.MethodPost,
			"/containers/"+url.PathEscape(current.ID)+"/rename?name="+url.QueryEscape(name),
			http.StatusNoContent,
		); err != nil {
			failures = append(failures, "restore original name: "+err.Error())
		}
		if err := c.simpleEngineAction(http.MethodPost, "/containers/"+url.PathEscape(current.ID)+"/start", http.StatusNoContent, http.StatusNotModified); err != nil {
			failures = append(failures, "restart original: "+err.Error())
		}
		if len(failures) > 0 {
			return errors.New(strings.Join(failures, "; "))
		}
		return nil
	}
	restoreError := func(message string, newID string) error {
		if rollbackErr := rollback(newID); rollbackErr != nil {
			return fmt.Errorf("%s; automatic rollback failed: %v", message, rollbackErr)
		}
		return errors.New(message + "; original restored")
	}

	endpoints := make(map[string]interface{}, len(current.NetworkSettings.Networks))
	for networkName, settings := range current.NetworkSettings.Networks {
		endpoint := map[string]interface{}{
			"IPAMConfig": settings.IPAMConfig,
			"Links":      settings.Links,
			"Aliases":    settings.Aliases,
			"DriverOpts": settings.DriverOpts,
		}
		if settings.MacAddress != "" {
			endpoint["MacAddress"] = settings.MacAddress
		}
		endpoints[networkName] = endpoint
	}
	createBody := make(map[string]interface{}, len(current.Config)+2)
	for key, value := range current.Config {
		createBody[key] = value
	}
	createBody["HostConfig"] = current.HostConfig
	createBody["NetworkingConfig"] = map[string]interface{}{"EndpointsConfig": endpoints}
	body, err := json.Marshal(createBody)
	if err != nil {
		return "", restoreError("could not prepare replacement container", "")
	}
	response, err = c.engineRequest(
		http.MethodPost,
		"/containers/create?name="+url.QueryEscape(name),
		strings.NewReader(string(body)),
	)
	if err != nil {
		return "", restoreError(err.Error(), "")
	}
	var created struct {
		ID string `json:"Id"`
	}
	decodeErr := json.NewDecoder(io.LimitReader(response.Body, 1024*1024)).Decode(&created)
	createStatus := response.StatusCode
	response.Body.Close()
	if createStatus < 200 || createStatus >= 300 || decodeErr != nil || created.ID == "" {
		return "", restoreError(fmt.Sprintf("replacement container creation failed with HTTP %d", createStatus), created.ID)
	}
	if err := c.simpleEngineAction(http.MethodPost, "/containers/"+url.PathEscape(created.ID)+"/start", http.StatusNoContent, http.StatusNotModified); err != nil {
		return "", restoreError("replacement container did not start", created.ID)
	}
	hasHealthcheck := false
	if rawHealthcheck, ok := current.Config["Healthcheck"].(map[string]interface{}); ok {
		if test, ok := rawHealthcheck["Test"].([]interface{}); ok && len(test) > 0 {
			hasHealthcheck = test[0] != "NONE"
		}
	}
	if err := c.waitForReplacement(created.ID, hasHealthcheck); err != nil {
		return "", restoreError(err.Error(), created.ID)
	}
	if err := c.simpleEngineAction(http.MethodDelete, "/containers/"+url.PathEscape(current.ID)+"?force=1&v=0", http.StatusNoContent, http.StatusNotFound); err != nil {
		return "updated and recreated; stopped rollback container retained as " + rollbackName, nil
	}
	return "updated and recreated", nil
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
