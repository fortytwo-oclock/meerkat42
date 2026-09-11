package dockerctl

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
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

func TestContainerActionsRequireExactAllowlist(t *testing.T) {
	requests := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		if r.Method != http.MethodPost || r.URL.Path != "/containers/api-1/restart" || r.URL.Query().Get("t") != "10" {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.String())
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()
	client := &Client{
		base: server.URL, http: server.Client(),
		allowedContainers: map[string]struct{}{"api-1": {}},
	}
	if _, err := client.Restart("api-1"); err != nil || requests != 1 {
		t.Fatalf("restart failed: requests=%d err=%v", requests, err)
	}
	if _, err := client.Stop("db-1"); err == nil || requests != 1 {
		t.Fatalf("unlisted container reached Docker: requests=%d err=%v", requests, err)
	}
}

func TestContainerActionsAllowExplicitWildcard(t *testing.T) {
	requests := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()
	client := &Client{
		base: server.URL, http: server.Client(),
		allowedContainers: map[string]struct{}{}, allowAllContainers: true,
	}
	if _, err := client.Restart("any-valid-container"); err != nil || requests != 1 {
		t.Fatalf("wildcard restart failed: requests=%d err=%v", requests, err)
	}
	if _, err := client.Stop("../invalid"); err == nil || requests != 1 {
		t.Fatalf("invalid name reached Docker: requests=%d err=%v", requests, err)
	}
}

func TestUpdateAndRecreateKeepsOriginalUntilReplacementStarts(t *testing.T) {
	var events []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		events = append(events, r.Method+" "+r.URL.Path)
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/containers/api-1/json":
			w.Write([]byte(`{"Id":"old-id","Name":"/api-1","State":{"Running":true},"Config":{"Image":"registry.example/app:v2","Env":["MODE=prod"],"Healthcheck":{"Test":["CMD","true"]}},"HostConfig":{"RestartPolicy":{"Name":"unless-stopped"}},"NetworkSettings":{"Networks":{"app_default":{"Aliases":["api"]}}}}`))
		case r.Method == http.MethodGet && r.URL.Path == "/containers/json":
			w.Write([]byte(`[{"Image":"registry.example/app:v2"}]`))
		case r.Method == http.MethodPost && r.URL.Path == "/images/create":
			w.Write([]byte("{\"status\":\"Downloaded newer image\"}\n"))
		case r.Method == http.MethodPost && r.URL.Path == "/containers/api-1/stop":
			w.WriteHeader(http.StatusNoContent)
		case r.Method == http.MethodPost && r.URL.Path == "/containers/old-id/rename":
			if !strings.HasPrefix(r.URL.Query().Get("name"), "api-1.meerkat42-rollback-") {
				t.Fatalf("unexpected rollback name %q", r.URL.Query().Get("name"))
			}
			w.WriteHeader(http.StatusNoContent)
		case r.Method == http.MethodPost && r.URL.Path == "/containers/create":
			var body map[string]interface{}
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Fatal(err)
			}
			if body["HostConfig"] == nil || body["NetworkingConfig"] == nil || body["Image"] != "registry.example/app:v2" {
				t.Fatalf("replacement did not preserve configuration: %#v", body)
			}
			w.WriteHeader(http.StatusCreated)
			w.Write([]byte(`{"Id":"new-id"}`))
		case r.Method == http.MethodPost && r.URL.Path == "/containers/new-id/start":
			w.WriteHeader(http.StatusNoContent)
		case r.Method == http.MethodGet && r.URL.Path == "/containers/new-id/json":
			w.Write([]byte(`{"Id":"new-id","State":{"Running":true,"Health":{"Status":"healthy"}},"Config":{"Image":"registry.example/app:v2"},"HostConfig":{},"NetworkSettings":{"Networks":{}}}`))
		case r.Method == http.MethodDelete && r.URL.Path == "/containers/old-id":
			w.WriteHeader(http.StatusNoContent)
		default:
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.String())
		}
	}))
	defer server.Close()
	client := &Client{
		base: server.URL, http: server.Client(),
		allowed:           map[string]struct{}{"registry.example": {}},
		allowedContainers: map[string]struct{}{"api-1": {}},
	}
	status, err := client.UpdateAndRecreate("api-1")
	if err != nil || status != "updated and recreated" {
		t.Fatalf("update failed: status=%q err=%v events=%v", status, err, events)
	}
	wantLast := "DELETE /containers/old-id"
	if events[len(events)-1] != wantLast {
		t.Fatalf("original removed before replacement start: events=%v", events)
	}
}

func TestUpdateAndRecreateRestoresOriginalWhenReplacementCannotStart(t *testing.T) {
	var restored, restarted, removedReplacement bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/containers/api-1/json":
			w.Write([]byte(`{"Id":"old-id","State":{"Running":true},"Config":{"Image":"registry.example/app:v2"},"HostConfig":{},"NetworkSettings":{"Networks":{}}}`))
		case r.Method == http.MethodGet && r.URL.Path == "/containers/json":
			w.Write([]byte(`[{"Image":"registry.example/app:v2"}]`))
		case r.Method == http.MethodPost && r.URL.Path == "/images/create":
			w.Write([]byte("{\"status\":\"up to date\"}\n"))
		case r.Method == http.MethodPost && r.URL.Path == "/containers/api-1/stop":
			w.WriteHeader(http.StatusNoContent)
		case r.Method == http.MethodPost && r.URL.Path == "/containers/old-id/rename":
			if r.URL.Query().Get("name") == "api-1" {
				restored = true
			}
			w.WriteHeader(http.StatusNoContent)
		case r.Method == http.MethodPost && r.URL.Path == "/containers/create":
			w.WriteHeader(http.StatusCreated)
			w.Write([]byte(`{"Id":"new-id"}`))
		case r.Method == http.MethodPost && r.URL.Path == "/containers/new-id/start":
			w.WriteHeader(http.StatusInternalServerError)
		case r.Method == http.MethodDelete && r.URL.Path == "/containers/new-id":
			removedReplacement = true
			w.WriteHeader(http.StatusNoContent)
		case r.Method == http.MethodPost && r.URL.Path == "/containers/old-id/start":
			restarted = true
			w.WriteHeader(http.StatusNoContent)
		default:
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.String())
		}
	}))
	defer server.Close()
	client := &Client{
		base: server.URL, http: server.Client(),
		allowed:           map[string]struct{}{"registry.example": {}},
		allowedContainers: map[string]struct{}{"api-1": {}},
	}
	if _, err := client.UpdateAndRecreate("api-1"); err == nil {
		t.Fatal("expected replacement start failure")
	}
	if !removedReplacement || !restored || !restarted {
		t.Fatalf("rollback incomplete: removed=%v restored=%v restarted=%v", removedReplacement, restored, restarted)
	}
}
