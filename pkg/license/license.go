package license

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
)

var (
	ErrInvalidFormat    = errors.New("invalid license format: expected header.payload.signature")
	ErrInvalidSignature = errors.New("cryptographic signature verification failed: license tampered or invalid")
	ErrLicenseExpired   = errors.New("license has expired")
	ErrNodeLimitExceeded = errors.New("maximum node limit exceeded under current license")
	ErrFeatureDisabled  = errors.New("requested feature is not enabled in current license tier")
)

// Header defines the standard JWT header for EdDSA (Ed25519).
type Header struct {
	Alg string `json:"alg"`
	Typ string `json:"typ"`
}

// Claims defines the payload containing customer tier and resource limits.
type Claims struct {
	Issuer        string   `json:"iss"`
	Subject       string   `json:"sub"`
	CustomerName  string   `json:"customer_name"`
	Tier          string   `json:"tier"`           // "community", "pro", "enterprise"
	MaxNodes      int      `json:"max_nodes"`      // -1 for unlimited, or e.g. 3, 25, 100
	Features      []string `json:"features"`       // "multi_tenancy", "smart_forensics", "zfs_checksum_alerts", "vault_backup", "white_label"
	WhiteLabelLogo string   `json:"white_label_logo,omitempty"`
	IssuedAt      int64    `json:"iat"`
	ExpiresAt     int64    `json:"exp"`
}

// License represents a parsed and validated license.
type License struct {
	RawToken string
	Header   Header
	Claims   Claims
}

// SignLicense creates a signed Ed25519 license token given claims and an Ed25519 private key.
func SignLicense(claims Claims, privKey ed25519.PrivateKey) (string, error) {
	header := Header{
		Alg: "EdDSA",
		Typ: "JWT",
	}

	headerJSON, err := json.Marshal(header)
	if err != nil {
		return "", fmt.Errorf("marshal header: %w", err)
	}

	claimsJSON, err := json.Marshal(claims)
	if err != nil {
		return "", fmt.Errorf("marshal claims: %w", err)
	}

	headerB64 := base64.RawURLEncoding.EncodeToString(headerJSON)
	claimsB64 := base64.RawURLEncoding.EncodeToString(claimsJSON)

	signingInput := headerB64 + "." + claimsB64
	sig := ed25519.Sign(privKey, []byte(signingInput))
	sigB64 := base64.RawURLEncoding.EncodeToString(sig)

	return signingInput + "." + sigB64, nil
}

// VerifyLicense mathematically checks the Ed25519 signature and validates expiration offline.
func VerifyLicense(token string, pubKey ed25519.PublicKey) (*License, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return nil, ErrInvalidFormat
	}

	headerB64, claimsB64, sigB64 := parts[0], parts[1], parts[2]
	signingInput := headerB64 + "." + claimsB64

	sig, err := base64.RawURLEncoding.DecodeString(sigB64)
	if err != nil {
		return nil, fmt.Errorf("decode signature: %w", err)
	}

	// 1. Verify Ed25519 signature
	if !ed25519.Verify(pubKey, []byte(signingInput), sig) {
		return nil, ErrInvalidSignature
	}

	// 2. Decode header
	headerJSON, err := base64.RawURLEncoding.DecodeString(headerB64)
	if err != nil {
		return nil, fmt.Errorf("decode header: %w", err)
	}
	var header Header
	if err := json.Unmarshal(headerJSON, &header); err != nil {
		return nil, fmt.Errorf("unmarshal header: %w", err)
	}

	// 3. Decode claims
	claimsJSON, err := base64.RawURLEncoding.DecodeString(claimsB64)
	if err != nil {
		return nil, fmt.Errorf("decode claims: %w", err)
	}
	var claims Claims
	if err := json.Unmarshal(claimsJSON, &claims); err != nil {
		return nil, fmt.Errorf("unmarshal claims: %w", err)
	}

	// 4. Validate expiration
	now := time.Now().Unix()
	if claims.ExpiresAt > 0 && now > claims.ExpiresAt {
		return nil, ErrLicenseExpired
	}

	return &License{
		RawToken: token,
		Header:   header,
		Claims:   claims,
	}, nil
}

// CheckNodeLimit checks if the current node count is allowed under the license.
func (l *License) CheckNodeLimit(currentNodeCount int) error {
	if l == nil {
		// Default to Community Edition limit of 3 nodes
		if currentNodeCount >= 3 {
			return ErrNodeLimitExceeded
		}
		return nil
	}

	if l.Claims.MaxNodes == -1 {
		return nil // Unlimited
	}

	if currentNodeCount >= l.Claims.MaxNodes {
		return fmt.Errorf("%w: current %d, allowed %d", ErrNodeLimitExceeded, currentNodeCount, l.Claims.MaxNodes)
	}
	return nil
}

// HasFeature verifies whether a given enterprise capability is enabled in the claims.
func (l *License) HasFeature(feature string) bool {
	if l == nil {
		return false
	}
	for _, f := range l.Claims.Features {
		if f == feature || f == "*" {
			return true
		}
	}
	return false
}
