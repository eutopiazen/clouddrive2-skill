#!/usr/bin/env python3
"""CloudDrive2 gRPC 通用调用器 — 覆盖全部 227 个 RPC。

用法:
  python3 cd2.py <RpcName> [field=value ...] [--json] [--no-auth]

参数解析:
  - field 名用 camelCase(与 proto 一致),如 parentPath=/media
  - 值按 JSON 解析: 数字/int/bool/数组; 其余按字符串
  - 嵌套 message 用点号: sub.desc=hello 会设置 sub 字段的 desc
  - 流式方法自动聚合所有响应块

认证:
  - 优先用环境变量 CD2_TOKEN 或文件 token.txt(本目录)
  - 否则用 CD2_USER/CD2_PASS 调 GetToken(2FA 时需 CD2_TOTP)
  - token 会缓存到 token.txt(0600)

示例:
  python3 cd2.py GetSubFiles path=/115open
  python3 cd2.py GetSubFiles path=/ forceRefresh=true
  python3 cd2.py FindFileByPath parentPath=/115open path=/115open/电影
  python3 cd2.py GetMountPoints
  python3 cd2.py GetDownloadUrlPath path=/115open/abc.mkv preview=false
  python3 cd2.py ListTokens
"""
import argparse
import json
import os
import re
import sys

import grpc

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "gen", "proto"))

import clouddrive_pb2 as pb  # noqa: E402
import clouddrive_pb2_grpc as pb_grpc  # noqa: E402

SERVER = os.environ.get("CD2_SERVER", "127.0.0.1:19798")
TOKEN_FILE = os.path.join(BASE, "token.txt")

# 不需要鉴权的公共方法
PUBLIC_METHODS = {
    "GetSystemInfo", "GetToken", "Login", "LoginWithThirdPartyAccount",
    "Register", "SendResetAccountEmail", "ResetAccount", "GetApiTokenInfo",
    "LoginWith2FA", "SendDisable2FAEmail", "Disable2FAByEmail",
}

# 流式响应方法名(服务端流)
STREAM_METHODS = {
    "GetSubFiles", "GetSearchResults", "PushMessage", "PushTaskChange",
    "LocalGetSubFiles", "APILogin115QRCode", "APILogin115OpenQRCode",
    "APILoginAliyunDriveQRCode", "APILogin189QRCode",
    "APILoginGuangYaPanQRCode", "RemoteUploadChannel",
}


def get_stub():
    channel = grpc.insecure_channel(SERVER)
    return pb_grpc.CloudDriveFileSrvStub(channel), channel


# CloudDrive2 API token 存储位置(1Panel/Web 创建的 token 明文就在这里)
API_TOKENS_FILE = "/opt/clouddrive2/config/api_tokens.json"
API_TOKENS_LOCAL = "/opt/clouddrive2/api_tokens.json"


def load_token():
    # 1) 显式环境变量
    t = os.environ.get("CD2_TOKEN")
    if t:
        return t.strip()
    # 2) OpenClaw 后台 secret 注入的环境变量(CLOUDDRIVEAPI)
    t = os.environ.get("CLOUDDRIVEAPI")
    if t:
        return t.strip()
    # 3) 本机 CloudDrive2 API token 文件的第一把 token(UUID)
    for f in (API_TOKENS_FILE, API_TOKENS_LOCAL):
        try:
            data = json.load(open(f))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data:
            # 优先选 root_dir=/ 且权限最全的 token
            best = None
            for uuid_, meta in data.items():
                if not isinstance(meta, dict):
                    continue
                perms = meta.get("permissions", {})
                allow = sum(1 for v in perms.values() if v is True)
                score = (meta.get("root_dir", "/") == "/", allow)
                if best is None or score > best[0]:
                    best = (score, uuid_)
            if best:
                return best[1]
    # 4) 本地 token 文件
    if os.path.exists(TOKEN_FILE):
        try:
            t = open(TOKEN_FILE).read().strip()
            if t:
                return t
        except OSError:
            pass
    return None


def save_token(t):
    try:
        with open(TOKEN_FILE, "w") as f:
            f.write(t)
        os.chmod(TOKEN_FILE, 0o600)
    except OSError as e:
        print(f"warn: 无法缓存 token: {e}", file=sys.stderr)


def get_token_via_login():
    user = os.environ.get("CD2_USER", "")
    pwd = os.environ.get("CD2_PASS", "")
    if not pwd:
        print("error: 无 token 且未设置 CD2_PASS(可手动放 token 文件或用 CD2_TOKEN)", file=sys.stderr)
        sys.exit(2)
    req = pb.GetTokenRequest(userName=user, password=pwd)
    totp = os.environ.get("CD2_TOTP")
    if totp:
        req.totpCode = totp
    stub, channel = get_stub()
    try:
        resp = stub.GetToken(req, timeout=30)
    except grpc.RpcError as e:
        print(f"error: GetToken RPC 失败: {e}", file=sys.stderr)
        channel.close()
        sys.exit(2)
    channel.close()
    if not resp.success:
        msg = resp.errorMessage or "未知错误"
        if "2FA" in msg or "totp" in msg.lower() or "TOTP" in msg:
            msg += " (需要设置 CD2_TOTP)"
        print(f"error: 认证失败: {msg}", file=sys.stderr)
        sys.exit(2)
    save_token(resp.token)
    return resp.token


def metadata_for(token):
    if not token:
        return None
    return (("authorization", f"Bearer {token}"),)


def parse_value(raw):
    """按 JSON 解析; 失败按字符串; 含逗号则拆成列表。"""
    if "," in raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) > 1:
            return [parse_value(p) for p in parts]
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def set_field(msg, name, value):
    """递归设置字段, 支持 foo.bar=1 与 repeated 字段。"""
    parts = name.split(".")
    cur = msg
    for i, p in enumerate(parts):
        if i == len(parts) - 1:
            cur = setattr_scalar(cur, p, value)
        else:
            sub = getattr(cur, p)
            cur = sub
    return msg


def setattr_scalar(obj, field, value):
    if not hasattr(obj, field):
        raise ValueError(f"字段 {field} 不存在于 {type(obj).__name__}")
    if isinstance(value, dict):
        sub = getattr(obj, field)
        for k, v in value.items():
            setattr_scalar(sub, k, v)
        return sub
    if isinstance(value, list):
        f = getattr(obj, field)
        del f[:]  # 清空默认
        for item in value:
            if isinstance(item, dict):
                f.append()
                sub = f[-1]
                for k, v in item.items():
                    setattr_scalar(sub, k, v)
            else:
                f.append(item)
        return f
    # 枚举: 接受 int 或名称字符串
    desc = obj.DESCRIPTOR.fields_by_name.get(field)
    if desc and desc.enum_type and isinstance(value, str):
        try:
            value = desc.enum_type.values_by_name[value].number
        except KeyError:
            valid = [v.name for v in desc.enum_type.values]
            raise ValueError(f"枚举值 {value} 无效, 可选: {valid}")
    setattr(obj, field, value)
    return getattr(obj, field)


def build_request(method_name, fields):
    desc = pb.DESCRIPTOR.services_by_name.get("CloudDriveFileSrv")
    if not desc:
        raise ValueError(f"service CloudDriveFileSrv 未找到")
    method_desc = desc.methods_by_name.get(method_name)
    if not method_desc:
        raise ValueError(f"RPC {method_name} 不存在")
    req_type = method_desc.input_type
    if req_type.name == "Empty":
        from google.protobuf import empty_pb2
        return empty_pb2.Empty()
    req_cls = pb.__dict__.get(req_type.name)
    if not req_cls:
        raise ValueError(f"无法找到请求类型 {req_type.name}")
    req = req_cls()
    for name, raw in fields.items():
        value = parse_value(raw)
        try:
            set_field(req, name, value)
        except (ValueError, AttributeError, TypeError) as e:
            raise ValueError(f"参数 {name}={raw} 设置失败: {e}")
    return req


def call_rpc(stub, method_name, req, token):
    fn = getattr(stub, method_name, None)
    if not fn:
        raise ValueError(f"stub 上找不到方法 {method_name}")
    kwargs = {}
    md = metadata_for(token)
    if md:
        kwargs["metadata"] = md
    if method_name in STREAM_METHODS or "stream" in str(fn.__doc__ or "").lower():
        # 服务端流: 聚合
        try:
            parts = []
            for chunk in fn(req, **kwargs):
                parts.append(chunk)
            return parts
        except grpc.RpcError as e:
            return e
    try:
        return fn(req, **kwargs)
    except grpc.RpcError as e:
        return e


def to_jsonable(msg):
    """Message -> dict, 使用官方 json_format(自动处理 nested/repeated/enum)。"""
    from google.protobuf import json_format
    return json_format.MessageToDict(msg, preserving_proto_field_name=True)


def main():
    ap = argparse.ArgumentParser(description="CloudDrive2 gRPC 通用调用器")
    ap.add_argument("method", nargs="?", default="", help="RPC 方法名, 如 GetSubFiles / GetMountPoints")
    ap.add_argument("fields", nargs="*", help="field=value 参数")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--no-auth", action="store_true", help="跳过鉴权(公共方法)")
    ap.add_argument("--raw", action="store_true", help="输出原始 proto 文本而非 JSON")
    ap.add_argument("--list", action="store_true", help="列出全部方法")
    args = ap.parse_args()

    if args.list:
        # 从 proto 源文件解析方法名(比 descriptor 更稳)
        proto_src = open(os.path.join(BASE, "clouddrive.proto")).read()
        proto_src = re.sub(r"//[^\n]*", "", proto_src)
        m_block = re.search(r"service CloudDriveFileSrv \{(.*?)\n\}", proto_src, re.S)
        if m_block:
            for m in re.findall(r"rpc\s+(\w+)\s*\(\s*([\w.]+)\s*\)\s*returns\s*\(\s*(?:stream\s+)?([\w.]+)\s*\)", m_block.group(1)):
                print(f"{m[0]}  {m[1]} -> {m[2]}")
            sys.exit(0)
        sys.exit(1)

    method = args.method
    fields = {}
    for f in args.fields:
        if "=" not in f:
            fields[f] = "true"  # 纯字段名视为 bool true
        else:
            k, v = f.split("=", 1)
            fields[k] = v

    try:
        req = build_request(method, fields)
    except ValueError as e:
        print(f"参数错误: {e}", file=sys.stderr)
        sys.exit(2)

    token = None
    if method not in PUBLIC_METHODS and not args.no_auth:
        token = load_token()
        if not token:
            token = get_token_via_login()

    stub, channel = get_stub()
    try:
        resp = call_rpc(stub, method, req, token)
    except Exception as e:
        print(f"调用失败: {e}", file=sys.stderr)
        channel.close()
        sys.exit(1)

    if isinstance(resp, grpc.RpcError):
        code = resp.code().name if resp.code() else "UNKNOWN"
        print(f"RPC 失败 [{code}]: {resp.details()}", file=sys.stderr)
        channel.close()
        sys.exit(1)

    if isinstance(resp, list):
        # 流聚合
        items = [to_jsonable(r) for r in resp]
        if args.raw:
            for r in resp:
                print(r)
        else:
            print(json.dumps(items, ensure_ascii=False, indent=2))
    else:
        if args.raw:
            print(resp)
        else:
            # 合并流切片为单个消息再 JSON
            print(json.dumps(to_jsonable(resp), ensure_ascii=False, indent=2))
    channel.close()


if __name__ == "__main__":
    main()