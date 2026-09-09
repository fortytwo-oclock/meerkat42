#!/usr/bin/env python3
import base64
import json
import time
import sys
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('utf-8').rstrip('=')

def b64url_decode(s: str) -> bytes:
    padding = '=' * (4 - (len(s) % 4)) if (len(s) % 4) != 0 else ''
    return base64.urlsafe_b64decode(s + padding)

def generate_keypair():
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_bytes = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption()
    )
    pub_bytes = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )
    return base64.b64encode(priv_bytes).decode(), base64.b64encode(pub_bytes).decode()

def sign_license(priv_key_b64: str, customer_name: str, tier: str, max_nodes: int, features: list, days: int = 365) -> str:
    priv_bytes = base64.b64decode(priv_key_b64)
    priv_key = ed25519.Ed25519PrivateKey.from_private_bytes(priv_bytes)
    
    header = {"alg": "EdDSA", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "iss": "control-plane-saas",
        "sub": f"cust_{now}",
        "customer_name": customer_name,
        "tier": tier,
        "max_nodes": max_nodes,
        "features": features,
        "iat": now,
        "exp": now + (days * 86400) if days > 0 else 0
    }
    
    h_b64 = b64url_encode(json.dumps(header, separators=(',', ':')).encode('utf-8'))
    p_b64 = b64url_encode(json.dumps(payload, separators=(',', ':')).encode('utf-8'))
    signing_input = f"{h_b64}.{p_b64}".encode('utf-8')
    
    sig = priv_key.sign(signing_input)
    sig_b64 = b64url_encode(sig)
    
    return f"{h_b64}.{p_b64}.{sig_b64}"

def verify_license(pub_key_b64: str, token: str) -> dict:
    pub_bytes = base64.b64decode(pub_key_b64)
    pub_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
    
    parts = token.strip().split('.')
    if len(parts) != 3:
        raise ValueError("Invalid license format: expected header.payload.signature")
    
    h_b64, p_b64, sig_b64 = parts
    signing_input = f"{h_b64}.{p_b64}".encode('utf-8')
    sig = b64url_decode(sig_b64)
    
    # 1. Cryptographic signature verification
    pub_key.verify(sig, signing_input)
    
    # 2. Decode payload
    payload = json.loads(b64url_decode(p_b64).decode('utf-8'))
    
    # 3. Check expiration
    now = int(time.time())
    if payload.get("exp", 0) > 0 and now > payload["exp"]:
        raise ValueError(f"License expired at {time.ctime(payload['exp'])}")
        
    return payload

if __name__ == "__main__":
    print("Testing Offline Ed25519 Licensing Engine...")
    priv_b64, pub_b64 = generate_keypair()
    print(f"Generated Public Key:  {pub_b64}")
    print(f"Generated Private Key: {priv_b64}")
    
    token = sign_license(
        priv_b64,
        customer_name="Precision Managed Services Inc.",
        tier="enterprise",
        max_nodes=100,
        features=["multi_tenancy", "smart_forensics", "zfs_checksum_alerts", "vault_backup", "white_label"],
        days=365
    )
    print(f"\nGenerated Signed Token:\n{token}\n")
    
    # Verify valid token
    verified = verify_license(pub_b64, token)
    print(f"Verification Success! Customer: {verified['customer_name']}, Tier: {verified['tier']}, Max Nodes: {verified['max_nodes']}")
    
    # Test Tampering attack: Modify payload max_nodes from 100 to 9999
    print("\nSimulating Tampering Attack: Altering payload 'max_nodes' to 9999...")
    h_b64, p_b64, sig_b64 = token.split('.')
    payload_data = json.loads(b64url_decode(p_b64).decode('utf-8'))
    payload_data["max_nodes"] = 9999
    tampered_p_b64 = b64url_encode(json.dumps(payload_data, separators=(',', ':')).encode('utf-8'))
    tampered_token = f"{h_b64}.{tampered_p_b64}.{sig_b64}"
    
    try:
        verify_license(pub_b64, tampered_token)
        print("ERROR: Tampered token was accepted!")
        sys.exit(1)
    except Exception as e:
        print(f"Security Test Passed: Tampered token correctly rejected by Ed25519 signature verification: {e}")
