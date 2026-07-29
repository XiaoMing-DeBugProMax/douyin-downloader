# 抖音视频下载

这是一个仅在 Windows 本机运行的单视频下载工具，只能用于你拥有权利或已获授权的抖音公开视频；请遵守平台规则和适用法律。

## 直接使用

1. 双击 `抖音视频下载.exe`。
2. 等待“本地服务运行中”控制窗口出现，默认浏览器会自动打开本地页面。
3. 粘贴抖音分享文案或链接，点击“开始解析”。
4. 核对封面、作者、文案和时长。
5. 点击“下载视频”时，文件按浏览器设置保存到默认下载目录；点击“选择保存位置”时，可在支持该功能的 Edge 或 Chrome 中选择文件名和目录。

控制窗口中的“重新打开网页”会打开当前本地页面。“停止并退出”以及直接关闭控制窗口都会停止本地服务并释放端口。页面右上角可以切换三种主题，选择保存在浏览器本机存储中。

本工具不会读取浏览器或抖音登录 Cookie，不保存下载历史，不把视频写入项目目录，也不会向局域网或公网提供服务。

## 从源码运行

需要 Windows 10/11 x64 和 Python 3.12。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m douyin_downloader
```

运行指定测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_logging_config.py -q
```

运行完整门禁：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m mypy src
```

## 构建 EXE

唯一构建入口会先运行完整测试、代码检查和类型检查，再生成图标与单文件 EXE：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

成功后文件位于 `dist\抖音视频下载.exe`，命令末尾会显示它的 SHA-256。

## 本机文件

- 运行状态：`%LOCALAPPDATA%\DouyinLocalDownloader\runtime.json`
- 脱敏日志：`%LOCALAPPDATA%\DouyinLocalDownloader\logs\app.log`

日志单文件最多 1 MiB，并保留最多 5 个轮转备份。日志只记录安全的操作字段，不记录分享文案、Cookie、启动令牌、解析令牌或媒体地址。

## 常见问题

- **解析突然失败：** 抖音公开接口或页面规则可能已经变化。退出后重试；持续失败时需要更新解析适配器或固定版本依赖。工具不会改用第三方解析网站，也不会读取登录 Cookie。
- **没有弹出保存位置窗口：** `showSaveFilePicker` 需要浏览器支持和用户点击触发。请使用当前稳定版 Edge/Chrome；不支持时页面会提示并退回浏览器默认下载。
- **安全软件提示风险：** 未签名的 PyInstaller 单文件程序可能被误报。请核对可信源码和构建 SHA-256，或直接从源码运行；不要为了通过检测关闭系统防护。
- **网页没有打开：** 在控制窗口点击“重新打开网页”。不要把运行地址改成局域网地址。

## 明确不包含

本 MVP 不包含批量下载、账号登录、私密或受限内容绕过、用户主页/收藏/图集/直播、下载历史、数据库、云服务、局域网或公网部署、安装器、代码签名、自动更新和遥测。
