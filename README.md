# JMComic 禁漫漫画下载插件

AstrBot 的禁漫漫画下载插件，支持下载漫画并自动转换为 PDF 格式。

## 功能特性

- 🎨 下载禁漫漫画并转换为 PDF
- 📦 自动上传到群文件或私聊发送
- 🔄 自动域名测试和更新
- 🗑️ 自动清理过期文件
- 🌐 支持代理配置
- 📊 PDF 压缩优化
- ⚡ 自动检测并安装缺失的依赖

## 安装

1. 将插件文件夹放入 AstrBot 的 `plugins` 目录
2. 插件会在首次加载时自动检测并安装依赖
3. 如果自动安装失败，可以手动安装依赖：
```bash
pip install -r requirements.txt
```
或
```bash
pip install pillow pyyaml img2pdf jmcomic
```

## 配置说明

在 AstrBot 的插件配置界面中可以配置以下参数：

### 代理与客户端
- **use_proxy**: 是否使用代理（默认：true）
- **proxy_address**: 代理地址（默认：http://127.0.0.1:7890；Docker 内自动改为 host.docker.internal）
- **client_impl**: 客户端类型，`api`（推荐）或 `html`（默认：api）
- **auto_update_jmcomic**: 启动时自动升级 jmcomic 库（默认：true）

### 域名设置
- **custom_domains**: 自定义域名，仅 `client_impl=html` 时生效

### 下载与预览
- **download_path**: 下载保存路径（默认：./downloads）
- **cleanup_days**: 文件保留天数（默认：3天）
- **send_album_preview**: 下载/查询时发送封面和简介（默认：true）
- **preview_card**: 用 AstrBot HtmlRenderer 合成封面+简介为一张预览卡（默认：true；失败自动回退分发）
- **search_max_results**: 搜索每页展示条数（默认：10）
- **filter_r18g**: 过滤 R-18G 内容（默认：true）
- **max_download_pages**: 最大下载页数，超过则拒绝下载（默认：100，设为 0 不限制）
- **upload_path_map**: Docker→NapCat 可见路径映射（NapCat 在虚拟机时填虚拟机内共享目录，例如 `/AstrBot/data=/mnt/shared/main_bot/data`）
- **max_base64_upload_mb**: 路径上传失败时允许 base64 的最大文件 MB（默认：8，过大易超时）
- **jmcomic_log_level**: 底层日志级别 off / summary / full（默认：off）

### PDF 设置
- **compress_quality**: PDF 压缩质量 1-100（默认：85）
- **max_image_dimension**: 图片最大尺寸（默认：1200px）

### 网络设置
- **timeout**: 请求超时时间（默认：10秒）
- **retry_times**: 重试次数（默认：10次）

## 使用方法

### 下载漫画
```
jm <漫画ID>
jm123456
JM123456
jm download <漫画ID>
```
例如：`jm 1224351`、`jm1224351`、`JM1224351` 或 `jm download 1224351`

### 查看详情（不下载）
```
jm info <漫画ID>
```
默认会用 AstrBot HtmlRenderer 把封面和简介合成一张预览卡；若渲染失败则回退为文字+封面分发。

### 搜索本子
```
jm search tag <标签> [页码]
jm search title <标题关键词> [页码]
```
示例：
- `jm search tag 无修正` — 按标签搜索
- `jm search title 姐姐 2` — 按标题搜索第 2 页

搜索到 ID 后可用 `jm <ID>` 下载。

### 更新 jmcomic 库
```
jm update
```

### 测试域名
```
jm domains
```
自动测试所有可用域名并更新配置

### 手动清理
```
jm cleanup
```
手动清理过期的下载文件

## 注意事项

1. **代理配置**：由于禁漫网站可能需要科学上网，建议配置代理
2. **Docker / 虚拟机**：若 AstrBot 与 NapCat 不在同一文件系统（例如 AstrBot 在 Docker、NapCat 在虚拟机），**必须配置 `upload_path_map`**，右侧填 NapCat 进程能打开的路径；否则大文件只能走 base64 且容易 WebSocket 超时
3. **文件大小**：PDF 文件可能较大，群文件上传可能受限
4. **自动清理**：每天凌晨 2 点自动清理超过指定天数的文件
5. **R-18G 内容**：默认会自动过滤，可在配置中关闭 `filter_r18g`

## 常见问题

### Q: 插件加载时提示缺少依赖
A: 插件会自动尝试安装依赖。如果自动安装失败，请手动运行 `pip install -r requirements.txt`

### Q: 下载失败，提示"访问被拒绝"
A: 请确保已启用代理配置，并且代理服务正常运行

### Q: 群文件上传失败 / base64 超时
A: 常见于 AstrBot 与 NapCat 不在同一文件系统（Docker / 虚拟机分离）：协议端读不到容器内路径，大文件 base64 又会 WebSocket 超时。  
请配置 `upload_path_map`：左侧是 AstrBot 容器路径，右侧是 **NapCat 里能 `ls`/`open` 到的路径**（虚拟机就填虚拟机内共享目录）：

```
/AstrBot/data=/mnt/shared/main_bot/data
```

没有共享目录的话，路径上传做不到，只能缩小 PDF（降低压缩质量/分辨率）或提高协议端超时，否则大文件会一直失败。

### Q: 域名测试失败
A: 请检查网络连接和代理配置，或手动更新 custom_domains 配置

## 更新日志

### v1.0.6
- 用 AstrBot HtmlRenderer 合成封面+简介预览卡（`preview_card`）
- 自动裁剪 t2i 黑白画布留白
- 预览卡改用第一章首页高清图（albums CDN 仅约 400px）

### v1.0.5
- 新增 `upload_path_map`：将 Docker 内路径映射为 NapCat 可见路径，优先本地路径上传
- 新增 `max_base64_upload_mb`：大文件跳过 base64，避免 WebSocket 超时
- 适配 AstrBot(Docker) + NapCat(虚拟机/异机) 部署
- 用 AstrBot HtmlRenderer 合成封面+简介预览卡（`preview_card`，失败回退分发）

### v1.0.4
- 升级 jmcomic 依赖至 2.7.5，同步域名获取方式
- 支持 `jm123456` / `JM123456` 无空格与大小写指令
- 新增 `max_download_pages`，超过页数上限拒绝下载

### v1.0.3
- 默认过滤 R-18G 内容（搜索、详情、下载）
- 可配置 `filter_r18g` 开关

### v1.0.2
- 新增 `jm search tag` / `jm search title` 搜索功能
- 可配置 `search_max_results` 控制每页展示条数

### v1.0.1
- 新增 `jm info` 仅查看详情（不下载）
- 支持从混合文本提取漫画 ID
- API 客户端页数修正、3:4 封面比例
- 简介/封面分开发送，封面失败自动旋转重试
- 切换 API 客户端，Docker 代理与 jmcomic 自动更新
- 预览与查询走线程池，避免阻塞 bot

### v1.0.0
- 初始版本
- 支持漫画下载和 PDF 转换
- 支持配置化管理
- 自动清理功能

## 许可证

MIT License

## 作者

Origami
