package telemetry

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

func testImageChecker(serverURL string) *imageUpdateChecker {
	return &imageUpdateChecker{
		enabled:   true,
		interval:  6 * time.Hour,
		allowed:   map[string]struct{}{"docker.io": {}},
		dockerURL: serverURL,
		now:       func() time.Time { return time.Unix(1_800_000_000, 0) },
		cache:     make(map[string]imageUpdateCacheEntry),
	}
}

func TestImageUpdateDetectsAndCachesNewDigest(t *testing.T) {
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"Descriptor":{"digest":"sha256:new"}}`)
	}))
	defer server.Close()
	checker := testImageChecker(server.URL)

	result, checked := checker.check(
		server.Client(), "nginx:latest", "sha256:image", "sha256:old", true,
	)
	if !checked || !result.available || result.status != "UPDATE_AVAILABLE" {
		t.Fatalf("expected available update, got checked=%v result=%+v", checked, result)
	}
	if result.remoteDigest != "sha256:new" || requests.Load() != 1 {
		t.Fatalf("unexpected digest/request count: %+v requests=%d", result, requests.Load())
	}

	cached, checked := checker.check(
		server.Client(), "nginx:latest", "sha256:image", "sha256:new", true,
	)
	if checked || cached.available || cached.status != "CURRENT" || requests.Load() != 1 {
		t.Fatalf("expected cached current result, got checked=%v result=%+v", checked, cached)
	}
}

func TestImageUpdatePinnedAndAllowlist(t *testing.T) {
	checker := testImageChecker("http://unused")
	pinned, checked := checker.check(
		http.DefaultClient, "nginx@sha256:fixed", "", "", true,
	)
	if checked || pinned.status != "PINNED" || pinned.available {
		t.Fatalf("unexpected pinned result: checked=%v result=%+v", checked, pinned)
	}
	blocked, checked := checker.check(
		http.DefaultClient, "registry.internal.example/app:latest", "", "", true,
	)
	if checked || blocked.status != "REGISTRY_NOT_ALLOWED" {
		t.Fatalf("unexpected blocked result: checked=%v result=%+v", checked, blocked)
	}
}

func TestImageRegistryParsing(t *testing.T) {
	cases := map[string]string{
		"nginx:latest":                 "docker.io",
		"library/ubuntu:24.04":         "docker.io",
		"ghcr.io/acme/app:v1":          "ghcr.io",
		"registry.example:5000/app:v1": "registry.example:5000",
	}
	for reference, expected := range cases {
		if actual := imageRegistry(reference); actual != expected {
			t.Errorf("imageRegistry(%q)=%q, want %q", reference, actual, expected)
		}
	}
}
