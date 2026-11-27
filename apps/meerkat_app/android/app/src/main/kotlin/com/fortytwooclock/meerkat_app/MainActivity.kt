package com.fortytwooclock.meerkat_app

import android.content.Context
import android.content.RestrictionsManager
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    companion object {
        private const val MANAGED_CONFIG_CHANNEL = "com.fortytwooclock.meerkat/managed_config"
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            MANAGED_CONFIG_CHANNEL
        ).setMethodCallHandler { call, result ->
            if (call.method == "getManagedConfig") {
                val mgr = getSystemService(Context.RESTRICTIONS_SERVICE) as? RestrictionsManager
                val bundle = mgr?.applicationRestrictions
                if (bundle == null || bundle.isEmpty) {
                    result.success(null)
                } else {
                    val map = HashMap<String, Any?>()
                    for (key in bundle.keySet()) {
                        @Suppress("DEPRECATION")
                        map[key] = bundle.get(key)
                    }
                    result.success(map)
                }
            } else {
                result.notImplemented()
            }
        }
    }
}
