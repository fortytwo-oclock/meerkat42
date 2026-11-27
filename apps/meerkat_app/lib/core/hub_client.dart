import 'dart:convert';
import 'dart:io';

import 'models.dart';

class HubApiException implements Exception {
  HubApiException(this.statusCode, this.message);

  final int statusCode;
  final String message;

  @override
  String toString() => 'Hub API error ($statusCode): $message';
}

/// Zero-dependency HTTP client for the Meerkat42 Hub REST API.
class HubClient {
  HubClient({required this.config});

  final HubConnectionConfig config;

  Future<Map<String, dynamic>> _request(
    String method,
    String path, {
    Map<String, dynamic>? body,
  }) async {
    final uri = Uri.parse('${config.restBaseUrl}$path');
    final client = HttpClient()..connectionTimeout = const Duration(seconds: 8);
    try {
      final req = await client.openUrl(method, uri);
      req.headers.set(HttpHeaders.acceptHeader, 'application/json');
      req.headers.set(HttpHeaders.contentTypeHeader, 'application/json');
      if (config.operatorToken.isNotEmpty) {
        req.headers.set(
          HttpHeaders.authorizationHeader,
          'Bearer ${config.operatorToken}',
        );
      }
      if (body != null) {
        req.write(jsonEncode(body));
      }
      final resp = await req.close().timeout(const Duration(seconds: 10));
      final raw = await resp.transform(utf8.decoder).join();
      if (resp.statusCode < 200 || resp.statusCode >= 300) {
        String detail = resp.reasonPhrase;
        try {
          final parsed = decodeJsonObject(raw);
          if (parsed['detail'] != null) detail = parsed['detail'].toString();
        } catch (_) {}
        throw HubApiException(resp.statusCode, detail);
      }
      return decodeJsonObject(raw);
    } finally {
      client.close(force: true);
    }
  }

  Future<List<NodeSummary>> listNodes({String? deviceClass}) async {
    final qs = deviceClass != null && deviceClass.isNotEmpty
        ? '?device_class=${Uri.encodeQueryComponent(deviceClass)}'
        : '';
    final json = await _request('GET', '/api/v1/nodes$qs');
    final rawList = (json['nodes'] as List<dynamic>? ?? const []);
    return rawList
        .whereType<Map<String, dynamic>>()
        .map(NodeSummary.fromJson)
        .toList();
  }

  Future<List<AlertItem>> listAlerts() async {
    final json = await _request('GET', '/api/v1/alerts');
    final rawList = (json['alerts'] as List<dynamic>? ?? const []);
    return rawList
        .whereType<Map<String, dynamic>>()
        .map(AlertItem.fromJson)
        .toList();
  }

  Future<void> acknowledgeAlert(String alertId) async {
    await _request('POST', '/api/v1/alerts/${Uri.encodeComponent(alertId)}/ack');
  }

  Future<({String tier, List<EnterpriseFeatureStatus> enterprise})>
      getLicenseFeatures() async {
    final json = await _request('GET', '/api/v1/license/features');
    final tier = json['active_tier'] as String? ?? 'community';
    final rawList = (json['enterprise_features'] as List<dynamic>? ?? const []);
    final features = rawList
        .whereType<Map<String, dynamic>>()
        .map(EnterpriseFeatureStatus.fromJson)
        .toList();
    return (tier: tier, enterprise: features);
  }
}
