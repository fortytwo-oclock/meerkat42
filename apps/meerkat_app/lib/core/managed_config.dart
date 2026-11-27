import 'package:flutter/services.dart';

import 'models.dart';

/// Reads zero-touch Enterprise MDM configuration pushed through:
/// - Apple Business Manager (`com.apple.configuration.managed` via NSUserDefaults)
/// - Managed Google Play (`RestrictionsManager` / `app_restrictions.xml`)
/// - Microsoft Intune / Group Policy (Windows Registry / OMA-URI)
class ManagedConfigService {
  static const MethodChannel _channel =
      MethodChannel('com.fortytwooclock.meerkat/managed_config');

  Future<HubConnectionConfig?> loadManagedConfig() async {
    try {
      final raw = await _channel.invokeMapMethod<String, dynamic>('getManagedConfig');
      if (raw == null || raw.isEmpty) return null;
      final hubUrl = (raw['hub_url'] as String?)?.trim() ?? '';
      if (hubUrl.isEmpty) return null;
      return HubConnectionConfig(
        hubBaseUrl: hubUrl,
        operatorToken: (raw['operator_token'] as String?)?.trim() ?? '',
        enrollmentToken: (raw['enroll_token'] as String?)?.trim() ?? '',
        clientGroup: (raw['client_group'] as String?)?.trim().isNotEmpty == true
            ? (raw['client_group'] as String).trim()
            : 'default',
        lockedByMdm: raw['lock_server'] == true,
      );
    } on MissingPluginException {
      // Unmanaged / consumer install or desktop platform without MDM bridge.
      return null;
    } catch (_) {
      return null;
    }
  }
}
