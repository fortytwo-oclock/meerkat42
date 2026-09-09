package dockerctl

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestPullRequiresRunningExactImageAndSendsLocalAuth(t *testing.T) {
	requests := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		if r.URL.Path == "/containers/json" {
			w.Write([]byte(`[{"Image":"registry.example/app:v2"}]`))
			return
		}
		if r.URL.Path != "/images/create" || r.URL.Query().Get("fromImage") != "registry.example/app:v2" {
			t.Fatalf("unexpected request: %s", r.URL.String())
		}
		auth, err := base64.RawURLEncoding.DecodeString(r.Header.Get("X-Registry-Auth"))
		if err != nil || string(auth) != `{"auth":"local-secret"}` {
			t.Fatalf("unexpected auth header %q: %v", string(auth), err)
		}
		w.Write([]byte("{\"status\":\"Downloaded newer image\"}\n"))
	}))
	defer server.Close()
	client := &Client{
		base: server.URL, http: server.Client(),
		allowed: map[string]struct{}{"registry.example": {}},
		auth:    map[string]json.RawMessage{"registry.example": json.RawMessage(`{"auth":"local-secret"}`)},
	}
	status, err := client.Pull("registry.example/app:v2")
	if err != nil || status != "Downloaded newer image" || requests != 2 {
		t.Fatalf("pull failed: status=%q requests=%d err=%v", status, requests, err)
	}
}

func TestPullRejectsUnlistedAndPinnedImagesBeforeNetwork(t *testing.T) {
	client := &Client{allowed: map[string]struct{}{"docker.io": {}}}
	for _, image := range []string{"evil.example/app:latest", "nginx@sha256:abc", "../bad"} {
		if _, err := client.Pull(image); err == nil {
			t.Fatalf("expected %q to be rejected", image)
		}
	}
}
