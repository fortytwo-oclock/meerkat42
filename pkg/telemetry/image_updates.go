package telemetry

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

type imageUpdateResult struct {
	localDigest  string
	remoteDigest string
	available    bool
	status       string
	checkedAt    *time.Time
}

type imageUpdateCacheEntry struct {
	remoteDigest string
	status       string
	checkedAt    time.Time
	nextCheck    time.Time
}

type imageUpdateChecker struct {
	enabled   bool
	interval  time.Duration
	allowed   map[string]struct{}
	dockerURL string
	now       func() time.Time
	mu        sync.Mutex
	cache     map[string]imageUpdateCacheEntry
}

func newImageUpdateCheckerFromEnv() *imageUpdateChecker {
	enabled, _ := strconv.ParseBool(os.Getenv("IMAGE_UPDATE_CHECK_ENABLED"))
	interval := 6 * time.Hour
	if raw := os.Getenv("IMAGE_UPDATE_CHECK_INTERVAL_SEC"); raw != "" {
		if seconds, err := strconv.Atoi(raw); err == nil && seconds >= 300 {
			interval = time.Duration(seconds) * time.Second
		}
	}
	registries := os.Getenv("IMAGE_UPDATE_REGISTRIES")
	if registries == "" {
		registries = "docker.io,registry-1.docker.io,ghcr.io,quay.io"
	}
	allowed := make(map[string]struct{})
	for _, registry := range strings.Split(registries, ",") {
		if registry = strings.ToLower(strings.TrimSpace(registry)); registry != "" {
			allowed[registry] = struct{}{}
		}
	}
	return &imageUpdateChecker{
		enabled: enabled, interval: interval, allowed: allowed,
		dockerURL: "http://localhost", now: time.Now,
		cache: make(map[string]imageUpdateCacheEntry),
	}
}

func imageRegistry(reference string) string {
	name := strings.SplitN(reference, "@", 2)[0]
	lastSlash := strings.LastIndex(name, "/")
	first := name
	if slash := strings.Index(name, "/"); slash >= 0 {
		first = name[:slash]
	}
	if lastSlash < 0 || (!strings.Contains(first, ".") && !strings.Contains(first, ":") && first != "localhost") {
		return "docker.io"
	}
	return strings.ToLower(first)
}

func digestPart(value string) string {
	if at := strings.LastIndex(value, "@"); at >= 0 {
		return value[at+1:]
	}
	if strings.HasPrefix(value, "sha256:") {
		return value
	}
	return ""
}

func (c *imageUpdateChecker) check(
	client *http.Client,
	image string,
	imageID string,
	manifestDigest string,
	allowNetwork bool,
) (imageUpdateResult, bool) {
	result := imageUpdateResult{localDigest: digestPart(manifestDigest)}
	if !c.enabled {
		result.status = "DISABLED"
		return result, false
	}
	if pinned := digestPart(image); pinned != "" {
		result.localDigest = pinned
		result.remoteDigest = pinned
		result.status = "PINNED"
		return result, false
	}
	registry := imageRegistry(image)
	if _, ok := c.allowed[registry]; !ok {
		if _, wildcard := c.allowed["*"]; !wildcard {
			result.status = "REGISTRY_NOT_ALLOWED"
			return result, false
		}
	}

	now := c.now().UTC()
	c.mu.Lock()
	cached, exists := c.cache[image]
	c.mu.Unlock()
	if exists && now.Before(cached.nextCheck) {
		return compareImageDigests(result, cached), false
	}
	if !allowNetwork {
		result.status = "QUEUED"
		return result, false
	}
	if result.localDigest == "" {
		result.localDigest = c.inspectLocalDigest(client, imageID)
	}
	remote, err := c.inspectDistribution(client, image)
	entry := imageUpdateCacheEntry{checkedAt: now, nextCheck: now.Add(c.interval)}
	if err != nil {
		entry.status = "UNAVAILABLE"
		retry := 15 * time.Minute
		if c.interval < retry {
			retry = c.interval
		}
		entry.nextCheck = now.Add(retry)
	} else {
		entry.status = "CURRENT"
		entry.remoteDigest = remote
	}
	c.mu.Lock()
	c.cache[image] = entry
	c.mu.Unlock()
	return compareImageDigests(result, entry), true
}

func compareImageDigests(result imageUpdateResult, entry imageUpdateCacheEntry) imageUpdateResult {
	result.remoteDigest = entry.remoteDigest
	if !entry.checkedAt.IsZero() {
		checkedAt := entry.checkedAt
		result.checkedAt = &checkedAt
	}
	result.status = entry.status
	if entry.remoteDigest != "" && result.localDigest != "" && entry.remoteDigest != result.localDigest {
		result.available = true
		result.status = "UPDATE_AVAILABLE"
	}
	if entry.remoteDigest != "" && result.localDigest == "" {
		result.status = "LOCAL_DIGEST_UNAVAILABLE"
	}
	return result
}

func (c *imageUpdateChecker) inspectLocalDigest(client *http.Client, imageID string) string {
	if imageID == "" {
		return ""
	}
	endpoint := c.dockerURL + "/images/" + url.PathEscape(imageID) + "/json"
	response, err := client.Get(endpoint)
	if err != nil {
		return ""
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return ""
	}
	var body struct {
		RepoDigests []string `json:"RepoDigests"`
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&body); err != nil {
		return ""
	}
	if len(body.RepoDigests) == 0 {
		return ""
	}
	return digestPart(body.RepoDigests[0])
}

func (c *imageUpdateChecker) inspectDistribution(client *http.Client, image string) (string, error) {
	endpoint := c.dockerURL + "/distribution/" + url.PathEscape(image) + "/json"
	response, err := client.Get(endpoint)
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return "", fmt.Errorf("Docker distribution inspection returned %d", response.StatusCode)
	}
	var body struct {
		Descriptor struct {
			Digest string `json:"digest"`
		} `json:"Descriptor"`
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&body); err != nil {
		return "", err
	}
	digest := digestPart(body.Descriptor.Digest)
	if digest == "" {
		return "", fmt.Errorf("Docker distribution response has no digest")
	}
	return digest, nil
}
