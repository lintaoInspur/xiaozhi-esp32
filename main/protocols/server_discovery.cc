#include "server_discovery.h"

#include <esp_err.h>
#include <lwip/ip4_addr.h>
#include <mdns.h>

#include <cstring>
#include <string>

static const char* TAG = "MDNS-DISC";

// mDNS 服务类型（与服务端广播的类型必须一致）
static const char* kServiceName = "_xiaozhi";
static const char* kServiceProto = "_tcp";
// 查询超时（毫秒）
static constexpr uint32_t kQueryTimeoutMs = 2000;
// 默认 websocket 路径
static const char* kDefaultPath = "/ws";

static bool s_mdns_inited = false;
static bool s_cached_valid = false;
static std::string s_host;
static uint16_t s_port = 0;
static std::string s_txt_path = kDefaultPath;

namespace xiaozhi {

static void EnsureMdns() {
  if (s_mdns_inited) {
    return;
  }
  esp_err_t err = mdns_init();
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "mdns_init failed: %s", esp_err_to_name(err));
    return;
  }
  mdns_hostname_set("xiaozhi-seeker");
  mdns_instance_name_set("Xiaozhi Seeker");
  s_mdns_inited = true;
}

bool GetCachedServer(std::string& host, uint16_t& port, std::string& txt_path) {
  if (!s_cached_valid) {
    return false;
  }
  host = s_host;
  port = s_port;
  txt_path = s_txt_path;
  return true;
}

bool DiscoverXiaozhiServer(std::string& host, uint16_t& port, std::string& txt_path) {
  EnsureMdns();
  if (!s_mdns_inited) {
    return GetCachedServer(host, port, txt_path);
  }

  mdns_result_t* results = nullptr;
  esp_err_t err = mdns_query(kServiceName, kServiceProto, kQueryTimeoutMs, &results);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "mdns_query failed: %s", esp_err_to_name(err));
    return GetCachedServer(host, port, txt_path);
  }
  if (results == nullptr) {
    ESP_LOGW(TAG, "no xiaozhi server found via mDNS on this LAN");
    return GetCachedServer(host, port, txt_path);
  }

  bool found = false;
  for (mdns_result_t* r = results; r != nullptr; r = r->next) {
    if (r->addr.type != MDNS_IP_PROTOCOL_V4 || r->port == 0) {
      continue;  // 仅使用 IPv4；无端口的实例跳过
    }
    char ip_str[IP4ADDR_STRLEN_MAX] = {0};
    ip4addr_ntoa_r(&r->addr.ip4, ip_str, sizeof(ip_str));

    // 解析 TXT 中的 path（默认 /ws）
    std::string path = kDefaultPath;
    for (size_t i = 0; i < r->txt_count; ++i) {
      if (r->txt[i].key != nullptr && std::strcmp(r->txt[i].key, "path") == 0 &&
          r->txt[i].value != nullptr) {
        path = r->txt[i].value;
        if (path.empty() || path[0] != '/') {
          path = "/" + path;
        }
      }
    }

    s_host = ip_str;
    s_port = r->port;
    s_txt_path = path;
    s_cached_valid = true;
    found = true;
    ESP_LOGI(TAG, "discovered xiaozhi server: http://%s:%u%s", ip_str, r->port, path.c_str());
    break;
  }

  mdns_query_results_free(results);

  if (!found) {
    ESP_LOGW(TAG, "mdns query returned no usable xiaozhi instance");
    return GetCachedServer(host, port, txt_path);
  }

  host = s_host;
  port = s_port;
  txt_path = s_txt_path;
  return true;
}

}  // namespace xiaozhi
