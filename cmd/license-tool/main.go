package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"control-plane/pkg/license"
)

func main() {
	keygenCmd := flag.NewFlagSet("keygen", flag.ExitOnError)
	signCmd := flag.NewFlagSet("sign", flag.ExitOnError)
	verifyCmd := flag.NewFlagSet("verify", flag.ExitOnError)

	// Sign flags
	privKeyFlag := signCmd.String("privkey", "", "Base64-encoded Ed25519 private key (64 bytes or 32-byte seed)")
	custFlag := signCmd.String("customer", "Apex MSP Solutions", "Customer display name")
	tierFlag := signCmd.String("tier", "enterprise", "License tier (community, pro, enterprise)")
	nodesFlag := signCmd.Int("nodes", 100, "Maximum allowed nodes (-1 for unlimited)")
	daysFlag := signCmd.Int("days", 365, "License validity duration in days")
	featuresFlag := signCmd.String("features", "multi_tenancy,smart_forensics,zfs_checksum_alerts,vault_backup,white_label", "Comma-separated features")

	// Verify flags
	pubKeyFlag := verifyCmd.String("pubkey", "", "Base64-encoded Ed25519 public key (32 bytes)")
	tokenFlag := verifyCmd.String("token", "", "Signed license token string")

	if len(os.Args) < 2 {
		printUsage()
		os.Exit(1)
	}

	switch os.Args[1] {
	case "keygen":
		keygenCmd.Parse(os.Args[2:])
		pub, priv, err := ed25519.GenerateKey(rand.Reader)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Error generating key: %v\n", err)
			os.Exit(1)
		}
		fmt.Printf("=== NEW ED25519 KEYPAIR ===\n")
		fmt.Printf("Public Key (embed in Agent/Hub):\n%s\n\n", base64.StdEncoding.EncodeToString(pub))
		fmt.Printf("Private Key (keep strictly secret on SaaS billing server):\n%s\n", base64.StdEncoding.EncodeToString(priv))

	case "sign":
		signCmd.Parse(os.Args[2:])
		if *privKeyFlag == "" {
			fmt.Fprintln(os.Stderr, "Error: -privkey is required")
			os.Exit(1)
		}
		rawPriv, err := base64.StdEncoding.DecodeString(*privKeyFlag)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Invalid base64 private key: %v\n", err)
			os.Exit(1)
		}
		var privKey ed25519.PrivateKey
		if len(rawPriv) == ed25519.PrivateKeySize {
			privKey = ed25519.PrivateKey(rawPriv)
		} else if len(rawPriv) == ed25519.SeedSize {
			privKey = ed25519.NewKeyFromSeed(rawPriv)
		} else {
			fmt.Fprintf(os.Stderr, "Invalid private key size: expected %d or %d bytes, got %d\n",
				ed25519.PrivateKeySize, ed25519.SeedSize, len(rawPriv))
			os.Exit(1)
		}

		features := strings.Split(*featuresFlag, ",")
		now := time.Now().Unix()
		exp := now + int64(*daysFlag)*86400

		claims := license.Claims{
			Issuer:       "control-plane-saas",
			Subject:      fmt.Sprintf("sub_%d", now),
			CustomerName: *custFlag,
			Tier:         *tierFlag,
			MaxNodes:     *nodesFlag,
			Features:     features,
			IssuedAt:     now,
			ExpiresAt:    exp,
		}

		tok, err := license.SignLicense(claims, privKey)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Failed to sign license: %v\n", err)
			os.Exit(1)
		}
		fmt.Printf("=== GENERATED SIGNED LICENSE TOKEN ===\n%s\n", tok)

	case "verify":
		verifyCmd.Parse(os.Args[2:])
		if *pubKeyFlag == "" || *tokenFlag == "" {
			fmt.Fprintln(os.Stderr, "Error: both -pubkey and -token are required")
			os.Exit(1)
		}
		rawPub, err := base64.StdEncoding.DecodeString(*pubKeyFlag)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Invalid base64 public key: %v\n", err)
			os.Exit(1)
		}
		if len(rawPub) != ed25519.PublicKeySize {
			fmt.Fprintf(os.Stderr, "Invalid public key size: expected %d bytes, got %d\n",
				ed25519.PublicKeySize, len(rawPub))
			os.Exit(1)
		}
		pubKey := ed25519.PublicKey(rawPub)

		lic, err := license.VerifyLicense(*tokenFlag, pubKey)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Verification FAILED: %v\n", err)
			os.Exit(1)
		}
		fmt.Println("Verification SUCCESS!")
		fmt.Printf("Customer: %s\nTier: %s\nMax Nodes: %d\nExpires: %s\nFeatures: %v\n",
			lic.Claims.CustomerName, lic.Claims.Tier, lic.Claims.MaxNodes,
			time.Unix(lic.Claims.ExpiresAt, 0).Format(time.RFC3339),
			lic.Claims.Features)

	default:
		printUsage()
		os.Exit(1)
	}
}

func printUsage() {
	fmt.Println("Usage: license-tool <keygen|sign|verify> [options]")
	fmt.Println("  keygen                              Generate Ed25519 keypair")
	fmt.Println("  sign   -privkey <b64> [options]     Sign new offline customer license")
	fmt.Println("  verify -pubkey <b64> -token <jwt>   Mathematically verify a license offline")
}
