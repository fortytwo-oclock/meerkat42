#include "flutter_window.h"

#include <flutter/standard_method_codec.h>

#include <optional>
#include <string>

#include "flutter/generated_plugin_registrant.h"
#include "utils.h"

namespace {

constexpr wchar_t kPolicySubKey[] = L"SOFTWARE\\Policies\\Meerkat42";

std::optional<std::string> ReadRegistryString(const wchar_t* value_name) {
  DWORD size = 0;
  LSTATUS status = RegGetValueW(
      HKEY_LOCAL_MACHINE, kPolicySubKey, value_name, RRF_RT_REG_SZ, nullptr,
      nullptr, &size);
  if (status != ERROR_SUCCESS || size == 0) {
    return std::nullopt;
  }
  std::wstring buf(size / sizeof(wchar_t), L'\0');
  status = RegGetValueW(
      HKEY_LOCAL_MACHINE, kPolicySubKey, value_name, RRF_RT_REG_SZ, nullptr,
      buf.data(), &size);
  if (status != ERROR_SUCCESS) {
    return std::nullopt;
  }
  // Strip trailing NUL written by RegGetValueW.
  if (!buf.empty() && buf.back() == L'\0') {
    buf.pop_back();
  }
  return Utf8FromUtf16(buf.c_str());
}

std::optional<bool> ReadRegistryBool(const wchar_t* value_name) {
  DWORD data = 0;
  DWORD size = sizeof(data);
  LSTATUS status = RegGetValueW(
      HKEY_LOCAL_MACHINE, kPolicySubKey, value_name, RRF_RT_REG_DWORD, nullptr,
      &data, &size);
  if (status != ERROR_SUCCESS) {
    return std::nullopt;
  }
  return data != 0;
}

}  // namespace

FlutterWindow::FlutterWindow(const flutter::DartProject& project)
    : project_(project) {}

FlutterWindow::~FlutterWindow() {}

bool FlutterWindow::OnCreate() {
  if (!Win32Window::OnCreate()) {
    return false;
  }

  RECT frame = GetClientArea();

  // The size here must match the window dimensions to avoid unnecessary surface
  // creation / destruction in the startup path.
  flutter_controller_ = std::make_unique<flutter::FlutterViewController>(
      frame.right - frame.left, frame.bottom - frame.top, project_);
  // Ensure that basic setup of the controller was successful.
  if (!flutter_controller_->engine() || !flutter_controller_->view()) {
    return false;
  }
  RegisterPlugins(flutter_controller_->engine());

  managed_config_channel_ =
      std::make_unique<flutter::MethodChannel<flutter::EncodableValue>>(
          flutter_controller_->engine()->messenger(),
          "com.fortytwooclock.meerkat/managed_config",
          &flutter::StandardMethodCodec::GetInstance());
  managed_config_channel_->SetMethodCallHandler(
      [](const flutter::MethodCall<flutter::EncodableValue>& call,
         std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>>
             result) {
        if (call.method_name() != "getManagedConfig") {
          result->NotImplemented();
          return;
        }
        flutter::EncodableMap config;
        if (auto v = ReadRegistryString(L"HubURL"); v && !v->empty()) {
          config[flutter::EncodableValue("hub_url")] =
              flutter::EncodableValue(*v);
        }
        if (auto v = ReadRegistryString(L"EnrollToken"); v && !v->empty()) {
          config[flutter::EncodableValue("enroll_token")] =
              flutter::EncodableValue(*v);
        }
        if (auto v = ReadRegistryString(L"ClientGroup"); v && !v->empty()) {
          config[flutter::EncodableValue("client_group")] =
              flutter::EncodableValue(*v);
        }
        if (auto v = ReadRegistryString(L"OperatorToken"); v && !v->empty()) {
          config[flutter::EncodableValue("operator_token")] =
              flutter::EncodableValue(*v);
        }
        if (auto v = ReadRegistryBool(L"LockServer"); v.has_value()) {
          config[flutter::EncodableValue("lock_server")] =
              flutter::EncodableValue(*v);
        }
        if (config.empty()) {
          result->Success();
        } else {
          result->Success(flutter::EncodableValue(config));
        }
      });

  SetChildContent(flutter_controller_->view()->GetNativeWindow());

  flutter_controller_->engine()->SetNextFrameCallback([&]() {
    this->Show();
  });

  // Flutter can complete the first frame before the "show window" callback is
  // registered. The following call ensures a frame is pending to ensure the
  // window is shown. It is a no-op if the first frame hasn't completed yet.
  flutter_controller_->ForceRedraw();

  return true;
}

void FlutterWindow::OnDestroy() {
  managed_config_channel_.reset();
  if (flutter_controller_) {
    flutter_controller_ = nullptr;
  }

  Win32Window::OnDestroy();
}

LRESULT
FlutterWindow::MessageHandler(HWND hwnd, UINT const message,
                              WPARAM const wparam,
                              LPARAM const lparam) noexcept {
  // Give Flutter, including plugins, an opportunity to handle window messages.
  if (flutter_controller_) {
    std::optional<LRESULT> result =
        flutter_controller_->HandleTopLevelWindowProc(hwnd, message, wparam,
                                                      lparam);
    if (result) {
      return *result;
    }
  }

  switch (message) {
    case WM_FONTCHANGE:
      flutter_controller_->engine()->ReloadSystemFonts();
      break;
  }

  return Win32Window::MessageHandler(hwnd, message, wparam, lparam);
}
