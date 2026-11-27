import Cocoa
import FlutterMacOS

class MainFlutterWindow: NSWindow {
  override func awakeFromNib() {
    let flutterViewController = FlutterViewController()
    let windowFrame = self.frame
    self.contentViewController = flutterViewController
    self.setFrame(windowFrame, display: true)

    RegisterGeneratedPlugins(registry: flutterViewController)

    let channel = FlutterMethodChannel(
      name: "com.fortytwooclock.meerkat/managed_config",
      binaryMessenger: flutterViewController.engine.binaryMessenger
    )
    channel.setMethodCallHandler { call, result in
      if call.method == "getManagedConfig" {
        if let managed = UserDefaults.standard.dictionary(forKey: "com.apple.configuration.managed") {
          result(managed)
          return
        }
        // Fallback: support direct MDM preference keys pushed via .mobileconfig
        let defaults = UserDefaults.standard
        var map: [String: Any] = [:]
        for key in ["hub_url", "enroll_token", "client_group", "operator_token"] {
          if let val = defaults.string(forKey: key), !val.isEmpty {
            map[key] = val
          }
        }
        if defaults.object(forKey: "lock_server") != nil {
          map["lock_server"] = defaults.bool(forKey: "lock_server")
        }
        result(map.isEmpty ? nil : map)
      } else {
        result(FlutterMethodNotImplemented)
      }
    }

    super.awakeFromNib()
  }
}
