#pragma once

#include <cstdint>
#include <string>

namespace xiaozhi {

// 通过 mDNS (DNS-SD) 在局域网内自动发现小智服务端，实现"换网不重配"。
//
// 服务端（盒子）会广播 _xiaozhi._tcp.local 服务，TXT 中携带 path（默认 /ws）。
// 设备联网后调用 DiscoverXiaozhiServer() 即可拿到服务端的 host/port，
// 由调用方拼出 ws / http 地址，无需在配网页手动填写 IP。
//
// 说明：
//   - host     : 服务端的局域网 IPv4 地址，如 "192.168.1.20"
//   - port     : websocket / http 共用端口，如 8800
//   - txt_path : mDNS TXT 中的 websocket 路径（默认 "/ws"）

// 发起一次 mDNS 查询并缓存结果。成功发现返回 true，并通过出参回填地址。
bool DiscoverXiaozhiServer(std::string& host, uint16_t& port, std::string& txt_path);

// 仅返回已缓存的发现结果（不发起新查询）。供 OTA 在没有先连 ws 时复用。
bool GetCachedServer(std::string& host, uint16_t& port, std::string& txt_path);

}  // namespace xiaozhi
