#!/usr/bin/env python3
"""cd2cmd.py — convenience CLI for common CloudDrive2 operations (127.0.0.1:19798).

Standalone wrapper (does not import cd2.py). Auth: env CD2_TOKEN, else
./token.txt; fallback GetToken via CD2_USER env +
CD2_PASS, cache 0600; 2FA needs CD2_TOTP. Token/password never printed.

Commands (--json on ls/find/info/clouds/copy-tasks):
  ls PATH [--refresh]                  list dir: name<TAB>size<TAB>type
  find PATH NAME [--refresh] [--content]  fuzzy search by name or content
  info PATH                            file fields
  mkdir PARENT NAME                    create folder
  mv/cp SRC... DEST [--policy P]       move/copy (P: overwrite|rename|skip)
  rm PATH... | rmperm PATH...          delete | delete permanently
  rn PATH NEWNAME                      rename
  dl PATH [OUTDIR]                     download URL + curl/wget
  mounts | clouds | copy-tasks [--all]
  dl-tasks | ul-tasks | backups        transfer/backup lists
  settings | acct | tokens | stat      settings / account / tokens / info
  token-create NAME [--perm ...]       create API token
  unlock PATH PASSWORD [--permanent]   unlock encrypted file
Errors -> stderr, exit non-zero.
"""

import argparse
import json
import os
import sys

import grpc

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "gen", "proto"))
import clouddrive_pb2 as pb  # noqa: E402
import clouddrive_pb2_grpc as pbgrpc  # noqa: E402

SERVER = os.environ.get("CD2_SERVER", "127.0.0.1:19798")
TOKEN_FILE = os.path.join(HERE, "token.txt")
EMPTY = pb.google_dot_protobuf_dot_empty__pb2.Empty()


# ---------------------------------------------------------------------------
# Client / auth
# ---------------------------------------------------------------------------
class Client:
    def __init__(self):
        self.channel = grpc.insecure_channel(SERVER)
        self.stub = pbgrpc.CloudDriveFileSrvStub(self.channel)
        self._token = None

    def md(self):
        tok = self.token(optional=True)
        return (("authorization", "Bearer " + tok),) if tok else ()

    def token(self, optional=False):
        if self._token is None:
            self._token = self._load(optional)
        return self._token

    def require_token(self):
        tok = self._load(True)
        if not tok:
            die("no token: set CD2_TOKEN or CD2_PASS (and CD2_USER, CD2_TOTP "
                "if 2FA)", code=2)
        self._token = tok
        return tok

    def _load(self, optional):
        tok = os.environ.get("CD2_TOKEN")
        if tok:
            return tok
        tok = os.environ.get("CLOUDDRIVEAPI")
        if tok:
            return tok
        tok = self._load_api_tokens_file()
        if tok:
            return tok
        if os.path.exists(TOKEN_FILE):
            tok = open(TOKEN_FILE).read().strip()
            if tok:
                return tok
        if optional:
            return None
        return self._login()

    @staticmethod
    def _load_api_tokens_file():
        for f in ("/opt/clouddrive2/config/api_tokens.json", "/opt/clouddrive2/api_tokens.json"):
            try:
                data = json.load(open(f))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data:
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
            elif isinstance(data, list) and data:
                first = data[0]
                if isinstance(first, dict):
                    return first.get("token") or first.get("key") or first.get("id")
        return None

    def _login(self):
        user = os.environ.get("CD2_USER", "")
        pwd = os.environ.get("CD2_PASS")
        if not pwd:
            die("no token: set CD2_TOKEN or CD2_PASS (and CD2_USER, CD2_TOTP "
                "if 2FA)", code=2)
        req = pb.GetTokenRequest(userName=user, password=pwd)
        if os.environ.get("CD2_TOTP"):
            req.totpCode = os.environ["CD2_TOTP"]
        try:
            resp = self.stub.GetToken(req, timeout=30)
        except grpc.RpcError as e:
            die("GetToken failed: %s" % e)
        if not resp.success:
            m = resp.errorMessage or "unknown"
            if "totp" in m.lower() or "2fa" in m.lower():
                die("GetToken failed: 2FA required — set CD2_TOTP. (%s)" % m)
            die("GetToken failed: %s" % m)
        try:
            fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(resp.token)
        except OSError:
            pass
        return resp.token


def die(msg, code=1):
    print("ERROR: %s" % msg, file=sys.stderr)
    sys.exit(code)

def hbytes(n):
    n = int(n)
    for u in ("", "K", "M", "G", "T", "P"):
        if n < 1024 or u == "P":
            return ("%.1f%s" % (n, u)) if u else str(n)
        n /= 1024.0
    return str(n)


def ftype(f):
    return "dir" if (f.isDirectory or f.fileType == pb.CloudDriveFile.Directory) else "file"

def emit_json(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _policy(name):
    m = {"overwrite": pb.MoveFileRequest.Overwrite,
         "rename": pb.MoveFileRequest.Rename,
         "skip": pb.MoveFileRequest.Skip}
    if name.lower() not in m:
        die("invalid policy: %s (overwrite|rename|skip)" % name)
    return m[name.lower()]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def h_ls(c, a):
    files = []
    for r in c.stub.GetSubFiles(pb.ListSubFileRequest(path=a.path, forceRefresh=a.refresh), metadata=c.md()):
        files.extend(r.subFiles)
    if a.json:
        return emit_json([{"name": f.name, "size": f.size, "type": ftype(f), "path": f.fullPathName} for f in files])
    for f in files:
        print("%s\t%s\t%s" % (f.name, f.size, ftype(f)))
    print("OK: %d entries" % len(files))


def h_find(c, a):
    req = pb.SearchRequest(path=a.path, searchFor=a.name, forceRefresh=a.refresh, fuzzyMatch=True)
    if a.content:
        req.contentSearch = True
    files = []
    for r in c.stub.GetSearchResults(req, metadata=c.md()):
        files.extend(r.subFiles)
    if a.json:
        return emit_json([{"name": f.name, "size": f.size, "type": ftype(f), "path": f.fullPathName} for f in files])
    for f in files:
        print("%s\t%s\t%s" % (f.fullPathName, f.size, ftype(f)))
    print("OK: %d match(es)" % len(files))


def h_info(c, a):
    f = c.stub.FindFileByPath(pb.FindFileByPathRequest(path=a.path), metadata=c.md())
    if not f.fullPathName:
        die("not found: %s" % a.path)
    if a.json:
        return emit_json({"name": f.name, "path": f.fullPathName, "size": f.size,
                          "type": ftype(f), "id": f.id, "isDirectory": f.isDirectory,
                          "isCloudFile": f.isCloudFile, "readOnly": f.readOnly,
                          "fileEncryptionType": f.fileEncryptionType})
    for k in ("name", "id", "fullPathName", "size", "isDirectory", "isCloudFile",
              "readOnly", "fileEncryptionType"):
        print("%s: %s" % (k, getattr(f, k, None)))
    print("OK: %s" % f.fullPathName)


def h_mkdir(c, a):
    r = c.stub.CreateFolder(pb.CreateFolderRequest(parentPath=a.parent, folderName=a.name), metadata=c.md())
    if not r.result.success:
        die(r.result.errorMessage or "mkdir failed")
    print("OK: %s" % r.folderCreated.fullPathName)


def _mvcp(c, a, move):
    srcs, dest = a.src[:-1], a.src[-1]
    if not srcs:
        die("need source(s) and dest")
    req = (pb.MoveFileRequest if move else pb.CopyFileRequest)(
        theFilePaths=srcs, destPath=dest, conflictPolicy=_policy(a.policy))
    r = (c.stub.MoveFile if move else c.stub.CopyFile)(req, metadata=c.md())
    if not r.success:
        die(r.errorMessage or "failed")
    print("OK: %d -> %s" % (len(srcs), dest))


def h_mv(c, a):
    _mvcp(c, a, True)


def h_cp(c, a):
    _mvcp(c, a, False)


def h_rm(c, a):
    r = c.stub.DeleteFiles(pb.MultiFileRequest(path=a.path), metadata=c.md())
    if not r.success:
        die(r.errorMessage or "delete failed")
    print("OK: deleted %d" % len(a.path))

def h_rmperm(c, a):
    r = c.stub.DeleteFilesPermanently(pb.MultiFileRequest(path=a.path), metadata=c.md())
    if not r.success:
        die(r.errorMessage or "delete failed")
    print("OK: permanently deleted %d" % len(a.path))


def h_rn(c, a):
    r = c.stub.RenameFile(pb.RenameFileRequest(theFilePath=a.path, newName=a.newname), metadata=c.md())
    if not r.success:
        die(r.errorMessage or "rename failed")
    print("OK: renamed to %s" % a.newname)

def h_dl(c, a):
    info = c.stub.GetDownloadUrlPath(pb.GetDownloadUrlPathRequest(
        path=a.path, lazy_read=False, get_direct_url=False), metadata=c.md())
    out = os.path.basename(a.path)
    if a.outdir:
        out = os.path.join(a.outdir, out)
    if info.directUrl:
        print("downloadUrl: %s" % info.directUrl)
        if info.userAgent:
            print('curl: curl -L -A "%s" -o %r "%s"' % (info.userAgent, out, info.directUrl))
        else:
            print("curl: curl -L -o %r %r" % (out, info.directUrl))
    elif info.downloadUrlPath:
        scheme, host = ("http", SERVER) if "://" not in SERVER else SERVER.split("://", 1)
        p = info.downloadUrlPath.replace("{SCHEME}", scheme).replace("{HOST}", host).replace("{PREVIEW}", "false")
        url = scheme + "://" + host + p
        print("downloadUrl: %s" % url)
        print("curl: curl -L -o %r %r" % (out, url))
        print("wget: wget -O %r %r" % (out, url))
    else:
        die("no download URL")
    if info.expiresIn:
        print("expiresIn: %s sec" % info.expiresIn)
    print("OK")


def h_mounts(c, a):
    r = c.stub.GetMountPoints(EMPTY, metadata=c.md())
    for m in r.mountPoints:
        print("%s\t%s\t%s" % (m.mountPoint, m.name, m.sourceDir))
    print("OK: %d mount(s)" % len(r.mountPoints))

def h_clouds(c, a):
    r = c.stub.GetAllCloudApis(EMPTY, metadata=c.md())
    apis = r.apis
    if a.json:
        return emit_json([{"name": x.name, "userName": x.userName, "readOnly": x.readOnly,
                           "isLocked": x.isLocked} for x in apis])
    for x in apis:
        print("%s\t%s\t%s\t%s\t%s" % (x.name, x.userName,
              "locked" if x.isLocked else "ok", "RO" if x.readOnly else "RW", x.path))
    print("OK: %d cloud(s)" % len(apis))


def h_copy_tasks(c, a):
    tasks = c.stub.GetCopyTasks(EMPTY, metadata=c.md()).copyTasks
    sn = {0: "Pending", 1: "Scanning", 2: "Scanned", 3: "Completed", 4: "Failed"}
    shown = tasks if a.all else [t for t in tasks if t.status != pb.CopyTask.Completed]
    if a.json:
        return emit_json([{"mode": "Copy" if t.taskMode == pb.CopyTask.Copy else "Move",
                           "source": t.sourcePath, "dest": t.destPath,
                           "status": sn.get(t.status, str(t.status)),
                           "files": t.uploadedFiles, "totalFiles": t.totalFiles,
                           "bytes": t.uploadedBytes, "totalBytes": t.totalBytes} for t in shown])
    done = sum(1 for t in tasks if t.status == pb.CopyTask.Completed)
    failed = sum(1 for t in tasks if t.status == pb.CopyTask.Failed)
    for t in shown:
        print("%s\t%s -> %s" % (sn.get(t.status, "?"), t.sourcePath, t.destPath))
    print("SUMMARY: %d total, %d active, %d completed, %d failed"
          % (len(tasks), len(tasks) - done - failed, done, failed))


def h_dl_tasks(c, a):
    r = c.stub.GetDownloadFileList(EMPTY, metadata=c.md())
    for d in r.downloadFiles:
        print("%s\t%s\t%.1f KB/s" % (d.filePath, hbytes(d.fileLength), d.bytesPerSecond))
    print("OK: %d download(s), %.1f KB/s global" % (len(r.downloadFiles), r.globalBytesPerSecond))


def h_ul_tasks(c, a):
    r = c.stub.GetUploadFileList(pb.GetUploadFileListRequest(getAll=True), metadata=c.md())
    for u in r.uploadFiles:
        print("%s\t%s\t%s" % (u.destPath, u.status, hbytes(u.transferedBytes)))
    print("OK: %d upload(s) (filtered %d)" % (len(r.uploadFiles), r.totalCountFiltered))


def h_backups(c, a):
    r = c.stub.BackupGetAll(EMPTY, metadata=c.md())
    sn = {0: "Idle", 1: "Walking", 2: "Error", 3: "Disabled", 4: "Scanned", 5: "Finished", 6: "Waiting"}
    for b in r.backups:
        dests = ",".join(d.destinationPath for d in b.backup.destinations)
        print("%s\t%s -> %s" % (sn.get(b.status, "?"), b.backup.sourcePath, dests))
    print("OK: %d backup(s)" % len(r.backups))


def h_settings(c, a):
    s = c.stub.GetSystemSettings(EMPTY, metadata=c.md())
    for k in ("fileBufferDiskCacheLocation", "fileBufferDiskCacheMaxBytes",
              "tempFileLocation", "dirCacheDbLocation", "maxFileLogSizeBytes",
              "maxBackupLogSizeBytes", "maxFileLogFiles", "maxBackupLogFiles",
              "backupQueueHighWater", "backupQueueLowWater",
              "maxConcurrentBackupWalkers", "maxDownloadSpeedKBytesPerSecond",
              "maxUploadSpeedKBytesPerSecond", "deviceName", "updateChannel",
              "startDelaySecs"):
        print("%s: %s" % (k, getattr(s, k, None)))
    print("OK: settings")


def h_acct(c, a):
    r = c.stub.GetAccountStatus(EMPTY, metadata=c.md())
    print("userName: %s" % r.userName)
    print("emailConfirmed: %s" % r.emailConfirmed)
    print("balance: %s" % r.accountBalance)
    p = r.accountPlan
    if p and p.planName:
        print("plan: %s (%s, planId=%s)" % (p.planName, p.description, p.planId))
    s = getattr(r, "subscription", None)
    if s and s.productId:
        print("subscription: %s autoRenew=%s expires=%s" % (s.productId, s.autoRenew, s.expiresAt))
    print("OK")


def h_tokens(c, a):
    r = c.stub.ListTokens(EMPTY, metadata=c.md())
    for t in r.tokens:
        print("%s\t%s\t%s" % (t.friendly_name, t.rootDir or "/",
              "expires_in=%s" % t.expires_in if t.expires_in else "no-expiry"))
    print("OK: %d token(s)" % len(r.tokens))


def h_token_create(c, a):
    perms = pb.TokenPermissions()
    valid = [n for n in dir(perms) if n.startswith("allow_")]
    if a.perm:
        bad = [p for p in a.perm if p not in valid]
        if bad:
            die("unknown perm(s): %s" % ", ".join(bad))
        for p in a.perm:
            setattr(perms, p, True)
    else:
        for p in ("allow_list", "allow_search", "allow_read", "allow_get_mounts",
                  "allow_get_transfer_tasks", "allow_get_cloud_apis",
                  "allow_get_system_settings", "allow_get_backups",
                  "allow_get_account_info", "allow_view_runtime_info"):
            setattr(perms, p, True)
    info = c.stub.CreateToken(pb.CreateTokenRequest(rootDir="/", permissions=perms,
                                                    friendly_name=a.name), metadata=c.md())
    print("token: %s" % info.token)
    print("rootDir: %s" % info.rootDir)
    if info.expires_in:
        print("expiresIn: %s sec" % info.expires_in)
    print("OK: created '%s'" % info.friendly_name)


def h_unlock(c, a):
    r = c.stub.UnlockEncryptedFile(pb.UnlockEncryptedFileRequest(
        path=a.path, password=a.password, permanentUnlock=a.permanent), metadata=c.md())
    if not r.success:
        die(r.errorMessage or "unlock failed")
    print("OK: unlocked %s%s" % (a.path, " (permanent)" if a.permanent else ""))


def h_stat(c, a):
    try:
        si = c.stub.GetSystemInfo(EMPTY)  # public, no auth
    except grpc.RpcError as e:
        die("GetSystemInfo failed: %s" % e)
    print("IsLogin: %s" % si.IsLogin)
    print("UserName: %s" % si.UserName)
    print("SystemReady: %s" % si.SystemReady)
    if si.SystemMessage:
        print("SystemMessage: %s" % si.SystemMessage)
    try:
        rt = c.stub.GetRuntimeInfo(EMPTY, metadata=c.md())
        print("product: %s %s (CloudAPI %s)" % (rt.productName, rt.productVersion, rt.CloudAPIVersion))
        print("os: %s" % rt.osInfo)
    except grpc.RpcError as e:
        print("runtime info unavailable: %s" % e)
    print("OK")


HANDLERS = {
    "ls": h_ls, "find": h_find, "info": h_info, "mkdir": h_mkdir,
    "mv": h_mv, "cp": h_cp, "rm": h_rm, "rmperm": h_rmperm, "rn": h_rn,
    "dl": h_dl, "mounts": h_mounts, "clouds": h_clouds, "copy-tasks": h_copy_tasks,
    "dl-tasks": h_dl_tasks, "ul-tasks": h_ul_tasks, "backups": h_backups,
    "settings": h_settings, "acct": h_acct, "tokens": h_tokens,
    "token-create": h_token_create, "unlock": h_unlock, "stat": h_stat,
}


def build_parser():
    p = argparse.ArgumentParser(prog="cd2cmd.py", description="CloudDrive2 convenience CLI")
    s = p.add_subparsers(dest="cmd", required=True)
    j = dict(action="store_true")

    x = s.add_parser("ls"); x.add_argument("path"); x.add_argument("--refresh", **j); x.add_argument("--json", **j)
    x = s.add_parser("find"); x.add_argument("path"); x.add_argument("name"); x.add_argument("--refresh", **j); x.add_argument("--content", **j); x.add_argument("--json", **j)
    x = s.add_parser("info"); x.add_argument("path"); x.add_argument("--json", **j)
    x = s.add_parser("mkdir"); x.add_argument("parent"); x.add_argument("name")
    for n in ("mv", "cp"):
        x = s.add_parser(n); x.add_argument("src", nargs="+"); x.add_argument("--policy", default="rename")
    x = s.add_parser("rm"); x.add_argument("path", nargs="+")
    x = s.add_parser("rmperm"); x.add_argument("path", nargs="+")
    x = s.add_parser("rn"); x.add_argument("path"); x.add_argument("newname")
    x = s.add_parser("dl"); x.add_argument("path"); x.add_argument("outdir", nargs="?")
    s.add_parser("mounts")
    x = s.add_parser("clouds"); x.add_argument("--json", **j)
    x = s.add_parser("copy-tasks"); x.add_argument("--all", **j); x.add_argument("--json", **j)
    s.add_parser("dl-tasks")
    s.add_parser("ul-tasks")
    s.add_parser("backups")
    s.add_parser("settings")
    s.add_parser("acct")
    s.add_parser("tokens")
    x = s.add_parser("token-create"); x.add_argument("name"); x.add_argument("--perm", action="append")
    x = s.add_parser("unlock"); x.add_argument("path"); x.add_argument("password"); x.add_argument("--permanent", **j)
    s.add_parser("stat")
    return p


def main():
    args = build_parser().parse_args()
    c = Client()
    try:
        if args.cmd != "stat":
            c.require_token()
        HANDLERS[args.cmd](c, args)
    except grpc.RpcError as e:
        die("RPC error: %s" % e)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
