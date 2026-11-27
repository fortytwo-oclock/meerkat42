# Meerkat42 Unified App (Windows, macOS, iOS, Android, Linux)

Single-codebase Flutter application for monitoring fleets, acknowledging alerts,
and acting as a Store-safe device monitoring & tracking agent connected to any
self-hosted or cloud Meerkat42 Hub.

## Capabilities

- **Adaptive UI:** NavigationRail on desktop/tablet (Windows, macOS, Linux, iPad)
  and bottom NavigationBar on phones (iOS, Android).
- **Fleet & Device Classes:** Filter Servers, Workstations, Mobile, and IoT
  devices with live CPU, RAM, battery, and location badges.
- **Alerts Inbox:** Real-time alert visibility with one-tap acknowledgment.
- **Embedded Device Agent:** Outbound WebSocket telemetry + opt-in location
  tracking + adaptive `set_stream_rate` live-tail support.
- **Zero-Touch Enterprise MDM:** Reads Managed App Configurations pushed via
  Apple Business Manager, Managed Google Play (`app_restrictions.xml`), and
  Microsoft Intune.

## Develop & Test

```bash
cd apps/meerkat_app
flutter analyze
flutter test
flutter run
```

See [`docs/store-publishing.md`](../../docs/store-publishing.md) for Microsoft
Store (MSIX), Apple App Store / Mac App Store / ABM, and Google Play release
steps.
