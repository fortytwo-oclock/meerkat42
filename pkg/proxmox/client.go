// Package proxmox provides a narrowly scoped Proxmox VE API client.
// Credentials and trust roots stay on the edge agent and are never sent to the Hub.
package proxmox

import (
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

const maxResponseBytes = 2 * 1024 * 1024

type Guest struct {
	VMID      int     `json:"vmid"`
	Node      string  `json:"node"`
	Name      string  `json:"name"`
	Type      string  `json:"type"`
	Status    string  `json:"status"`
	CPU       float64 `json:"cpu"`
	Memory    uint64  `json:"mem"`
	MaxMemory uint64  `json:"maxmem"`
	Uptime    uint64  `json:"uptime"`
}

type Client struct {
	base    *url.URL
	tokenID string
	secret  string
	allowed map[int]struct{}
	http    *http.Client
}

func NewFromEnv() (*Client, error) {
	raw := strings.TrimSpace(os.Getenv("PROXMOX_API_URL"))
	if raw == "" {
		return nil, nil
	}
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil {
		return nil, errors.New("PROXMOX_API_URL must be an https URL without credentials")
	}
	parsed.Path = strings.TrimSuffix(parsed.Path, "/")
	tokenID := strings.TrimSpace(os.Getenv("PROXMOX_TOKEN_ID"))
	secret := strings.TrimSpace(os.Getenv("PROXMOX_TOKEN_SECRET"))
	if tokenID == "" || secret == "" {
		return nil, errors.New("PROXMOX_TOKEN_ID and PROXMOX_TOKEN_SECRET are required")
	}
	allowed, err := parseAllowlist(os.Getenv("PROXMOX_GUEST_ALLOWLIST"))
	if err != nil {
		return nil, err
	}
	pool, err := x509.SystemCertPool()
	if err != nil || pool == nil {
		pool = x509.NewCertPool()
	}
	if caFile := strings.TrimSpace(os.Getenv("PROXMOX_CA_FILE")); caFile != "" {
		pem, readErr := os.ReadFile(caFile)
		if readErr != nil {
			return nil, fmt.Errorf("read PROXMOX_CA_FILE: %w", readErr)
		}
		if !pool.AppendCertsFromPEM(pem) {
			return nil, errors.New("PROXMOX_CA_FILE contains no certificates")
		}
	}
	transport := &http.Transport{TLSClientConfig: &tls.Config{
		MinVersion: tls.VersionTLS12, RootCAs: pool,
	}}
	return &Client{
		base: parsed, tokenID: tokenID, secret: secret, allowed: allowed,
		http: &http.Client{Transport: transport, Timeout: 10 * time.Second},
	}, nil
}

func parseAllowlist(raw string) (map[int]struct{}, error) {
	result := make(map[int]struct{})
	for _, item := range strings.Split(raw, ",") {
		item = strings.TrimSpace(item)
		if item == "" {
			continue
		}
		vmid, err := strconv.Atoi(item)
		if err != nil || vmid < 100 || vmid > 999999999 {
			return nil, fmt.Errorf("invalid PROXMOX_GUEST_ALLOWLIST entry %q", item)
		}
		result[vmid] = struct{}{}
	}
	return result, nil
}

func (c *Client) request(method, endpoint string) ([]byte, error) {
	reference, err := url.Parse(endpoint)
	if err != nil {
		return nil, err
	}
	target := c.base.ResolveReference(reference)
	req, err := http.NewRequest(method, target.String(), nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "PVEAPIToken="+c.tokenID+"="+c.secret)
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("Proxmox API request failed: %w", err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxResponseBytes+1))
	if err != nil {
		return nil, errors.New("could not read Proxmox API response")
	}
	if len(body) > maxResponseBytes {
		return nil, errors.New("Proxmox API response exceeded size limit")
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("Proxmox API returned HTTP %d", resp.StatusCode)
	}
	return body, nil
}

func (c *Client) Guests() ([]Guest, error) {
	body, err := c.request(http.MethodGet, "/api2/json/cluster/resources?type=vm")
	if err != nil {
		return nil, err
	}
	var envelope struct {
		Data []Guest `json:"data"`
	}
	if err := json.Unmarshal(body, &envelope); err != nil {
		return nil, errors.New("invalid Proxmox API response")
	}
	return envelope.Data, nil
}

func (c *Client) GuestAction(node, guestType string, vmid int, action string) (string, error) {
	if _, ok := c.allowed[vmid]; !ok {
		return "", errors.New("guest is not in PROXMOX_GUEST_ALLOWLIST")
	}
	if guestType != "qemu" && guestType != "lxc" {
		return "", errors.New("guest_type must be qemu or lxc")
	}
	if action != "start" && action != "shutdown" && action != "reboot" {
		return "", errors.New("unsupported Proxmox guest action")
	}
	found := false
	guests, err := c.Guests()
	if err != nil {
		return "", err
	}
	for _, guest := range guests {
		if guest.VMID == vmid && guest.Node == node && guest.Type == guestType {
			found = true
			break
		}
	}
	if !found {
		return "", errors.New("guest identity does not match Proxmox inventory")
	}
	body, err := c.request(http.MethodPost, fmt.Sprintf(
		"/api2/json/nodes/%s/%s/%d/status/%s", url.PathEscape(node), guestType, vmid, action,
	))
	if err != nil {
		return "", err
	}
	var envelope struct {
		Data string `json:"data"`
	}
	if err := json.Unmarshal(body, &envelope); err != nil || envelope.Data == "" {
		return "", errors.New("Proxmox API did not return a task ID")
	}
	return envelope.Data, nil
}
