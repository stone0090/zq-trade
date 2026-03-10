# 知识星球 (zsxq.com) 数据访问调研报告

调研时间: 2026-03

## 结论

知识星球 **没有官方公开 API**。但其 Web 版 (`wx.zsxq.com`) 是 SPA 单页应用，所有数据通过 `api.zsxq.com` 的 REST 接口获取 JSON，社区已逆向出一套较完整的 API 体系，可以通过模拟登录后直接调用这些接口获取数据。

---

## 一、官方 API 情况

- 不存在 `open.zsxq.com` 或类似的开发者门户
- 不存在官方 API 文档、SDK 或第三方集成支持
- 不存在 OAuth 授权、Webhook 等开放能力
- 不存在官方数据导出功能

知识星球是封闭的知识付费社区，商业模式依赖内容独占性，刻意不提供开放接口。

---

## 二、逆向 API 体系

### 2.1 基础架构

| 项目 | 详情 |
|------|------|
| 基础域名 | `https://api.zsxq.com` |
| API 版本 | `v1` / `v1.2` / `v1.10` / `v2` / `v3` (并存) |
| 数据格式 | JSON |
| 网页入口 | `https://wx.zsxq.com` (SPA) |

### 2.2 认证机制

使用请求头 `Authorization` 字段:

```http
Authorization: Bearer xxxxxxxxxxxxxxxx
```

**Token 获取方法:**
1. 浏览器打开 `https://wx.zsxq.com` 并登录 (微信扫码)
2. F12 -> Network 面板
3. 找到发往 `api.zsxq.com` 的请求
4. 复制 `Authorization` 值 (即 `zsxq_access_token`)
5. 同时复制 `User-Agent` (需保持一致)

**Token 有效期:** 通常 1~3 个月，过期后需重新从浏览器获取。

### 2.3 签名验证 (部分版本需要)

较新版本增加了签名校验:

| 请求头 | 说明 |
|--------|------|
| `X-Signature` | MD5 签名值 (32位小写) |
| `X-Timestamp` | 13位毫秒级时间戳 |

**签名算法:**
1. 准备参数: `app_version`, `platform`, `timestamp` + 业务参数
2. 按键名升序排列，拼接为 `key1=value1&key2=value2`
3. 构建待签名字符串: `{path}&{sorted_params}&zsxqapi2020`
4. MD5 加密得到签名

### 2.4 API 端点

**星球管理**

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/v2/groups` | 获取我的星球列表 |
| GET | `/v2/groups/{group_id}` | 星球详情 |
| GET | `/v2/groups/{group_id}/statistics` | 星球统计数据 |
| GET | `/v2/groups/{group_id}/hashtags` | 星球标签列表 |

**话题/帖子**

| 方法 | 端点 | 功能 | 参数 |
|------|------|------|------|
| GET | `/v1.10/groups/{group_id}/topics` | 话题列表 | `count`, `end_time`, `scope`(all/digests/questions) |
| GET | `/v1.10/groups/{group_id}/topics?scope=digests` | 精华帖 | `count=20` |
| GET | `/v1/topics/{topic_id}` | 话题详情 | - |
| GET | `/v1/topics/{topic_id}/comments` | 话题评论 | `count`, `end_time` |
| POST | `/v1/topics/{topic_id}/likes` | 点赞 | - |

**用户**

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/v3/users/self` | 当前登录用户信息 |
| GET | `/v3/users/{user_id}` | 指定用户信息 |

### 2.5 响应格式

```json
{
  "succeeded": true,
  "resp_data": {
    "topics": [...],
    "end_time": "2024-01-01T00:00:00.000+0800"
  }
}
```

### 2.6 分页机制

采用 **游标式分页** (Cursor-based):
- 首次请求不传 `end_time`
- 响应返回 `resp_data.end_time`
- 下一页请求传入该值作为 `end_time` 参数
- 直到返回空列表为止

---

## 三、数据访问方案

### 方案 A: 直接调用 API (推荐)

```python
import requests
import time

BASE_URL = "https://api.zsxq.com"
HEADERS = {
    "Authorization": "Bearer YOUR_TOKEN_HERE",
    "User-Agent": "YOUR_BROWSER_UA",
    "Origin": "https://wx.zsxq.com",
    "Referer": "https://wx.zsxq.com/",
    "Accept": "application/json"
}

def get_topics(group_id, count=20, end_time=None):
    url = f"{BASE_URL}/v1.10/groups/{group_id}/topics"
    params = {"scope": "all", "count": count}
    if end_time:
        params["end_time"] = end_time
    resp = requests.get(url, headers=HEADERS, params=params)
    return resp.json()

# 分页遍历
end_time = None
while True:
    data = get_topics("YOUR_GROUP_ID", end_time=end_time)
    if not data.get("succeeded") or not data["resp_data"].get("topics"):
        break
    for topic in data["resp_data"]["topics"]:
        print(topic.get("talk", {}).get("text", ""))
    end_time = data["resp_data"].get("end_time")
    time.sleep(2)  # 防限流
```

- 优点: 速度快、数据结构化、实现简单
- 缺点: Token 需手动获取，1-3个月过期

### 方案 B: 浏览器自动化 (Playwright/Puppeteer)

- 使用无头浏览器模拟完整登录流程
- 拦截 Network 请求自动捕获 API 调用
- 优点: 可自动化 Token 获取
- 缺点: 资源消耗大，速度慢

### 方案 C: 浏览器扩展

- 开发 Chrome 扩展，在已登录页面上下文中读取数据
- 优点: 最自然的访问方式
- 缺点: 需用户手动安装

---

## 四、反爬虫措施与应对

| 措施 | 详情 | 应对 |
|------|------|------|
| 签名校验 | 部分版本要求 X-Signature + X-Timestamp | 按算法生成 (密钥 `zsxqapi2020`) |
| 频率限制 | 高频请求被限流 | `time.sleep(2-3)`, 每次 count=20 |
| IP 封禁 | 持续高频可能封 IP | 代理 IP 池 |
| Token 时效 | 1-3 个月过期 | 定期手动刷新 |
| UA 校验 | 需与登录时一致 | 保持 UA 不变 |
| Referer/Origin | 需正确来源头 | 设置 `Origin: https://wx.zsxq.com` |

---

## 五、GitHub 开源项目

| 项目 | 语言 | 功能 |
|------|------|------|
| [zsxq-sdk](https://github.com/yiancode/zsxq-sdk) | TS/Java/Go/Python | 最全面的多语言 SDK, 覆盖 50+ 端点 |
| [zsxq-spider](https://github.com/PlexPt/zsxq-spider) | Java | 下载文件区+文章内容 |
| [zsxq-crawler-pro](https://github.com/Anionex/zsxq-crawler-pro) | Python | 批量爬取 + 生成 PDF 电子书 |

---

## 六、法律/合规风险

| 风险等级 | 场景 |
|---------|------|
| **低** | 爬取自己已付费加入的星球, 仅供个人使用 |
| **中** | 批量爬取用于数据分析/研究, 不传播原始内容 |
| **高** | 爬取付费内容后公开传播/转售 |
| **红线** | 数据用于商业用途、传播他人付费内容 |

知识星球用户协议未明确禁止自动化访问，但明确禁止"利用技术手段批量创建虚假账号"，内容版权归创作者所有。

---

## 七、ZQ-Trade 集成建议

如果要从知识星球获取交易相关信息 (如大V的持仓分享、交易策略等):

1. **推荐方案 A (直接调用API)**: 最简单高效
2. 手动获取 Token 后配置到项目中
3. 按 group_id 定时拉取指定星球的新帖子
4. 解析帖子内容中的股票代码、交易信号等信息
5. 注意控制请求频率 (2-3秒间隔), Token 过期需手动更新
6. 仅用于个人投资参考，不传播原始内容
