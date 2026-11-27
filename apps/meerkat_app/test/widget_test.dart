import 'package:flutter_test/flutter_test.dart';
import 'package:meerkat_app/core/device_agent.dart';
import 'package:meerkat_app/core/models.dart';
import 'package:meerkat_app/main.dart';

void main() {
  test('parses meerkat42 enrollment pairing URI', () {
    const uri =
        'meerkat42://enroll?hub=wss%3A%2F%2Fmonitor.example.com%2Fapi%2Fv1%2Fagent%2Fstream&token=cpa_secret&group=workstations';
    final cfg = HubConnectionConfig.tryParseEnrollmentUri(uri);
    expect(cfg, isNotNull);
    expect(cfg!.restBaseUrl, 'https://monitor.example.com');
    expect(cfg.agentStreamUrl, 'wss://monitor.example.com/api/v1/agent/stream');
    expect(cfg.enrollmentToken, 'cpa_secret');
    expect(cfg.clientGroup, 'workstations');
  });

  test('embedded device agent builds valid handshake and telemetry frame', () {
    final agent = EmbeddedDeviceAgent(nodeId: 'test-device-1');
    const cfg = HubConnectionConfig(
      hubBaseUrl: 'https://hub.example.com',
      operatorToken: 'op',
      enrollmentToken: 'cpa_test',
      clientGroup: 'mobile-fleet',
    );
    final handshake = agent.buildHandshake(cfg);
    expect(handshake['node_id'], 'test-device-1');
    expect(handshake['auth_token'], 'cpa_test');
    expect(handshake['client_group'], 'mobile-fleet');
    expect(handshake['platform'], isNotEmpty);

    final frame = agent.buildTelemetryFrame();
    expect(frame['node_id'], 'test-device-1');
    expect(frame['metrics'], isA<Map<String, dynamic>>());
  });

  testWidgets('renders Meerkat42 shell navigation', (WidgetTester tester) async {
    await tester.pumpWidget(const MeerkatApp());
    await tester.pumpAndSettle();

    expect(find.text('Meerkat42'), findsOneWidget);
    expect(find.text('Fleet'), findsOneWidget);
    expect(find.text('Alerts'), findsOneWidget);
    expect(find.text('This Device'), findsOneWidget);
    expect(find.text('Connect'), findsOneWidget);
  });
}
