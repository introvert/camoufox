/*
Helper to extract values from the CAMOU_CONFIG environment variable(s).
Written by daijro.
*/

#pragma once
#include "json.hpp"
#include <memory>
#include <string>
#include <string_view>
#include <tuple>
#include <optional>
#include <codecvt>
#include "mozilla/glue/Debug.h"
#include <cstdlib>
#include <cstdio>
#include <mutex>
#include <variant>
#include <cstddef>
#include <vector>
#include <algorithm>

#ifdef _WIN32
#  include <windows.h>
#endif

namespace MaskConfig {

// Function to get the value of an environment variable as a UTF-8 string.
inline std::optional<std::string> get_env_utf8(const std::string& name) {
#ifdef _WIN32
  std::wstring wName(name.begin(), name.end());
  DWORD size = GetEnvironmentVariableW(wName.c_str(), nullptr, 0);
  if (size == 0) return std::nullopt;  // Environment variable not found

  std::vector<wchar_t> buffer(size);
  GetEnvironmentVariableW(wName.c_str(), buffer.data(), size);
  std::wstring wValue(buffer.data());

  // Convert UTF-16 to UTF-8
  std::wstring_convert<std::codecvt_utf8_utf16<wchar_t>> converter;
  return converter.to_bytes(wValue);
#else
  const char* value = std::getenv(name.c_str());
  if (!value) return std::nullopt;
  return std::string(value);
#endif
}

inline const nlohmann::json& GetJson() {
  static std::once_flag initFlag;
  static nlohmann::json jsonConfig;

  std::call_once(initFlag, []() {
    std::string jsonString;
    int index = 1;

    while (true) {
      std::string envName = "CAMOU_CONFIG_" + std::to_string(index);
      auto partialConfig = get_env_utf8(envName);
      if (!partialConfig) break;

      jsonString += *partialConfig;
      index++;
    }

    if (jsonString.empty()) {
      // Check for the original CAMOU_CONFIG as fallback
      auto originalConfig = get_env_utf8("CAMOU_CONFIG");
      if (originalConfig) jsonString = *originalConfig;
    }

    if (jsonString.empty()) {
      jsonConfig = nlohmann::json{};
      return;
    }

    // Validate
    if (!nlohmann::json::accept(jsonString)) {
      printf_stderr("ERROR: Invalid JSON passed to CAMOU_CONFIG!\n");
      jsonConfig = nlohmann::json{};
      return;
    }

    jsonConfig = nlohmann::json::parse(jsonString);
  });

  return jsonConfig;
}

inline bool HasKey(const std::string& key, const nlohmann::json& data) {
  return data.contains(key);
}

inline std::optional<std::string> GetString(const std::string& key) {
  const auto& data = GetJson();
  if (!HasKey(key, data)) return std::nullopt;
  return data[key].get<std::string>();
}

inline std::vector<std::string> GetStringList(const std::string& key) {
  std::vector<std::string> result;
  const auto& data = GetJson();
  if (!HasKey(key, data)) return {};
  for (const auto& item : data[key]) {
    result.push_back(item.get<std::string>());
  }
  return result;
}

inline std::vector<std::string> GetStringListLower(const std::string& key) {
  std::vector<std::string> result = GetStringList(key);
  for (auto& str : result) {
    std::transform(str.begin(), str.end(), str.begin(),
                   [](unsigned char c) { return std::tolower(c); });
  }
  return result;
}

/**
 * The spoofed font family allowlist ("fonts"), lowercased and cached for the
 * lifetime of the process. CAMOU_CONFIG is read once at startup and never
 * changes, and the gfx font lookup paths consult this on every family
 * resolution, so re-parsing the JSON per call is not an option.
 * An empty list means no font spoofing is configured.
 */
inline const std::vector<std::string>& FontAllowlist() {
  static const std::vector<std::string> fonts = GetStringListLower("fonts");
  return fonts;
}

inline bool HasFontAllowlist() { return !FontAllowlist().empty(); }

/**
 * Whether a font family may be used. `family` must already be lowercased
 * (gfxPlatformFontList::GenerateFontListKey output is). Always true when no
 * allowlist is configured.
 */
inline bool IsFontAllowed(std::string_view family) {
  const auto& fonts = FontAllowlist();
  if (fonts.empty()) return true;
  return std::find(fonts.begin(), fonts.end(), family) != fonts.end();
}

template <typename T>
inline std::optional<T> GetUintImpl(const std::string& key) {
  const auto& data = GetJson();
  if (!HasKey(key, data)) return std::nullopt;
  if (data[key].is_number_unsigned()) return data[key].get<T>();
  printf_stderr("ERROR: Value for key '%s' is not an unsigned integer\n",
                key.c_str());
  return std::nullopt;
}

inline std::optional<uint64_t> GetUint64(const std::string& key) {
  return GetUintImpl<uint64_t>(key);
}

inline std::optional<uint32_t> GetUint32(const std::string& key) {
  return GetUintImpl<uint32_t>(key);
}

inline std::optional<int32_t> GetInt32(const std::string& key) {
  const auto& data = GetJson();
  if (!HasKey(key, data)) return std::nullopt;
  if (data[key].is_number_integer()) return data[key].get<int32_t>();
  printf_stderr("ERROR: Value for key '%s' is not an integer\n", key.c_str());
  return std::nullopt;
}

inline std::optional<double> GetDouble(const std::string& key) {
  const auto& data = GetJson();
  if (!HasKey(key, data)) return std::nullopt;
  if (data[key].is_number_float()) return data[key].get<double>();
  if (data[key].is_number_unsigned() || data[key].is_number_integer())
    return static_cast<double>(data[key].get<int64_t>());
  printf_stderr("ERROR: Value for key '%s' is not a double\n", key.c_str());
  return std::nullopt;
}

inline std::optional<bool> GetBool(const std::string& key) {
  const auto& data = GetJson();
  if (!HasKey(key, data)) return std::nullopt;
  if (data[key].is_boolean()) return data[key].get<bool>();
  printf_stderr("ERROR: Value for key '%s' is not a boolean\n", key.c_str());
  return std::nullopt;
}

inline bool CheckBool(const std::string& key) {
  return GetBool(key).value_or(false);
}

inline std::optional<std::array<uint32_t, 4>> GetRect(
    const std::string& left, const std::string& top, const std::string& width,
    const std::string& height) {
  std::array<std::optional<uint32_t>, 4> values = {
      GetUint32(left).value_or(0), GetUint32(top).value_or(0), GetUint32(width),
      GetUint32(height)};

  if (!values[2].has_value() || !values[3].has_value()) {
    if (values[2].has_value() ^ values[3].has_value())
      printf_stderr(
          "Both %s and %s must be provided. Using default behavior.\n",
          height.c_str(), width.c_str());
    return std::nullopt;
  }

  std::array<uint32_t, 4> result;
  std::transform(values.begin(), values.end(), result.begin(),
                 [](const auto& value) { return value.value(); });

  return result;
}

inline std::optional<std::array<int32_t, 4>> GetInt32Rect(
    const std::string& left, const std::string& top, const std::string& width,
    const std::string& height) {
  if (auto optValue = GetRect(left, top, width, height)) {
    std::array<int32_t, 4> result;
    std::transform(optValue->begin(), optValue->end(), result.begin(),
                   [](const auto& val) { return static_cast<int32_t>(val); });
    return result;
  }
  return std::nullopt;
}

// Helpers for WebGL
//
// Every helper takes the JSON object that holds the WebGL keys ("webGl:...",
// "webGl2:..."): the launch-wide CAMOU_CONFIG, or a per-context record set
// with window.setWebGLParameters() (WebGLParamsManager).

inline const nlohmann::json* GetNestedIn(const nlohmann::json& root,
                                         const std::string& domain,
                                         const std::string& keyStr) {
  auto outer = root.find(domain);
  if (outer == root.end() || !outer->is_object()) return nullptr;
  auto inner = outer->find(keyStr);
  if (inner == outer->end()) return nullptr;
  return &*inner;
}

inline std::optional<nlohmann::json> GetNested(const std::string& domain,
                                               std::string keyStr) {
  if (const auto* value = GetNestedIn(GetJson(), domain, keyStr)) {
    return *value;
  }
  return std::nullopt;
}

/**
 * Whether a getParameter() name reads a property of the GPU and driver -- a
 * limit, a range, a bit count of the hardware -- rather than state the page
 * itself changes (viewport, bindings, enable flags, clear color, pixel store,
 * drawing-buffer bits that follow the requested context attributes).
 *
 * Only these are answered from the spoofed table. The table was scraped from
 * a fresh context, so answering state from it froze VIEWPORT at
 * [0,0,300,150] whatever gl.viewport() set and left every binding null after
 * a bind: one call and one read to tell a spoofed context from a real one.
 */
inline bool IsGLConstantParam(uint32_t pname, bool isWebGL2) {
  switch (pname) {
    case 0x0D33:  // MAX_TEXTURE_SIZE
    case 0x0D3A:  // MAX_VIEWPORT_DIMS
    case 0x0D50:  // SUBPIXEL_BITS
    case 0x846D:  // ALIASED_POINT_SIZE_RANGE
    case 0x846E:  // ALIASED_LINE_WIDTH_RANGE
    case 0x84E8:  // MAX_RENDERBUFFER_SIZE
    case 0x851C:  // MAX_CUBE_MAP_TEXTURE_SIZE
    case 0x8869:  // MAX_VERTEX_ATTRIBS
    case 0x8872:  // MAX_TEXTURE_IMAGE_UNITS
    case 0x8B4C:  // MAX_VERTEX_TEXTURE_IMAGE_UNITS
    case 0x8B4D:  // MAX_COMBINED_TEXTURE_IMAGE_UNITS
    case 0x8DFB:  // MAX_VERTEX_UNIFORM_VECTORS
    case 0x8DFC:  // MAX_VARYING_VECTORS
    case 0x8DFD:  // MAX_FRAGMENT_UNIFORM_VECTORS
    // Extension limits; the caller checks the extension is enabled.
    case 0x84FF:  // MAX_TEXTURE_MAX_ANISOTROPY_EXT
    case 0x8824:  // MAX_DRAW_BUFFERS(_WEBGL)
    case 0x8CDF:  // MAX_COLOR_ATTACHMENTS(_WEBGL)
      return true;
    default:
      break;
  }
  if (!isWebGL2) return false;
  switch (pname) {
    case 0x8073:  // MAX_3D_TEXTURE_SIZE
    case 0x80E8:  // MAX_ELEMENTS_VERTICES
    case 0x80E9:  // MAX_ELEMENTS_INDICES
    case 0x84FD:  // MAX_TEXTURE_LOD_BIAS
    case 0x88FF:  // MAX_ARRAY_TEXTURE_LAYERS
    case 0x8904:  // MIN_PROGRAM_TEXEL_OFFSET
    case 0x8905:  // MAX_PROGRAM_TEXEL_OFFSET
    case 0x8A2B:  // MAX_VERTEX_UNIFORM_BLOCKS
    case 0x8A2D:  // MAX_FRAGMENT_UNIFORM_BLOCKS
    case 0x8A2E:  // MAX_COMBINED_UNIFORM_BLOCKS
    case 0x8A2F:  // MAX_UNIFORM_BUFFER_BINDINGS
    case 0x8A30:  // MAX_UNIFORM_BLOCK_SIZE
    case 0x8A31:  // MAX_COMBINED_VERTEX_UNIFORM_COMPONENTS
    case 0x8A33:  // MAX_COMBINED_FRAGMENT_UNIFORM_COMPONENTS
    case 0x8A34:  // UNIFORM_BUFFER_OFFSET_ALIGNMENT
    case 0x8B49:  // MAX_FRAGMENT_UNIFORM_COMPONENTS
    case 0x8B4A:  // MAX_VERTEX_UNIFORM_COMPONENTS
    case 0x8B4B:  // MAX_VARYING_COMPONENTS
    case 0x8C80:  // MAX_TRANSFORM_FEEDBACK_SEPARATE_COMPONENTS
    case 0x8C8A:  // MAX_TRANSFORM_FEEDBACK_INTERLEAVED_COMPONENTS
    case 0x8C8B:  // MAX_TRANSFORM_FEEDBACK_SEPARATE_ATTRIBS
    case 0x8D57:  // MAX_SAMPLES
    case 0x8D6B:  // MAX_ELEMENT_INDEX
    case 0x9111:  // MAX_SERVER_WAIT_TIMEOUT
    case 0x9122:  // MAX_VERTEX_OUTPUT_COMPONENTS
    case 0x9125:  // MAX_FRAGMENT_INPUT_COMPONENTS
      return true;
    default:
      return false;
  }
}

inline const char* ParamsDomain(bool isWebGL2) {
  return isWebGL2 ? "webGl2:parameters" : "webGl:parameters";
}

/**
 * The spoofed value of a constant getParameter() name, or nullopt to let
 * Firefox answer. A stored null also falls through: it was scraped from a
 * context that had not enabled the extension the name belongs to, so it is
 * not the GPU's value.
 */
inline std::optional<std::variant<int64_t, bool, double, std::string>>
GLParamIn(const nlohmann::json& root, uint32_t pname, bool isWebGL2) {
  if (!IsGLConstantParam(pname, isWebGL2)) return std::nullopt;
  const auto* data =
      GetNestedIn(root, ParamsDomain(isWebGL2), std::to_string(pname));
  if (!data || data->is_null()) return std::nullopt;
  if (data->is_number_integer()) return data->get<int64_t>();
  if (data->is_boolean()) return data->get<bool>();
  if (data->is_number_float()) return data->get<double>();
  if (data->is_string()) return data->get<std::string>();
  return std::nullopt;
}

/** A constant array parameter (MAX_VIEWPORT_DIMS, ALIASED_*_RANGE). */
template <typename T>
inline T MParamGLIn(const nlohmann::json& root, uint32_t pname,
                    T defaultValue, bool isWebGL2) {
  if (!IsGLConstantParam(pname, isWebGL2)) return defaultValue;
  const auto* data =
      GetNestedIn(root, ParamsDomain(isWebGL2), std::to_string(pname));
  if (!data || !data->is_array() ||
      data->size() != std::tuple_size<T>::value) {
    return defaultValue;
  }
  return data->get<T>();
}

/** "webGl:parameters:blockIfNotDefined": null for constants the table lacks. */
inline bool BlockUndefinedParamIn(const nlohmann::json& root, uint32_t pname,
                                  bool isWebGL2) {
  if (!IsGLConstantParam(pname, isWebGL2)) return false;
  auto it = root.find(isWebGL2 ? "webGl2:parameters:blockIfNotDefined"
                               : "webGl:parameters:blockIfNotDefined");
  if (it == root.end() || !it->is_boolean() || !it->get<bool>()) return false;
  const auto* data =
      GetNestedIn(root, ParamsDomain(isWebGL2), std::to_string(pname));
  return !data;
}

inline std::optional<std::array<int32_t, 3UL>> MShaderDataIn(
    const nlohmann::json& root, uint32_t shaderType, uint32_t precisionType,
    bool isWebGL2) {
  std::string valueName =
      std::to_string(shaderType) + "," + std::to_string(precisionType);
  const auto* data = GetNestedIn(root,
                                 isWebGL2 ? "webGl2:shaderPrecisionFormats"
                                          : "webGl:shaderPrecisionFormats",
                                 valueName);
  if (!data || !data->is_object() || !data->contains("rangeMin") ||
      !data->contains("rangeMax") || !data->contains("precision")) {
    return std::nullopt;
  }
  return std::array<int32_t, 3U>{(*data)["rangeMin"].get<int32_t>(),
                                 (*data)["rangeMax"].get<int32_t>(),
                                 (*data)["precision"].get<int32_t>()};
}

inline bool CheckBoolIn(const nlohmann::json& root, const std::string& key) {
  auto it = root.find(key);
  return it != root.end() && it->is_boolean() && it->get<bool>();
}

inline std::optional<std::string> GetStringIn(const nlohmann::json& root,
                                              const std::string& key) {
  auto it = root.find(key);
  if (it == root.end() || !it->is_string()) return std::nullopt;
  return it->get<std::string>();
}

inline std::vector<std::string> GetStringListIn(const nlohmann::json& root,
                                                const std::string& key) {
  std::vector<std::string> result;
  auto it = root.find(key);
  if (it == root.end() || !it->is_array()) return result;
  for (const auto& item : *it) {
    if (item.is_string()) result.push_back(item.get<std::string>());
  }
  return result;
}

inline std::optional<
    std::vector<std::tuple<std::string, std::string, std::string, bool, bool>>>
MVoices() {
  auto data = GetJson();
  if (!data.contains("voices") || !data["voices"].is_array()) {
    return std::nullopt;
  }

  std::vector<std::tuple<std::string, std::string, std::string, bool, bool>>
      voices;
  for (const auto& voice : data["voices"]) {
    // Each voice must be a full object with all five fields. A bare string
    // (e.g. "Name:lang:type") or an object missing a field registers NOTHING
    // and would silently leave the host's native voices exposed, so warn
    // loudly instead of dropping it quietly.
    if (!voice.is_object() || !voice.contains("lang") ||
        !voice.contains("name") || !voice.contains("voiceUri") ||
        !voice.contains("isDefault") || !voice.contains("isLocalService")) {
      printf_stderr(
          "ERROR: 'voices' entry is not a complete object "
          "{lang,name,voiceUri,isDefault,isLocalService}; skipping: %s\n",
          voice.dump().c_str());
      continue;
    }

    voices.emplace_back(
        voice["lang"].get<std::string>(), voice["name"].get<std::string>(),
        voice["voiceUri"].get<std::string>(), voice["isDefault"].get<bool>(),
        voice["isLocalService"].get<bool>());
  }
  return voices;
}

}  // namespace MaskConfig