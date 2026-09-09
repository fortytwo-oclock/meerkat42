// Package vault implements the agent side of the Infrastructure Vault: it reads
// local docker-compose / .env files and AES-256-GCM encrypts each one under a key
// the hub never sees, so the hub stores ciphertext-only disaster-recovery copies.
package vault

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

// EncryptedFile is one captured file. Ciphertext is GCM output (ct||tag); the AAD
// is the (slash-normalised) relative path, binding ciphertext to its location.
type EncryptedFile struct {
	Path       string `json:"path"`
	Nonce      string `json:"nonce"`      // base64 std, 12 bytes
	Ciphertext string `json:"ciphertext"` // base64 std
	SHA256     string `json:"sha256"`     // hex of plaintext, integrity check after decrypt
	Size       int    `json:"size"`       // plaintext byte count
}

// Result is the payload returned to the hub over the tunnel.
type Result struct {
	Files          []EncryptedFile `json:"files"`
	KeyFingerprint string          `json:"key_fingerprint"`
}

var exactNames = map[string]bool{
	"docker-compose.yml":  true,
	"docker-compose.yaml": true,
	"compose.yml":         true,
	"compose.yaml":        true,
	".env":                true,
}

var skipDirs = map[string]bool{
	".git": true, "node_modules": true, "vendor": true, ".cache": true,
}

func wanted(name string) bool {
	lower := strings.ToLower(name)
	return exactNames[lower] || strings.HasSuffix(lower, ".env")
}

func keyFingerprint(key []byte) string {
	sum := sha256.Sum256(key)
	return hex.EncodeToString(sum[:8])
}

// DecodeKey validates and decodes a base64 (std) 32-byte AES-256 key.
func DecodeKey(keyB64 string) ([]byte, error) {
	key, err := base64.StdEncoding.DecodeString(strings.TrimSpace(keyB64))
	if err != nil {
		return nil, fmt.Errorf("decode vault key: %w", err)
	}
	if len(key) != 32 {
		return nil, fmt.Errorf("vault key must be 32 bytes base64, got %d", len(key))
	}
	return key, nil
}

// Backup walks each root (a directory, or a file whose directory is walked),
// encrypts every matching file, and returns them. maxTotal (bytes, 0 = no limit)
// caps the aggregate plaintext size.
func Backup(roots []string, keyB64 string, maxTotal int) (*Result, error) {
	key, err := DecodeKey(keyB64)
	if err != nil {
		return nil, err
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}

	res := &Result{KeyFingerprint: keyFingerprint(key)}
	total := 0
	seen := map[string]bool{}

	for _, root := range roots {
		root = strings.TrimSpace(root)
		if root == "" {
			continue
		}
		info, statErr := os.Stat(root)
		if statErr != nil {
			continue
		}
		base := root
		if !info.IsDir() {
			base = filepath.Dir(root)
		}

		walkErr := filepath.WalkDir(base, func(path string, d fs.DirEntry, err error) error {
			if err != nil {
				return nil // skip unreadable entries
			}
			if d.IsDir() {
				if skipDirs[d.Name()] {
					return filepath.SkipDir
				}
				return nil
			}
			if !wanted(d.Name()) || seen[path] {
				return nil
			}
			seen[path] = true

			plain, rerr := os.ReadFile(path)
			if rerr != nil {
				return nil
			}
			total += len(plain)
			if maxTotal > 0 && total > maxTotal {
				return fmt.Errorf("backup exceeds %d bytes", maxTotal)
			}

			rel := path
			if r, rerr := filepath.Rel(base, path); rerr == nil {
				rel = r
			}
			rel = filepath.ToSlash(rel)

			nonce := make([]byte, gcm.NonceSize())
			if _, nerr := rand.Read(nonce); nerr != nil {
				return nerr
			}
			ct := gcm.Seal(nil, nonce, plain, []byte(rel))
			sum := sha256.Sum256(plain)

			res.Files = append(res.Files, EncryptedFile{
				Path:       rel,
				Nonce:      base64.StdEncoding.EncodeToString(nonce),
				Ciphertext: base64.StdEncoding.EncodeToString(ct),
				SHA256:     hex.EncodeToString(sum[:]),
				Size:       len(plain),
			})
			return nil
		})
		if walkErr != nil {
			return nil, walkErr
		}
	}
	return res, nil
}

// SplitPaths parses a ':' or ',' separated path list from an env var.
func SplitPaths(v string) []string {
	fields := strings.FieldsFunc(v, func(r rune) bool { return r == ':' || r == ',' })
	out := make([]string, 0, len(fields))
	for _, f := range fields {
		if f = strings.TrimSpace(f); f != "" {
			out = append(out, f)
		}
	}
	return out
}
