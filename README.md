# CloudDrive2 Skill / gRPC 客户端

基于 [CloudDrive2](https://www.clouddrive2.com/) 官方 gRPC API(proto v1.0.17)编写的 OpenClaw/Agent skill,覆盖 **全部 227 个 RPC**(文档所有功能),支持通过自然语言命令操作 115 / 百度网盘 / 迅雷云盘 / 本地目录等云盘资源。

## 功能

- 📁 文件操作: 列出/搜索/查找/创建文件夹/重命名/移动/复制/删除/永久删除
- 🔐 加密文件夹: 创建加密目录/解锁/锁定
- 🧲 离线下载: 添加离线任务/列表/清理(Aria2/magnet 等)
- ⬇️ 下载链接: 获取直链 + 自动生成 curl/wget 命令
- 🗂️ 挂载点管理: 查看/添加/卸载挂载点
- ☁️ 云盘管理: 5+ 云盘集成、二维码/扫码登录、WebDAV/S3/SFTP/FTP/SMB
- 📊 传输任务: 下载/上传队列、复制任务(转存)、暂停/恢复/取消
- 💾 备份管理: 备份列表/状态/增改
- 🔑 令牌与账户: API 令牌管理、账户状态、2FA、会话管理

## 文件

| 文件 | 说明 |
|---|---|
| `cd2.py` | **通用调用器** — 覆盖全部 227 个 RPC, `python3 cd2.py <RpcName> field=value ...` |
| `cd2cmd.py` | **快捷命令** — 22 个高频操作一行搞定 (ls/find/mv/cp/rm/dl/mounts/clouds/backups...) |
| `SKILL.md` | skill 说明与使用手册(触发词: 115/百度/迅雷/CD2/转存/离线下载...) |
| `clouddrive.proto` | 官方 proto v1.0.17 |
| `gen/proto/` | 生成的 Python gRPC 客户端代码 |

## 快速开始

```bash
# 依赖
pip install grpcio grpcio-tools protobuf

# 列目录(自动读取本机 api_tokens.json 认证)
python3 cd2.py GetSubFiles path=/115open

# 快捷命令
python3 cd2cmd.py ls /115open
python3 cd2cmd.py find /115open 电影
python3 cd2cmd.py dl /115open/xx.mkv
python3 cd2cmd.py clouds
python3 cd2cmd.py stat
```

## 认证

凭据**绝不硬编码**。按优先级自动读取:

1. 环境变量 `CD2_TOKEN`(或 OpenClaw 后台 secret `CLOUDDRIVEAPI`)
2. 本机 `/opt/clouddrive2/config/api_tokens.json`(CloudDrive2 原生 API token)
3. 本目录 `token.txt`(登录后自动缓存)

需要账号密码登录时设置 `CD2_USER`/`CD2_PASS`(2FA 启用时加 `CD2_TOTP`)。

## 使用技巧

- 任意 RPC: `python3 cd2.py <方法名> [字段=值 ...]`, `--list` 列出全部, `--json` 输出 JSON
- 冲突策略: `conflictPolicy=Overwrite|Rename|Skip`
- 流式方法(GetSubFiles/GetSearchResults 等)自动聚合
- 字段用 camelCase,值按 JSON 解析,逗号分隔自动成数组

详情见 `SKILL.md`。
