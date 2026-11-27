# App Store & Enterprise Distribution Guide

Meerkat42 ships a single unified Flutter app ([`apps/meerkat_app/`](../apps/meerkat_app))
paired with the pure-Go Edge Agent ([`cmd/agent/`](../cmd/agent)) so you can
publish directly through your **Microsoft**, **Apple**, and **Google Play**
developer accounts for both open-source self-hosters and Enterprise MDM fleets.

## Dual-Mode Architecture (Store-Safe + Privileged Companion)

| Mode | Distribution Channel | Capabilities |
|---|---|---|
| **Store-Safe Standard Mode** | Microsoft Store, Mac App Store, Apple App Store, Google Play | Fleet dashboard, alert inbox & ack, QR/URI onboarding, sandbox-permitted local device telemetry & opt-in location check-in, Enterprise MDM zero-touch config |
| **Privileged Companion Mode** | Enterprise MSI/PKG via Intune, Jamf, Kandji, or `meerkat-agent service install` | Deep root/SYSTEM telemetry (per-disk I/O, SMART, ZFS, Docker, systemd/Windows Services) running even when no desktop user is logged in |

## 1. Microsoft Store & Intune (Windows 10/11)

1. Update [`apps/meerkat_app/store/msix_config.yaml`](../apps/meerkat_app/store/msix_config.yaml) with your Microsoft Partner Center `publisher` CN and `identity_name`.
2. Build the Windows release binary and MSIX package on a Windows runner:
   ```powershell
   cd apps/meerkat_app
   flutter build windows --release
   ```
3. Upload the signed `.msixupload` bundle to **Microsoft Partner Center**.
4. For Enterprise fleets, assign the Store app in **Microsoft Intune** and optionally deploy `control-plane-agent-windows-amd64.exe` (or `.msi`) for SYSTEM-level workstation monitoring.

## 2. Apple App Store, Mac App Store & Apple Business Manager (iOS / macOS)

1. Configure your Apple Developer Team ID and bundle identifier (`com.fortytwooclock.meerkatApp`) in Xcode (`apps/meerkat_app/ios` and `apps/meerkat_app/macos`).
2. Build signed archives on a macOS runner:
   ```bash
   cd apps/meerkat_app
   flutter build ipa --release
   flutter build macos --release
   ```
3. Submit to **App Store Connect** (iOS App Store & Mac App Store) for public distribution, or publish as **Unlisted / Custom Apps** via **Apple Business Manager (ABM)** for private enterprise rollouts.
4. Import [`apps/meerkat_app/store/apple_managed_config_schema.xml`](../apps/meerkat_app/store/apple_managed_config_schema.xml) into Jamf Pro, Kandji, or Intune to push `hub_url`, `enroll_token`, `client_group`, and `lock_server` zero-touch.

## 3. Google Play Store & Managed Google Play (Android)

1. Configure Play App Signing keystore in `apps/meerkat_app/android/key.properties`.
2. Build the Android App Bundle (`.aab`):
   ```bash
   cd apps/meerkat_app
   flutter build appbundle --release
   ```
3. Upload to **Google Play Console**.
4. The bundled [`app_restrictions.xml`](../apps/meerkat_app/android/app/src/main/res/xml/app_restrictions.xml) automatically exposes Managed Configurations in **Managed Google Play** / Android Enterprise EMM consoles.
