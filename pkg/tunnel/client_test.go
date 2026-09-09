package tunnel

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"control-plane/pkg/telemetry"
	"github.com/gorilla/websocket"
)

func websocketPair(t *testing.T) (*websocket.Conn, <-chan commandResult) {
	t.Helper()
	results := make(chan commandResult, 1)
	upgrader := websocket.Upgrader{CheckOrigin: func(*http.Request) bool { return true }}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			t.Errorf("upgrade: %v", err)
			return
		}
		defer conn.Close()
		var result commandResult
		if err := conn.ReadJSON(&result); err != nil {
			t.Errorf("read command result: %v", err)
			return
		}
		results <- result
	}))
	t.Cleanup(server.Close)

	url := "ws" + strings.TrimPrefix(server.URL, "http")
	conn, _, err := websocket.DefaultDialer.Dial(url, nil)
	if err != nil {
		t.Fatalf("dial test websocket: %v", err)
	}
	t.Cleanup(func() { conn.Close() })
	return conn, results
}

func awaitResult(t *testing.T, results <-chan commandResult) commandResult {
	t.Helper()
	select {
	case result := <-results:
		return result
	case <-time.After(2 * time.Second):
		t.Fatal("timed out waiting for command result")
		return commandResult{}
	}
}

func TestHandleCommandSuccess(t *testing.T) {
	conn, results := websocketPair(t)
	client := NewClient(Config{CommandHandler: func(action string, payload map[string]interface{}) (interface{}, error) {
		if action != "ping" {
			t.Fatalf("action = %q", action)
		}
		return map[string]string{"pong": "ok"}, nil
	}}, nil)
	client.conn = conn

	client.handleCommand(telemetry.HubCommand{CommandID: "cmd-1", Action: "ping"})
	result := awaitResult(t, results)
	if result.Type != "command_result" || result.CommandID != "cmd-1" || result.Action != "ping" || !result.OK {
		t.Fatalf("unexpected result: %#v", result)
	}
}

func TestHandleCommandReportsHandlerError(t *testing.T) {
	conn, results := websocketPair(t)
	client := NewClient(Config{CommandHandler: func(string, map[string]interface{}) (interface{}, error) {
		return nil, errors.New("backup failed")
	}}, nil)
	client.conn = conn

	client.handleCommand(telemetry.HubCommand{CommandID: "cmd-2", Action: "request_config_backup"})
	result := awaitResult(t, results)
	if result.OK || result.Error != "backup failed" || result.Result != nil {
		t.Fatalf("unexpected result: %#v", result)
	}
}

func TestHandleCommandWithoutHandler(t *testing.T) {
	conn, results := websocketPair(t)
	client := NewClient(Config{}, nil)
	client.conn = conn

	client.handleCommand(telemetry.HubCommand{CommandID: "cmd-3", Action: "ping"})
	result := awaitResult(t, results)
	if result.OK || result.Error != "agent has no command handler configured" {
		t.Fatalf("unexpected result: %#v", result)
	}
}
