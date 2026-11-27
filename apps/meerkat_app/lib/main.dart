import 'package:material_ui/material_ui.dart';

import 'ui/meerkat_shell.dart';

void main() {
  runApp(const MeerkatApp());
}

class MeerkatApp extends StatelessWidget {
  const MeerkatApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Meerkat42',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF4F46E5),
          brightness: Brightness.light,
        ),
        useMaterial3: true,
      ),
      darkTheme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF6366F1),
          brightness: Brightness.dark,
        ),
        useMaterial3: true,
      ),
      home: const MeerkatShell(),
    );
  }
}
