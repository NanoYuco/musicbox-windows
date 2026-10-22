# NetEase-MusicBox · Windows

这是基于上游 [NetEase-MusicBox](https://github.com/darknessomi/musicbox) 的 Windows 适配版。请下载本仓库源码，按下文在本地源码目录中安装；包名、版本号、作者信息和 MIT 版权沿用上游。

基于 Python 的网易云音乐终端客户端，支持扫码登录、歌单、排行榜、搜索、双语歌词，以及通过默认的 mpg123 播放 MP3，或通过 mpv 播放 MP3 / FLAC。

本文面向 Windows 原生环境，支持 **Windows PowerShell 5.1** 和 **PowerShell 7（`pwsh`）**，下文命令均使用 PowerShell 语法，以当前仓库源码为准。

## 安装与启动

### 1. 准备环境

- Windows x64，建议使用 Windows 11，以及 Windows Terminal + PowerShell 7。
- Python 3.10 及以上，建议使用 3.13。
- [uv](https://docs.astral.sh/uv/getting-started/installation/)：管理 Python 环境和项目依赖。
- [mpg123](https://mpg123.org/download.shtml)：默认播放器配置，用于 MP3 播放。
- [mpv Windows 构建](https://mpv.io/installation/)：支持 MP3 / FLAC、无损 / Hi-Res 和 WASAPI 独占输出；需要这些功能时安装。

#### 安装 PowerShell 7（推荐）

推荐搭配 Windows Terminal 使用 PowerShell 7；对习惯 Linux 控制终端的用户，这种组合更适合日常命令行操作。Windows Terminal 是终端窗口，PowerShell 7 是其中运行的命令行环境，二者分别安装和选择。

按 [微软官方安装说明](https://learn.microsoft.com/en-us/powershell/scripting/install/install-powershell-on-windows)，可在现有 Windows PowerShell 中执行：

```powershell
winget install --id Microsoft.PowerShell --source winget
```

安装后重新打开 Windows Terminal，在新标签页中选择 PowerShell 7，或运行 `pwsh` 进入。执行 `$PSVersionTable.PSVersion`，确认主版本号为 `7`。在 Windows Terminal 的“设置 → 启动 → 默认配置文件”中选择 PowerShell 7（启动命令为 `pwsh.exe`），可让后续新窗口默认使用它。

PowerShell 7 与系统自带的 Windows PowerShell 5.1 并存，安装不会替换后者。后续依赖安装、MusicBox 启动和 CLI 命令均可在 PowerShell 7 中执行。

#### 安装 uv 和 Python

安装 uv：

```powershell
winget install --id=astral-sh.uv -e
```

安装后重新打开 PowerShell。尚未安装合适的 Python 时，可让 uv 安装：

```powershell
uv python install 3.13
```

### 2. 放置播放器

默认播放器配置是 **mpg123**；只播放 MP3 时可以先安装 mpg123。需要 FLAC、无损 / Hi-Res 或 mpv 独占输出时，再安装 mpv。两种播放器均由使用者自行安装，发布源码中不附带播放器或个人播放器配置。

#### mpg123（默认 MP3 播放器）

从 [mpg123 官方下载页](https://mpg123.org/download.shtml) 获取适合电脑架构的 Windows 动态版压缩包，完整解压到项目根目录的 `mpg123` 文件夹，确保存在 `mpg123\mpg123.exe`，并保留压缩包中的 DLL 等配套文件。也可以将完整安装目录加入 PATH。

在项目根目录检查本地安装：

```powershell
.\mpg123\mpg123.exe --version | Out-Host
```

如果已加入 PATH，可用 `mpg123 --version` 检查。MusicBox 会优先查找 PATH，再查找项目内的 `mpg123\mpg123.exe`。

#### mpv（无损 / Hi-Res 与独占输出）

下载适合电脑架构的 mpv Windows 压缩包，完整解压到项目根目录的 `mpv` 文件夹，确保结构如下：

```text
musicbox-windows/
├─ NEMbox/
├─ pyproject.toml
├─ uv.lock
└─ mpv/
   ├─ mpv.exe
   └─ 压缩包中的其他配套文件
```

MusicBox 会自动查找这里的 `mpv.exe`。如果已把 mpv 安装在其他位置，也可以将它所在的文件夹加入 PATH；PATH 中的版本优先。

以下命令在项目根目录执行，用于检查本地 mpv：

```powershell
.\mpv\mpv.exe --version | Out-Host
```

如果使用 PATH 中的 mpv，将文中的 `.\mpv\mpv.exe` 换成 `mpv`。

### 3. 安装依赖并运行

在能看到 `pyproject.toml` 的项目目录中打开 PowerShell：

```powershell
uv sync --frozen
uv run musicbox
```

依赖会自动包含 `windows-curses`。首次安装后，日常启动只需：

```powershell
uv run musicbox
```

界面需要真实的交互终端，建议窗口至少 80 列、25 行。窗口太小时先放大；搜索、配置读取等 CLI 子命令可以在非交互环境使用。

## 登录与日常操作

进入“我的歌单”“每日推荐”等需要登录的页面时，用网易云音乐手机 App 扫描终端二维码，并在手机上确认。登录成功后会保存状态，后续启动沿用。当前仅支持扫码登录。

### 默认快捷键

大写字母表示按住 Shift 输入对应字母。快捷键可在配置中的 `keymap.value` 修改。

| 按键 | 操作 |
| --- | --- |
| `j` / `k` | 下移 / 上移 |
| `l` 或 Enter / `h` | 进入 / 返回 |
| `d` / `u` | 下一页 / 上一页 |
| `g` / `G` | 第一项 / 最后一项 |
| 数字后按 `j`、`k`、`[`、`]` | 按指定数量移动或切歌 |
| `f` | 当前列表模糊搜索 |
| 空格 | 播放 / 暂停 |
| `[` / `]` | 上一曲 / 下一曲 |
| `+` / `-` | 增大 / 减小音量 |
| `P` | 切换播放模式 |
| `m` | 主菜单 |
| `p` | 当前 / 历史播放列表 |
| `i` | 当前歌曲信息 |
| `A` | 进入专辑 |
| `,` | 喜爱歌曲 |
| `s` / `c` | 添加到本地收藏 / 查看本地收藏 |
| `a` / `z` | 添加到打碟列表 / 查看打碟列表 |
| `C` | 缓存歌曲，需要额外安装 aria2c 或 wget |
| `t` | 定时退出 |
| `q` | 正常退出，保留登录状态 |
| `w` | 退出并清除用户信息 |

## 配置与音质

未设置 XDG 环境变量时，用户文件位于 `%USERPROFILE%\.netease-musicbox`：

| 文件 / 目录 | 用途 |
| --- | --- |
| `config.json` | 播放器、音质、界面和快捷键配置 |
| `cookie.txt` | 登录 Cookie |
| `database.json` | 用户状态、本地收藏和播放列表等数据 |
| `cached` | 缓存的音频文件 |
| `musicbox.log` | 程序日志 |

**先正常退出 MusicBox；若用过后台播放，也先执行 `uv run musicbox daemon stop`，再备份并编辑配置。** 修改后重启程序或后台服务生效。

用记事本打开实际配置位置：

```powershell
$musicboxConfig = Join-Path $env:USERPROFILE '.netease-musicbox\config.json'
if ($env:XDG_CONFIG_HOME) {
    $musicboxConfig = Join-Path $env:XDG_CONFIG_HOME 'netease-musicbox\config.json'
}
notepad $musicboxConfig
```

配置项的实际设置在 `value` 中。例如把 `music_quality.value` 改为 `"lossless"`。保留其他配置及对应的 `default`、`describe` 字段；JSON 使用英文双引号，不支持注释或末尾多余逗号，保存为 UTF-8 无 BOM。

| `music_quality.value` | 请求的音质 |
| --- | --- |
| `2` 或 `"standard"` | 标准 |
| `1` 或 `"higher"` | 较高 |
| `0` 或 `"exhigh"` | 极高，默认 |
| `3` 或 `"lossless"` | 无损 |
| `4` 或 `"hires"` | Hi-Res |
| `"jymaster"` | 超清母带 |

实际可用音质由歌曲资源、账号权限及接口返回决定。`player_backend.value` 的默认值为 `"mpg123"`，沿用上游默认配置；在 Windows 下，`mpg123_parameters.value` 为空且找到 mpv 时，会自动优先使用 mpv，FLAC / 无损 / Hi-Res 也使用 mpv。Windows 下如需实时进度反馈或独占输出，建议安装 mpv 并显式设置 `player_backend.value` 为 `"mpv"`。

其他常用项：`translation.value` 控制歌词翻译，`page_length.value` 控制每页条数，设为 `0` 时按窗口大小调整。Windows 核心播放不依赖系统通知或 Qt/DBus 桌面歌词。

当前 CLI 只提供配置读取，修改配置请编辑文件：

```powershell
uv run musicbox config get player_backend --json
uv run musicbox config get mpv_parameters --json
uv run musicbox config get music_quality --json
uv run musicbox config list --json
```

若设置了 XDG 变量，配置位于 `$env:XDG_CONFIG_HOME\netease-musicbox`，数据位于 `$env:XDG_DATA_HOME\netease-musicbox`，缓存位于 `$env:XDG_CACHE_HOME\netease-musicbox`；各项未设置时分别使用上面的默认位置。

## 开启 mpv WASAPI 独占模式

独占模式让 mpv 独占选定的音频设备，其他应用可能暂时无法通过该设备发声。它改变音频输出方式，不会自动提升歌曲音质，也不代表逐比特直通。

### 1. 允许设备独占

按 Win + R，输入 `mmsys.cpl` 并回车。在“播放”页选择实际使用的耳机、音箱或 DAC，打开“属性 → 高级”，勾选“允许应用程序独占控制此设备”和“给予独占模式应用程序优先权”，然后应用。

### 2. 设置 MusicBox

退出程序和后台服务，按上一节打开 `config.json`。**只修改现有两项的 `value`，不要用下面片段覆盖整个配置文件。** 若已有其他 mpv 参数，保留仍需使用的参数，并移除与本设置冲突的音频输出选项。

```json
{
  "player_backend": {
    "value": "mpv"
  },
  "mpv_parameters": {
    "value": [
      "--ao=wasapi",
      "--audio-exclusive=yes"
    ]
  }
}
```

`--ao=wasapi` 选择 Windows WASAPI 输出，`--audio-exclusive=yes` 请求独占。这里配置的是传给 mpv 的启动参数；修改后重新运行 `uv run musicbox`，或重新启动 CLI 后台播放。

### 3. 可选：指定耳机或 DAC

默认使用 WASAPI 默认输出设备。需要固定到某个设备时，先列出设备：

```powershell
.\mpv\mpv.exe --audio-device=help | Out-Host
```

从输出中复制对应设备引号内的完整 `wasapi/...` 标识。将参数中的 `--ao=wasapi` **替换为** `--audio-device=复制的完整标识`，继续保留 `--audio-exclusive=yes`。不要同时保留这两个设备选择方式，也不要把设备的显示名称当成标识。

### 4. 检查实际是否生效

先用前面的 `config get` 命令确认配置读取正确。需要核对实际音频初始化时，可以在 `mpv_parameters.value` 中临时追加以下两个字符串：

```json
[
  "--log-file=~~desktop/musicbox-mpv.log",
  "--msg-level=ao/wasapi=v"
]
```

这是向原参数列表追加，不能替换掉独占参数。`~~desktop/` 是 mpv 的桌面路径标记，不是 PowerShell 变量。

重启 MusicBox 并播放后，查看桌面上的 `musicbox-mpv.log`：检查 WASAPI 输出、格式接受记录中的 `exclusive`，以及后续是否成功打开设备并开始输出。只有尝试记录，或后面仍有初始化失败，不能算独占成功。界面上的采样率或歌曲音质标签也不能单独证明独占已启用。

日志在每次启动 mpv 时会覆盖，核对完成后可移除这两个临时参数。参数说明见 [mpv 官方手册](https://mpv.io/manual/stable/#audio)，格式接受日志可参照 [WASAPI 实现](https://github.com/mpv-player/mpv/blob/master/audio/out/ao_wasapi_utils.c)。

### 5. 关闭独占或处理无声

把 `--audio-exclusive=yes` 改为 `--audio-exclusive=no`，重启后恢复共享输出。需要移除显式设备选择时，可同时移除 `--ao=wasapi` 或 `--audio-device=...`，让 mpv 自动选择。

如果独占下无声，先确认选中了正确设备、Windows 允许独占、设备驱动支持当前输出格式，并关闭其他占用设备的播放器。蓝牙、HDMI、USB DAC 等设备的支持情况取决于驱动和设备本身；可先切回共享模式排查。需要让其他应用重新出声时，退出 MusicBox；CLI 播放则停止播放或停止后台服务，暂停不一定释放设备。

## 命令行播放与后台服务

以下命令面向后台播放。**先退出 TUI，再使用 CLI 播放控制；后台服务运行时不能同时启动 TUI。**

```powershell
uv run musicbox search "邓丽君" --type song --json
uv run musicbox song url 1847408145 --quality lossless --quiet
uv run musicbox play --id 1847408145 --json
uv run musicbox status --json
uv run musicbox pause --json
uv run musicbox seek +15 --json
uv run musicbox queue list --json
uv run musicbox stop --json
```

`play` 会自动启动后台服务；`seek` 需要使用 mpv 后端。`stop` 只停止播放，准备切回 TUI 时应停止服务：

```powershell
uv run musicbox daemon stop
uv run musicbox
```

查看后台服务或完整命令帮助：

```powershell
uv run musicbox daemon status --json
uv run musicbox --help
uv run musicbox play --help
```

## 常见问题

**找不到 mpv 或提示本地播放器不可用**：确认项目下有 `mpv\mpv.exe`，或当前终端 PATH 中能找到 mpv。完整解压播放器包，不要只复制缺少配套文件的可执行文件。检查 `player_backend.value` 为 `"mpv"`。

**安装默认播放器 mpg123**：从 [官方下载页](https://mpg123.org/download.shtml) 获取 Windows 动态版并完整解压到项目的 `mpg123` 文件夹，或加入 PATH。Windows 下建议用 mpv 获取实时进度；显式 mpg123 参数配置仍使用 mpg123，无损 FLAC 则使用 mpv。

**提示 daemon 正在运行或无法获取运行锁**：先正常退出另一个 TUI；若为后台服务，执行 `uv run musicbox daemon stop` 后再启动界面。

**显示 `--:--` 或播放失败**：`--:--` 表示尚未取得时长。检查播放器、音频设备、独占设置和网络；不要仅凭没有进度判定歌曲不可播放。

**缓存歌曲失败**：缓存功能依赖额外的 `aria2c` 或 `wget`，需安装 Windows 版本并放入 PATH；普通在线播放无需启用缓存功能。

**搜索或取歌曲链接失败**：检查网络及账号权限；部分资源受地区限制。需要代理时，在启动程序的同一个 PowerShell 会话设置自己的 HTTP 代理地址：

```powershell
$env:HTTP_PROXY = 'http://127.0.0.1:7890'
$env:HTTPS_PROXY = 'http://127.0.0.1:7890'
uv run musicbox
```

示例端口需改为实际代理监听端口；若后台服务已经运行，先停止它再从该会话重新启动，使它继承代理设置。

**查看程序日志**，默认路径下使用：

```powershell
Get-Content (Join-Path $env:USERPROFILE '.netease-musicbox\musicbox.log') -Tail 100 -Wait
```

若设置了 `XDG_DATA_HOME`，改用该目录下 `netease-musicbox\musicbox.log`。保留原配置和登录文件即可排查，不需要先删除整个用户目录。

## 本地开发检查

在项目根目录执行：

```powershell
uv run ruff check
uv run ruff format --check
uv run ty check
uv run pytest -q --tb=short
uv build
```

Windows 实际音频集成测试还需要对应播放器和本地音频样本；`MUSICBOX_TEST_MP3` 可指定测试 MP3。

仓库中的 GitHub Actions 配置仅包含 Windows 检查，支持 `main`、`master` 和手动触发。本次源码目录整理尚未在新仓库执行 GitHub CI，运行结果请以上传后的 Actions 记录为准。

## 致谢与许可

感谢 [MusicBox 贡献者](https://github.com/darknessomi/musicbox/graphs/contributors)。网易云音乐 API 适配参考 [api-enhanced](https://github.com/neteasecloudmusicapienhanced/api-enhanced)。

[更新日志](CHANGELOG.md) · [MIT License](LICENSE)

开发过程中使用的 TDD skill 来自 [addyosmani/agent-skills 的 test-driven-development](https://github.com/addyosmani/agent-skills/tree/main/skills/test-driven-development)。
