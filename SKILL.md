---
name: clouddrive2
description: "CloudDrive2 云盘管理: 通过本机 gRPC (127.0.0.1:19798) 列出/搜索/移动/复制/删除云盘文件, 创建文件夹, 加密, 离线下载, 获取下载链接, 管理挂载点/云盘/备份/传输任务/API令牌/账户. 触发词: 115, 百度网盘, 迅雷, 云盘, CloudDrive, CD2, 离线下载, 转存, 刮削, 挂载, strm."
metadata:
  trigger_phrases: ["115", "百度网盘", "迅雷云盘", "CloudDrive", "CD2", "云盘文件", "转存", "离线下载", "云盘挂载"]
---

# CloudDrive2 云盘管理

通过本机 CloudDrive2 gRPC 服务执行云盘(115/百度网盘/迅雷/本地目录)的文件操作与系统管理。服务地址 `127.0.0.1:19798`,已登录,认证自动完成。

## 前置

- 服务常驻运行,无需启动
- 认证自动:优先 `CD2_TOKEN`/`CLOUDDRIVEAPI` 环境变量 → 本机 `/opt/clouddrive2/config/api_tokens.json` 里的 API token → `GET token.txt`。一般直接可用。
- 工具目录: `/root/.openclaw/workspace/skills/clouddrive2/`
  - `cd2.py` — 通用调用器,支持**全部 227 个 RPC**
  - `cd2cmd.py` — 常用命令便捷封装
  - `gen/proto/` — 生成的 Python gRPC 客户端(勿动)
  - `clouddrive.proto` — 最新 proto 定义(1.0.17)

## 用法

### A. 快捷命令(cd2cmd.py,高频操作首选)

```bash
cd /root/.openclaw/workspace/skills/clouddrive2
python3 cd2cmd.py ls /115open            # 列目录: 名称<TAB>大小<TAB>类型
python3 cd2cmd.py ls / --refresh         # 强制刷新缓存
python3 cd2cmd.py find /115open 电影     # 模糊搜索
python3 cd2cmd.py info /115open/xx.mkv   # 文件详情
python3 cd2cmd.py mkdir /115open 新文件夹
python3 cd2cmd.py mv /115open/a.mkv /115open/电影        # 移动(默认 rename 冲突)
python3 cd2cmd.py cp /115open/a.mkv /115open/备份 --policy overwrite
python3 cd2cmd.py rm /115open/垃圾.mkv                    # 删除(回收站)
python3 cd2cmd.py rmperm /115open/垃圾.mkv                # 永久删除
python3 cd2cmd.py rn /115open/a.mkv b.mkv                # 重命名
python3 cd2cmd.py dl /115open/a.mkv                      # 下载URL + curl/wget
python3 cd2cmd.py mounts | clouds | copy-tasks           # 挂载点/云盘/复制任务
python3 cd2cmd.py dl-tasks | ul-tasks                    # 下载/上传队列
python3 cd2cmd.py backups                                # 备份状态
python3 cd2cmd.py settings | acct | tokens               # 设置/账户/令牌
python3 cd2cmd.py token-create 名字 --perm allow_read allow_list
python3 cd2cmd.py unlock /path 密码 [--permanent]
python3 cd2cmd.py stat                                   # 系统信息
```

### B. 任意 RPC(cd2.py)—— 文档全部功能

```bash
python3 cd2.py <RpcName> [field=value ...] [--json|--raw|--no-auth|--list]
```

- 字段名用 camelCase(与文档一致),嵌套用 `.`: `subField=value`
- 值按 JSON 解析(数字/bool/数组自动转换),流式方法自动聚合
- `--list` 打印全部 227 个方法名
- 常用例子:

```bash
python3 cd2.py GetSubFiles path=/115open forceRefresh=true
python3 cd2.py FindFileByPath parentPath=/115open path=/115open/电影/xxx.mkv
python3 cd2.py GetSearchResults searchFor=电影 path=/ fuzzyMatch=true
python3 cd2.py CreateFolder parentPath=/115open folderName=新目录
python3 cd2.py RenameFile theFilePath=/115open/a.mkv newName=b.mkv
python3 cd2.py MoveFile theFilePaths=/115open/a.mkv destPath=/115open/电影 conflictPolicy=Rename
python3 cd2.py CopyFile theFilePaths=/115open/a.mkv destPath=/115open/备份 conflictPolicy=Skip
python3 cd2.py DeleteFiles path=/115open/垃圾1.mkv path=/115open/垃圾2.mkv
python3 cd2.py GetMountPoints
python3 cd2.py GetAllCloudApis
python3 cd2.py GetDownloadUrlPath path=/115open/a.mkv preview=false
python3 cd2.py GetCopyTasks
python3 cd2.py BackupGetAll
python3 cd2.py GetAccountStatus
python3 cd2.py ListTokens
python3 cd2.py GetSystemSettings
```

## 关键概念

- **路径前缀**(云盘根): `/115open`、`/百度网盘`、`/迅雷云盘`、`/mnt`(本地盘)、`/CD2`(本地 /ssd/CD2)
- **冲突策略** `conflictPolicy`: `Overwrite=0 / Rename=1 / Skip=2`
- **云盘列表**: `python3 cd2.py GetAllCloudApis`
- **挂载点**: 默认只有 `/mnt/115open` 挂载,`GetMountPoints` 查看
- **删除** 默认进回收站; `DeleteFilesPermanently`/`rmperm` 才是永久删除
- **下载**: `GetDownloadUrlPath` 返回拼接好的 URL(`directUrl` 优先,否则 `downloadUrlPath` 含 `{SCHEME}/{HOST}/{PREVIEW}` 占位符需替换); 地址用 `127.0.0.1:19798`
- **大文件操作**(移动/复制跨云盘)会生成后台任务,用 `GetCopyTasks` 跟踪

## 认证故障排查

- `No valid auth token` → 证书文件/环境缺失,检查 `api_tokens.json` 是否存在
- 2FA 提示 → 需 `CD2_TOTP` 或改用 API token
- 公共方法(GetSystemInfo/GetToken/Login 等)不需要认证,加 `--no-auth`

## 验证

每执行一个操作后,应通过结果字段确认成功:
- 创建/移动/复制/删除 → 返回 `FileOperationResult` 含 `success/errorMessage`
- 列表/搜索 → 检查返回条目数与目标
- 若操作失败,先看 `errorMessage`,再决定重试或查任务状态