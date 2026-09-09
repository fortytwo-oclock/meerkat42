package vault

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"os"
	"path/filepath"
	"sort"
	"testing"
)

func testKey() ([]byte, string) {
	key := make([]byte, 32)
	for i := range key {
		key[i] = byte(i + 1)
	}
	return key, base64.StdEncoding.EncodeToString(key)
}

func decryptForTest(t *testing.T, key []byte, file EncryptedFile) []byte {
	t.Helper()
	block, err := aes.NewCipher(key)
	if err != nil {
		t.Fatal(err)
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		t.Fatal(err)
	}
	nonce, err := base64.StdEncoding.DecodeString(file.Nonce)
	if err != nil {
		t.Fatalf("decode nonce: %v", err)
	}
	ciphertext, err := base64.StdEncoding.DecodeString(file.Ciphertext)
	if err != nil {
		t.Fatalf("decode ciphertext: %v", err)
	}
	plain, err := gcm.Open(nil, nonce, ciphertext, []byte(file.Path))
	if err != nil {
		t.Fatalf("decrypt %s: %v", file.Path, err)
	}
	return plain
}

func writeFixture(t *testing.T, path, contents string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(contents), 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestBackupEncryptsOnlyConfigurationFiles(t *testing.T) {
	root := t.TempDir()
	writeFixture(t, filepath.Join(root, "docker-compose.yml"), "services:\n  api:\n    image: example/api\n")
	writeFixture(t, filepath.Join(root, "nested", "production.env"), "TOKEN=super-secret\n")
	writeFixture(t, filepath.Join(root, "notes.txt"), "not a backup target")
	writeFixture(t, filepath.Join(root, ".git", ".env"), "MUST_NOT_ESCAPE=yes\n")

	key, encodedKey := testKey()
	result, err := Backup([]string{root}, encodedKey, 1024*1024)
	if err != nil {
		t.Fatalf("Backup: %v", err)
	}
	if len(result.Files) != 2 {
		t.Fatalf("got %d files, want 2", len(result.Files))
	}

	expectedFingerprint := sha256.Sum256(key)
	if result.KeyFingerprint != hex.EncodeToString(expectedFingerprint[:8]) {
		t.Fatalf("unexpected key fingerprint %q", result.KeyFingerprint)
	}

	sort.Slice(result.Files, func(i, j int) bool { return result.Files[i].Path < result.Files[j].Path })
	expected := map[string]string{
		"docker-compose.yml":     "services:\n  api:\n    image: example/api\n",
		"nested/production.env": "TOKEN=super-secret\n",
	}
	for _, file := range result.Files {
		plain := decryptForTest(t, key, file)
		if string(plain) != expected[file.Path] {
			t.Fatalf("%s plaintext = %q", file.Path, plain)
		}
		if file.Size != len(plain) {
			t.Fatalf("%s size = %d, want %d", file.Path, file.Size, len(plain))
		}
		sum := sha256.Sum256(plain)
		if file.SHA256 != hex.EncodeToString(sum[:]) {
			t.Fatalf("%s checksum mismatch", file.Path)
		}
	}
}

func TestBackupBindsCiphertextToRelativePath(t *testing.T) {
	root := t.TempDir()
	writeFixture(t, filepath.Join(root, ".env"), "SECRET=value\n")
	key, encodedKey := testKey()
	result, err := Backup([]string{root}, encodedKey, 0)
	if err != nil {
		t.Fatal(err)
	}
	file := result.Files[0]
	file.Path = "moved/.env"

	block, _ := aes.NewCipher(key)
	gcm, _ := cipher.NewGCM(block)
	nonce, _ := base64.StdEncoding.DecodeString(file.Nonce)
	ciphertext, _ := base64.StdEncoding.DecodeString(file.Ciphertext)
	if _, err := gcm.Open(nil, nonce, ciphertext, []byte(file.Path)); err == nil {
		t.Fatal("decrypt succeeded after changing authenticated path")
	}
}

func TestBackupEnforcesAggregateLimit(t *testing.T) {
	root := t.TempDir()
	writeFixture(t, filepath.Join(root, ".env"), "1234567890")
	_, encodedKey := testKey()
	if _, err := Backup([]string{root}, encodedKey, 5); err == nil {
		t.Fatal("Backup succeeded above maxTotal")
	}
}

func TestDecodeKeyRejectsInvalidKeys(t *testing.T) {
	for _, value := range []string{"not-base64", base64.StdEncoding.EncodeToString(make([]byte, 16))} {
		if _, err := DecodeKey(value); err == nil {
			t.Fatalf("DecodeKey(%q) succeeded", value)
		}
	}
}

func TestSplitPaths(t *testing.T) {
	got := SplitPaths(" /one:/two, /three ,, ")
	want := []string{"/one", "/two", "/three"}
	if len(got) != len(want) {
		t.Fatalf("SplitPaths = %#v", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("SplitPaths[%d] = %q, want %q", i, got[i], want[i])
		}
	}
}
