package diagnostics

import (
	"strings"
	"testing"
)

func TestUnknownDiagnosticIsRejected(t *testing.T) {
	if _, err := Run("shell"); err == nil {
		t.Fatal("expected allowlist rejection")
	}
}

func TestOutputIsBounded(t *testing.T) {
	writer := &limitedBuffer{limit: 4}
	n, err := writer.Write([]byte("abcdef"))
	if n != 4 || err == nil || writer.buffer.String() != "abcd" {
		t.Fatalf("unexpected bounded write n=%d value=%q err=%v", n, writer.buffer.String(), err)
	}
}

func TestCommandDefinitionsHaveNoShell(t *testing.T) {
	for name, command := range commands {
		if len(command) == 0 || strings.Contains(strings.ToLower(command[0]), "sh") {
			t.Fatalf("%s uses an invalid executable: %v", name, command)
		}
	}
}
