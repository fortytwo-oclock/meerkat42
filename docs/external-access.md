# External dashboard access

This guide publishes a Meerkat42 dashboard at an HTTPS address such as
`https://monitor.example.com`. Pangolin is the recommended option when the
headend is behind NAT or you do not want to forward inbound ports. Caddy is the
simplest traditional reverse proxy. Nginx Proxy Manager is a good graphical
alternative.

Do not publish ports 3000 or 8080 directly to the internet. The public entry
point must terminate TLS and support WebSocket upgrades.

## How traffic must be routed

Meerkat42 serves two processes behind one public hostname:

| Public request | Internal target |
|---|---|
| `/api/*` | Hub on port 8080 |
| Everything else | Dashboard on port 3000 |

The `/api/*` route includes two long-lived WebSocket connections:

- `/api/v1/ui/stream` connects browsers to live dashboard updates.
- `/api/v1/agent/stream` connects remote agents to the Hub.

The proxy must preserve the original path. Do not strip `/api`.

## Prepare Meerkat42

Choose the public hostname before starting the stack. Edit
`deploy/self-host.env` or `deploy/pi.env`:

```dotenv
BIND_ADDRESS=127.0.0.1
CORS_ORIGINS=https://monitor.example.com
```

Use the exact public origin, with no trailing slash. Recreate the services:

```bash
docker compose --env-file deploy/self-host.env \
  -f deploy/docker-compose.hub.yml up -d --build
```

For the Raspberry Pi profile, substitute `deploy/pi.env` and
`deploy/docker-compose.pi.yml`.

Keeping `BIND_ADDRESS=127.0.0.1` is preferred when the reverse proxy or Pangolin
Newt connector runs directly on the same host. If the connector runs in a
container, see the container networking notes below before changing the bind
address.

## Option 1: Pangolin and Newt

Pangolin uses an outbound Newt tunnel, so the Meerkat42 headend does not need a
public IP, port forwarding, or inbound firewall opening. You can use Pangolin
Cloud or a separate self-hosted Pangolin server.

### 1. Create the site

In Pangolin, create a **Newt site** for the network containing the Meerkat42
headend. Install Newt on the headend and use the endpoint, ID, and secret shown
by Pangolin. Keep the Newt secret outside Git.

Run Newt as a native service, or as a container using host networking on Linux.
With either arrangement, its targets can be the loopback addresses below. A
minimal container shape is:

```yaml
services:
  newt:
    image: fosrl/newt
    restart: unless-stopped
    network_mode: host
    environment:
      PANGOLIN_ENDPOINT: https://pangolin.example.com
      NEWT_ID: replace-with-site-id
      NEWT_SECRET: replace-with-site-secret
```

Use Pangolin's generated deployment command when it differs from this example.
Pin a tested Newt image version for a production installation.

### 2. Create one HTTPS public resource

For the PostgreSQL/Redis cloud topology, start Meerkat42 with
`deploy/docker-compose.pangolin.yml` as documented in `deploy/README.md`. Caddy
then provides the complete application on one loopback target:

| Method | Hostname | Port | Path | Match | Rewrite |
|---|---:|---:|---|---|---|
| HTTP | `127.0.0.1` | 8080 | `/` | Prefix | None |

For the self-hosted SQLite or Raspberry Pi topology, create one public
HTTP/HTTPS resource named `Meerkat42` with the public domain
`monitor.example.com`, then add these two targets to that resource:

| Method | Hostname | Port | Path | Match | Rewrite |
|---|---:|---:|---|---|---|
| HTTP | `127.0.0.1` | 8080 | `/api` | Prefix | None |
| HTTP | `127.0.0.1` | 3000 | `/` | Prefix | None |

In the two-target topology, Pangolin gives the more-specific `/api` target
priority over the `/` catch-all. If you set priorities manually, give the Hub
target the higher value. Do not configure any target to strip or rewrite its
path.

Optional health checks are:

- Cloud Caddy target: `GET /api/v1/ready`, expected status 200.
- Self-hosted Hub target: `GET /api/v1/ready`, expected status 200.
- Self-hosted dashboard target: `GET /`, expected status 200.

Enable Pangolin SSO and grant the resource only to the users or roles that
should see the dashboard. Pangolin authentication is an additional outer
gate—it does not replace the Meerkat42 operator token and role checks.

### 3. Let agents reach their WebSocket

An agent cannot complete a browser SSO flow. In the resource's **Rules** tab,
add the highest-priority rule below:

| Action | Match type | Value |
|---|---|---|
| Bypass authentication | Path | `/api/v1/agent/stream` |

Bypass only that exact path. Do not bypass `/api/*`. The Hub still requires the
agent's enrollment token on the WebSocket handshake.

If Stripe billing webhooks are enabled, also bypass only
`/api/v1/billing/stripe/webhook`; the Hub validates Stripe's webhook signature.
No other API path should need a Pangolin authentication bypass.

### 4. Point agents at the public address

Set each remote agent's `deploy/agent.env` to:

```dotenv
HUB_URL=wss://monitor.example.com/api/v1/agent/stream
```

Recreate the agent and check its connection:

```bash
docker compose -f deploy/docker-compose.agent.yml up -d --build
docker compose -f deploy/docker-compose.agent.yml logs -f agent
```

### Pangolin container networking

Inside a Newt container, `127.0.0.1` normally means the Newt container itself,
not the Docker host. Use one of these arrangements:

1. On Linux, use `network_mode: host` for Newt and retain Meerkat42's loopback
   bind. This is the simplest same-host setup.
2. Put Newt and the Meerkat42 services on a deliberately shared Docker network,
   then use service names `hub:8080` and `dashboard:3000` as the targets.
3. Run Newt on another trusted LAN machine, bind Meerkat42 to its LAN address,
   and firewall ports 3000 and 8080 so only that Newt machine can reach them.

Avoid mounting the Docker socket into Newt unless you intentionally use
Pangolin's container discovery. It is not required for these manual targets.

## Option 2: Caddy

Use this option when DNS points to the headend and TCP ports 80 and 443 can be
forwarded to it. Install Caddy on the host, leave Meerkat42 bound to loopback,
and use this Caddyfile:

```caddyfile
monitor.example.com {
	encode zstd gzip

	@api path /api/*
	handle @api {
		reverse_proxy 127.0.0.1:8080
	}

	handle {
		reverse_proxy 127.0.0.1:3000
	}

	header {
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options "nosniff"
		Referrer-Policy "strict-origin-when-cross-origin"
		-Server
	}
}
```

Caddy obtains and renews the certificate automatically when the DNS record is
correct and ports 80 and 443 reach Caddy. Its reverse proxy supports WebSockets
without a separate upgrade-header configuration.

The production Compose topology already includes Caddy and uses the equivalent
[`deploy/Caddyfile`](../deploy/Caddyfile). Do not start a second Caddy instance
on the same ports.

## Option 3: Nginx Proxy Manager

Create a Proxy Host for `monitor.example.com`:

1. Forward the default location to the Meerkat42 host on port 3000 using HTTP.
2. Add a custom location `/api/` that forwards to the same host on port 8080
   using HTTP. Preserve the path.
3. Enable **Websockets Support**.
4. Request a Let's Encrypt certificate, enable **Force SSL**, and then enable
   HTTP/2 if desired.
5. Set `CORS_ORIGINS=https://monitor.example.com` and recreate Meerkat42.

Nginx Proxy Manager commonly runs in Docker. Do not use `127.0.0.1` as its
target unless it shares the host network; loopback inside that container points
back to Nginx Proxy Manager. Prefer a shared Docker network and service names.
Otherwise, target the headend's LAN address, set `BIND_ADDRESS=0.0.0.0`, and use
the host firewall to allow ports 3000 and 8080 only from the proxy.

The same two-route pattern works with Traefik, HAProxy, Apache, and a cloud load
balancer: route `/api/*` to port 8080, route `/` to port 3000, preserve paths,
terminate TLS, and allow WebSocket upgrades.

## Private access instead of publishing

If only administrators need the dashboard, a private Pangolin resource,
Tailscale, or WireGuard is safer than a public hostname. Grant the private
resource only to managed users or machines and keep both Meerkat42 ports off the
public internet. You still need HTTPS when traffic crosses an untrusted network,
and agents still need a routable `ws://` or `wss://` address for the Hub.

## Verify the deployment

Run these checks from outside the headend's network:

```bash
curl -I https://monitor.example.com/
curl https://monitor.example.com/api/v1/ready
```

Then:

1. Open `https://monitor.example.com` and complete any proxy authentication.
2. In **Settings**, save the Meerkat42 `ADMIN_TOKEN` as the Operator token.
3. Confirm nodes and live metrics load without browser WebSocket errors.
4. Confirm an agent using the `wss://` URL appears within 30 seconds.
5. Confirm direct public connections to ports 3000 and 8080 fail.

If the page loads but live data does not update, the `/api` route or WebSocket
upgrade is misconfigured. If Pangolin shows a login page to an agent, correct
the exact-path bypass rule. A `401` from the Hub means the tunnel works but the
Meerkat42 agent token is missing, expired, or incorrect.

## Security checklist

- Publish only TCP 443, plus TCP 80 when the proxy needs it for certificate
  issuance or HTTPS redirects.
- Keep ports 3000 and 8080 bound to loopback or restricted to the proxy host.
- Use an exact HTTPS value for `CORS_ORIGINS`.
- Use `wss://` for every agent crossing the internet.
- Keep Pangolin/Newt credentials, `ADMIN_TOKEN`, and agent tokens out of Git.
- Use a separate, expiring, group-bound enrollment token for each deployment
  instead of reusing the bootstrap token indefinitely.
- Retain Meerkat42 RBAC even when the proxy provides SSO or another login gate.
- Back up the Hub before changing networking or upgrading the proxy.

Current product documentation for Pangolin's sites, path-based targets, rules,
and authentication is available at <https://docs.pangolin.net/>. Caddy's proxy
documentation is at <https://caddyserver.com/docs/caddyfile/directives/reverse_proxy>.
