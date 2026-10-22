# 安全说明

本文适用于本仓库当前的 Windows 适配源码。安装和更新方式见本仓库 [README](README.md)；上游项目的版本支持范围和安全渠道由上游自行维护。

## 报告问题

请勿在公开 Issue 中披露可利用的漏洞细节、Cookie、扫码登录输出或含有敏感信息的完整日志。本仓库是否已启用 GitHub 私密漏洞报告，请以仓库的 Security 页面为准；本文不宣称私密渠道已开通，也不承诺响应时限。

若没有可用的私密渠道，可先用不包含漏洞细节和凭据的 Issue 询问维护者的联系方式。准备报告时，可记录受影响的源码版本、Windows 和 Python 版本、问题影响及脱敏后的复现步骤。上游仓库的漏洞入口不等同于本 Windows 适配仓库的入口。

## Windows 本地数据

未设置 XDG 环境变量时，下列文件位于 `%USERPROFILE%\.netease-musicbox`：

| 文件 / 目录 | 内容 |
| --- | --- |
| `cookie.txt` | 网易云登录会话 Cookie |
| `database.json` | 用户信息、本地收藏和播放列表等状态 |
| `config.json` | 用户配置，可能含个人代理或播放器参数 |
| `musicbox.log` | 应用日志，可能含请求或播放信息 |
| `cached`、`nemcache*` | 音频缓存与 HTTP 缓存 |

设置 `XDG_CONFIG_HOME`、`XDG_DATA_HOME`、`XDG_CACHE_HOME` 时，对应内容分别写入其下的 `netease-musicbox` 子目录；未设置的项仍使用上述默认目录。

请使用 Windows 账户及文件夹访问权限保护这些内容，不要将运行数据、个人配置或日志上传到仓库。能读取 Cookie 的本地程序或用户，可能在会话失效前使用该网易云会话。

登录仅支持网易云音乐 App 扫码。共享或弃用机器前，先退出 TUI、停止后台服务，再在同一配置环境中执行 `uv run musicbox auth logout --yes` 清除本地登录信息；本地清理不代替网易云账号侧的会话管理。

## Windows 后台通信

Windows daemon 使用绑定到 `127.0.0.1` 随机端口的本机 TCP 连接。端口及认证密钥的发现信息由 Windows DPAPI 按当前用户保护，控制消息使用随机 nonce 和 HMAC 校验。Windows 下不使用 Unix socket 的 `0600` / `0700` 权限模型。

运行文件默认位于 `%LOCALAPPDATA%\netease-musicbox`，设置 `XDG_RUNTIME_DIR` 时位于其下的 `netease-musicbox`。这些文件也属于本机运行状态，应保持私有并排除在源码发布之外。上述机制不能阻止已经取得当前 Windows 用户权限的程序访问本地数据。

## 网络与依赖

客户端访问网易云相关 API 及接口返回的媒体地址；请求可能涉及账号标识、播放信息和媒体 URL。配置 HTTP / HTTPS 代理时，请使用可信的代理，并在分享排错材料前检查是否包含凭据或个人信息。

依赖以本仓库的 `pyproject.toml` 和 `uv.lock` 为准。mpv、mpg123 由使用者按 README 安装。Ruff、类型检查和测试有助于检查代码质量，不构成完整的安全审计。第三方 Agent 或 skill 可能继承本地 shell 的文件访问权限，使用前请检查其来源和行为。
