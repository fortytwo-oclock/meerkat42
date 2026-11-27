import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'models.dart';

/// Store-safe embedded device telemetry & check-in agent for Windows, macOS,
/// iOS, Android, and Linux.
///
/// Connects outward to `/api/v1/agent/stream`, honors `set_stream_rate` live-tail
/// commands, and stays within App Store / Microsoft Store sandbox permissions.
class EmbeddedDeviceAgent {
  EmbeddedDeviceAgent({
    required this.nodeId,
    this.defaultInterval = const Duration(seconds: 15),
  });

  final String nodeId;
  final Duration defaultInterval;

  WebSocket? _socket;
  Timer? _pushTimer;
  Timer? _revertTimer;
  bool _running = false;
  bool includeLocation = false;
  DeviceLocation? lastLocation;
  DateTime? lastSentAt;
  String statusMessage = 'Stopped';

  bool get isRunning => _running;

  static String detectPlatform() {
    if (Platform.isIOS) return 'ios';
    if (Platform.isAndroid) return 'android';
    if (Platform.isMacOS) return 'macos';
    if (Platform.isWindows) return 'windows';
    return 'linux';
  }

  static String detectDeviceClass() {
    if (Platform.isIOS || Platform.isAndroid) return 'mobile';
    if (Platform.isMacOS || Platform.isWindows) return 'workstation';
    return 'server';
  }

  static Map<String, dynamic> collectHardwareSummary() {
    return {
      'os': Platform.operatingSystem,
      'os_version': Platform.operatingSystemVersion,
      'cpu_cores': Platform.numberOfProcessors,
      'runtime': 'flutter-store-agent',
    };
  }

  Map<String, dynamic> buildHandshake(HubConnectionConfig config) {
    return {
      'node_id': nodeId,
      'auth_token': config.enrollmentToken.isNotEmpty
          ? config.enrollmentToken
          : config.operatorToken,
      'agent_version': '2.0.0-flutter-store',
      'hostname': Platform.localHostname,
      'platform': detectPlatform(),
      'device_class': detectDeviceClass(),
      'arch': _guessArch(),
      'client_group': config.clientGroup,
      'hardware': collectHardwareSummary(),
    };
  }

  Map<String, dynamic> buildTelemetryFrame() {
    final rssMb = (ProcessInfo.currentRss / (1024 * 1024)).round();
    return {
      'node_id': nodeId,
      'hostname': Platform.localHostname,
      'os': '${Platform.operatingSystem} ${Platform.operatingSystemVersion}',
      'platform': detectPlatform(),
      'device_class': detectDeviceClass(),
      'arch': _guessArch(),
      'kernel': Platform.operatingSystemVersion,
      'uptime_sec': 0,
      'timestamp': DateTime.now().toUtc().toIso8601String(),
      'host_metrics': {
        'load_1': 0.0,
        'load_5': 0.0,
        'load_15': 0.0,
        'cpu_usage_pct': 0.0,
        'total_ram_mb': 0,
        'avail_ram_mb': 0,
        'used_ram_mb': rssMb,
        'ram_usage_pct': 0.0,
      },
      'hardware': collectHardwareSummary(),
      if (includeLocation && lastLocation != null)
        'location': lastLocation!.toJson(),
      'metrics': {
        'cpu.cores': Platform.numberOfProcessors.toDouble(),
        'agent.rss_mb': rssMb.toDouble(),
      },
      'agent_version': '2.0.0-flutter-store',
    };
  }

  Future<void> start(
    HubConnectionConfig config, {
    void Function()? onStatusChanged,
  }) async {
    await stop();
    final token = config.enrollmentToken.isNotEmpty
        ? config.enrollmentToken
        : config.operatorToken;
    statusMessage = 'Connecting…';
    onStatusChanged?.call();

    final ws = await WebSocket.connect(
      config.agentStreamUrl,
      headers: {
        'X-Node-ID': nodeId,
        'X-Auth-Token': token,
      },
    ).timeout(const Duration(seconds: 10));

    _socket = ws;
    _running = true;
    statusMessage = 'Connected (${detectPlatform()} / ${detectDeviceClass()})';
    onStatusChanged?.call();

    ws.add(jsonEncode(buildHandshake(config)));
    _sendTelemetry(onStatusChanged);
    _schedulePush(defaultInterval, onStatusChanged);

    ws.listen(
      (event) {
        if (event is String) {
          _handleDownstream(event, onStatusChanged);
        }
      },
      onDone: () {
        _running = false;
        _pushTimer?.cancel();
        _revertTimer?.cancel();
        statusMessage = 'Disconnected';
        onStatusChanged?.call();
      },
      onError: (Object err) {
        _running = false;
        _pushTimer?.cancel();
        _revertTimer?.cancel();
        statusMessage = 'Error: $err';
        onStatusChanged?.call();
      },
      cancelOnError: true,
    );
  }

  void _schedulePush(Duration interval, void Function()? onStatusChanged) {
    _pushTimer?.cancel();
    _pushTimer = Timer.periodic(interval, (_) => _sendTelemetry(onStatusChanged));
  }

  void _sendTelemetry(void Function()? onStatusChanged) {
    final ws = _socket;
    if (ws == null || !_running) return;
    ws.add(jsonEncode(buildTelemetryFrame()));
    lastSentAt = DateTime.now();
    onStatusChanged?.call();
  }

  void _handleDownstream(String raw, void Function()? onStatusChanged) {
    try {
      final msg = decodeJsonObject(raw);
      final action = msg['action'] as String?;
      final cmdId = msg['command_id'] as String? ?? '';
      if (action == 'ping') {
        _socket?.add(jsonEncode({
          'type': 'command_result',
          'command_id': cmdId,
          'action': action,
          'ok': true,
          'result': {'pong': 'ok'},
        }));
      } else if (action == 'set_stream_rate') {
        final payload = msg['payload'] as Map<String, dynamic>? ?? const {};
        final sec = (payload['interval_sec'] as num?)?.toInt() ?? 10;
        final ttl = (payload['ttl_sec'] as num?)?.toInt() ?? 60;
        if (sec >= 1 && sec <= 300) {
          _schedulePush(Duration(seconds: sec), onStatusChanged);
          _revertTimer?.cancel();
          _revertTimer = Timer(Duration(seconds: ttl.clamp(10, 600)), () {
            _schedulePush(defaultInterval, onStatusChanged);
          });
          _socket?.add(jsonEncode({
            'type': 'command_result',
            'command_id': cmdId,
            'action': action,
            'ok': true,
            'result': {'interval_sec': sec, 'ttl_sec': ttl},
          }));
        }
      }
    } catch (_) {
      // Ignore malformed downstream frames.
    }
  }

  Future<void> stop() async {
    _running = false;
    _pushTimer?.cancel();
    _pushTimer = null;
    _revertTimer?.cancel();
    _revertTimer = null;
    final ws = _socket;
    _socket = null;
    if (ws != null) {
      await ws.close();
    }
    statusMessage = 'Stopped';
  }

  static String _guessArch() {
    final v = Platform.version.toLowerCase();
    if (v.contains('arm64')) return 'arm64';
    if (v.contains('x64') || v.contains('amd64')) return 'amd64';
    if (v.contains('arm')) return 'arm';
    return 'unknown';
  }
}
