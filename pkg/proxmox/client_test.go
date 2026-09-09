package proxmox

import (
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
)

func testClient(t *testing.T, handler http.HandlerFunc) *Client {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)
	base, _ := url.Parse(server.URL)
	return &Client{
		base: base, tokenID: "agent@pve!monitor", secret: "secret",
		allowed: map[int]struct{}{101: {}}, http: server.Client(),
	}
}

func TestGuestActionUsesFixedInventoryAndEndpoint(t *testing.T) {
	requests := 0
	client := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		requests++
		if r.Header.Get("Authorization") != "PVEAPIToken=agent@pve!monitor=secret" {
			t.Fatal("missing API token header")
		}
		if r.Method == http.MethodGet {
			w.Write([]byte(`{"data":[{"vmid":101,"node":"pve-a","type":"qemu","status":"running"}]}`))
			return
		}
		if r.Method != http.MethodPost || r.URL.Path != "/api2/json/nodes/pve-a/qemu/101/status/reboot" {
			t.Fatalf("unexpected action request %s %s", r.Method, r.URL.Path)
		}
		w.Write([]byte(`{"data":"UPID:pve-a:task"}`))
	})
	upid, err := client.GuestAction("pve-a", "qemu", 101, "reboot")
	if err != nil || !strings.HasPrefix(upid, "UPID:") || requests != 2 {
		t.Fatalf("action failed: upid=%q requests=%d err=%v", upid, requests, err)
	}
}

func TestGuestActionRejectsOutsideAllowlist(t *testing.T) {
	client := testClient(t, func(http.ResponseWriter, *http.Request) {
		t.Fatal("API must not be called")
	})
	if _, err := client.GuestAction("pve-a", "lxc", 999, "shutdown"); err == nil {
		t.Fatal("expected allowlist rejection")
	}
}

func TestAllowlistValidation(t *testing.T) {
	if _, err := parseAllowlist("101,102"); err != nil {
		t.Fatal(err)
	}
	if _, err := parseAllowlist("99"); err == nil {
		t.Fatal("expected invalid VMID")
	}
}
