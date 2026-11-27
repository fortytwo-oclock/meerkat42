import 'dart:io';

import 'package:material_ui/material_ui.dart';

import '../core/device_agent.dart';
import '../core/hub_client.dart';
import '../core/managed_config.dart';
import '../core/models.dart';

class MeerkatShell extends StatefulWidget {
  const MeerkatShell({super.key});

  @override
  State<MeerkatShell> createState() => _MeerkatShellState();
}

class _MeerkatShellState extends State<MeerkatShell> {
  int _selectedIndex = 0;
  HubConnectionConfig _config = const HubConnectionConfig(
    hubBaseUrl: 'http://localhost:8080',
    operatorToken: '',
  );
  final ManagedConfigService _mdmService = ManagedConfigService();
  late final EmbeddedDeviceAgent _agent;

  String? _deviceClassFilter;
  List<NodeSummary> _nodes = const [];
  List<AlertItem> _alerts = const [];
  List<EnterpriseFeatureStatus> _enterpriseFeatures = const [];
  String _activeTier = 'community';
  bool _loading = false;
  String? _error;

  final _hubUrlCtrl = TextEditingController(text: 'http://localhost:8080');
  final _operatorTokenCtrl = TextEditingController();
  final _enrollTokenCtrl = TextEditingController();
  final _groupCtrl = TextEditingController(text: 'default');
  final _pairingUriCtrl = TextEditingController();

  @override
  void initState() {
    super.initState();
    final host = Platform.localHostname.replaceAll(RegExp(r'[^a-zA-Z0-9._-]'), '-');
    _agent = EmbeddedDeviceAgent(
      nodeId: '${EmbeddedDeviceAgent.detectPlatform()}-$host',
    );
    _initManagedConfig();
  }

  Future<void> _initManagedConfig() async {
    final managed = await _mdmService.loadManagedConfig();
    if (managed != null && mounted) {
      setState(() {
        _config = managed;
        _hubUrlCtrl.text = managed.hubBaseUrl;
        _operatorTokenCtrl.text = managed.operatorToken;
        _enrollTokenCtrl.text = managed.enrollmentToken;
        _groupCtrl.text = managed.clientGroup;
      });
      await _refreshAll();
    }
  }

  @override
  void dispose() {
    _agent.stop();
    _hubUrlCtrl.dispose();
    _operatorTokenCtrl.dispose();
    _enrollTokenCtrl.dispose();
    _groupCtrl.dispose();
    _pairingUriCtrl.dispose();
    super.dispose();
  }

  Future<void> _refreshAll() async {
    if (!_config.isConfigured) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    final client = HubClient(config: _config);
    try {
      final results = await Future.wait([
        client.listNodes(deviceClass: _deviceClassFilter),
        client.listAlerts(),
        client.getLicenseFeatures(),
      ]);
      if (!mounted) return;
      final lic = results[2]
          as ({String tier, List<EnterpriseFeatureStatus> enterprise});
      setState(() {
        _nodes = results[0] as List<NodeSummary>;
        _alerts = results[1] as List<AlertItem>;
        _activeTier = lic.tier;
        _enterpriseFeatures = lic.enterprise;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
      });
    } finally {
      if (mounted) {
        setState(() {
          _loading = false;
        });
      }
    }
  }

  void _applyPairingUri() {
    final parsed = HubConnectionConfig.tryParseEnrollmentUri(_pairingUriCtrl.text);
    if (parsed == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Invalid pairing URI. Expected meerkat42://enroll?hub=...'),
        ),
      );
      return;
    }
    setState(() {
      _config = parsed;
      _hubUrlCtrl.text = parsed.restBaseUrl;
      _enrollTokenCtrl.text = parsed.enrollmentToken;
      _groupCtrl.text = parsed.clientGroup;
      if (parsed.operatorToken.isNotEmpty) {
        _operatorTokenCtrl.text = parsed.operatorToken;
      }
    });
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('Loaded Hub ${parsed.restBaseUrl}')),
    );
  }

  void _saveManualConfig() {
    setState(() {
      _config = _config.copyWith(
        hubBaseUrl: _hubUrlCtrl.text.trim(),
        operatorToken: _operatorTokenCtrl.text.trim(),
        enrollmentToken: _enrollTokenCtrl.text.trim(),
        clientGroup:
            _groupCtrl.text.trim().isEmpty ? 'default' : _groupCtrl.text.trim(),
      );
    });
    _refreshAll();
  }

  Future<void> _toggleAgent(bool enabled) async {
    if (!enabled) {
      await _agent.stop();
      if (mounted) setState(() {});
      return;
    }
    try {
      await _agent.start(
        _config,
        onStatusChanged: () {
          if (mounted) setState(() {});
        },
      );
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _agent.statusMessage = 'Connection failed: $e';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final isWide = MediaQuery.sizeOf(context).width >= 760;
    const destinations = [
      NavigationDestination(
        icon: Icon(Icons.dns_outlined),
        selectedIcon: Icon(Icons.dns),
        label: 'Fleet',
      ),
      NavigationDestination(
        icon: Icon(Icons.notifications_outlined),
        selectedIcon: Icon(Icons.notifications_active),
        label: 'Alerts',
      ),
      NavigationDestination(
        icon: Icon(Icons.devices_outlined),
        selectedIcon: Icon(Icons.devices),
        label: 'This Device',
      ),
      NavigationDestination(
        icon: Icon(Icons.settings_outlined),
        selectedIcon: Icon(Icons.settings),
        label: 'Connect',
      ),
    ];

    final pages = [
      _buildFleetView(),
      _buildAlertsView(),
      _buildThisDeviceView(),
      _buildConnectView(),
    ];

    return Scaffold(
      appBar: AppBar(
        title: const Text('Meerkat42'),
        actions: [
          if (_config.lockedByMdm)
            const Padding(
              padding: EdgeInsets.only(right: 8),
              child: Chip(label: Text('Enterprise MDM')),
            ),
          IconButton(
            tooltip: 'Refresh',
            onPressed: _loading ? null : _refreshAll,
            icon: _loading
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.refresh),
          ),
        ],
      ),
      body: isWide
          ? Row(
              children: [
                NavigationRail(
                  selectedIndex: _selectedIndex,
                  onDestinationSelected: (i) => setState(() => _selectedIndex = i),
                  labelType: NavigationRailLabelType.all,
                  destinations: destinations
                      .map(
                        (d) => NavigationRailDestination(
                          icon: d.icon,
                          selectedIcon: d.selectedIcon,
                          label: Text(d.label),
                        ),
                      )
                      .toList(),
                ),
                const VerticalDivider(width: 1),
                Expanded(child: pages[_selectedIndex]),
              ],
            )
          : pages[_selectedIndex],
      bottomNavigationBar: isWide
          ? null
          : NavigationBar(
              selectedIndex: _selectedIndex,
              onDestinationSelected: (i) => setState(() => _selectedIndex = i),
              destinations: destinations,
            ),
    );
  }

  Widget _buildFleetView() {
    const filters = [
      (null, 'All'),
      ('server', 'Servers'),
      ('workstation', 'Workstations'),
      ('mobile', 'Mobile'),
      ('iot', 'IoT'),
    ];
    return Column(
      children: [
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
          child: Row(
            children: filters.map((item) {
              final selected = _deviceClassFilter == item.$1;
              return Padding(
                padding: const EdgeInsets.only(right: 8),
                child: FilterChip(
                  label: Text(item.$2),
                  selected: selected,
                  onSelected: (_) {
                    setState(() => _deviceClassFilter = item.$1);
                    _refreshAll();
                  },
                ),
              );
            }).toList(),
          ),
        ),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
            child: Card(
              color: Theme.of(context).colorScheme.errorContainer,
              child: ListTile(
                leading: const Icon(Icons.warning_amber_rounded),
                title: Text(_error!),
                trailing: TextButton(
                  onPressed: () => setState(() => _selectedIndex = 3),
                  child: const Text('Configure'),
                ),
              ),
            ),
          ),
        Expanded(
          child: _nodes.isEmpty
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Icon(Icons.monitor_heart_outlined, size: 48),
                        const SizedBox(height: 12),
                        const Text(
                          'No devices loaded yet',
                          style: TextStyle(fontWeight: FontWeight.w600),
                        ),
                        const SizedBox(height: 6),
                        const Text(
                          'Connect to your self-hosted or cloud Hub in the Connect tab, or enable monitoring for this device.',
                          textAlign: TextAlign.center,
                        ),
                        const SizedBox(height: 12),
                        FilledButton.tonal(
                          onPressed: _refreshAll,
                          child: const Text('Load Fleet'),
                        ),
                      ],
                    ),
                  ),
                )
              : RefreshIndicator(
                  onRefresh: _refreshAll,
                  child: ListView.builder(
                    padding: const EdgeInsets.all(16),
                    itemCount: _nodes.length,
                    itemBuilder: (context, idx) {
                      final n = _nodes[idx];
                      return Card(
                        margin: const EdgeInsets.only(bottom: 10),
                        child: ListTile(
                          leading: Icon(_iconForDeviceClass(n.deviceClass)),
                          title: Text(n.hostname),
                          subtitle: Text(
                            '${n.clientGroup} · ${n.platform ?? n.deviceClass} · '
                            'CPU ${n.cpuUsagePct.toStringAsFixed(0)}% · '
                            'RAM ${n.ramUsagePct.toStringAsFixed(0)}%'
                            '${n.batteryPct != null ? ' · Batt ${n.batteryPct!.toStringAsFixed(0)}%' : ''}',
                          ),
                          trailing: Chip(
                            label: Text(n.status),
                            visualDensity: VisualDensity.compact,
                          ),
                        ),
                      );
                    },
                  ),
                ),
        ),
      ],
    );
  }

  Widget _buildAlertsView() {
    if (_alerts.isEmpty) {
      return const Center(
        child: Text('No active alerts. Your fleet looks healthy.'),
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.all(16),
      itemCount: _alerts.length,
      itemBuilder: (context, idx) {
        final a = _alerts[idx];
        return Card(
          margin: const EdgeInsets.only(bottom: 10),
          child: ListTile(
            leading: Icon(
              Icons.warning_rounded,
              color: a.severity == 'CRITICAL' ? Colors.red : Colors.amber,
            ),
            title: Text('${a.nodeId}: ${a.subject}'),
            subtitle: Text('${a.message} (${a.state})'),
            trailing: a.state == 'FIRING'
                ? OutlinedButton(
                    onPressed: () async {
                      await HubClient(config: _config).acknowledgeAlert(a.id);
                      await _refreshAll();
                    },
                    child: const Text('Ack'),
                  )
                : null,
          ),
        );
      },
    );
  }

  Widget _buildThisDeviceView() {
    final hw = EmbeddedDeviceAgent.collectHardwareSummary();
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Card(
          child: SwitchListTile(
            title: const Text('Monitor this device'),
            subtitle: Text(_agent.statusMessage),
            value: _agent.isRunning,
            onChanged: _toggleAgent,
          ),
        ),
        const SizedBox(height: 8),
        Card(
          child: SwitchListTile(
            title: const Text('Opt-in device location tracking'),
            subtitle: const Text(
              'Include device coordinates in check-ins when permitted by OS privacy settings.',
            ),
            value: _agent.includeLocation,
            onChanged: (v) => setState(() => _agent.includeLocation = v),
          ),
        ),
        const SizedBox(height: 8),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Local Device Identity',
                  style: TextStyle(fontWeight: FontWeight.w600),
                ),
                const SizedBox(height: 8),
                Text('Node ID: ${_agent.nodeId}'),
                Text('Platform: ${EmbeddedDeviceAgent.detectPlatform()}'),
                Text('Device class: ${EmbeddedDeviceAgent.detectDeviceClass()}'),
                Text('CPU cores: ${hw['cpu_cores']}'),
                Text('Hub stream: ${_config.agentStreamUrl}'),
                if (_agent.lastSentAt != null)
                  Text('Last telemetry push: ${_agent.lastSentAt!.toLocal()}'),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildConnectView() {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Quick Pair (QR / Enrollment URI)',
                  style: TextStyle(fontWeight: FontWeight.w600),
                ),
                const SizedBox(height: 8),
                TextField(
                  controller: _pairingUriCtrl,
                  enabled: !_config.lockedByMdm,
                  decoration: const InputDecoration(
                    labelText: 'meerkat42://enroll?hub=...&token=...',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 8),
                FilledButton.tonal(
                  onPressed: _config.lockedByMdm ? null : _applyPairingUri,
                  child: const Text('Apply Pairing URI'),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 12),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Hub Connection',
                  style: TextStyle(fontWeight: FontWeight.w600),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _hubUrlCtrl,
                  enabled: !_config.lockedByMdm,
                  decoration: const InputDecoration(
                    labelText: 'Hub URL (https://monitor.example.com)',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 10),
                TextField(
                  controller: _operatorTokenCtrl,
                  obscureText: true,
                  decoration: const InputDecoration(
                    labelText: 'Operator / API Token (for Fleet & Alerts)',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 10),
                TextField(
                  controller: _enrollTokenCtrl,
                  obscureText: true,
                  enabled: !_config.lockedByMdm,
                  decoration: const InputDecoration(
                    labelText: 'Agent Enrollment Token (for This Device)',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 10),
                TextField(
                  controller: _groupCtrl,
                  enabled: !_config.lockedByMdm,
                  decoration: const InputDecoration(
                    labelText: 'Room / Client Group',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                FilledButton(
                  onPressed: _saveManualConfig,
                  child: const Text('Save & Connect'),
                ),
              ],
            ),
          ),
        ),
        if (_enterpriseFeatures.isNotEmpty) ...[
          const SizedBox(height: 12),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Hub Edition: ${_activeTier.toUpperCase()}',
                    style: const TextStyle(fontWeight: FontWeight.w600),
                  ),
                  const SizedBox(height: 8),
                  ..._enterpriseFeatures.map(
                    (f) => ListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      title: Text(f.name),
                      subtitle: Text(f.description),
                      trailing: Chip(
                        label: Text(f.enabled ? 'Active' : 'Enterprise'),
                        visualDensity: VisualDensity.compact,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ],
    );
  }

  IconData _iconForDeviceClass(String deviceClass) {
    switch (deviceClass) {
      case 'workstation':
        return Icons.laptop_mac;
      case 'mobile':
        return Icons.smartphone;
      case 'iot':
        return Icons.memory;
      default:
        return Icons.dns;
    }
  }
}
