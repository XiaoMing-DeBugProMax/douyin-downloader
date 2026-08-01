# FFmpeg Windows 分发与 M4A 流复制研究

日期：2026-08-01

## 研究问题

Issue #6 需要随 Windows 应用提供 `ffmpeg` / `ffprobe`，从已经归档的 MP4 中提取完整视频音轨为 M4A，并独立校验音频流和时长。本记录只采用 FFmpeg 官方网站、官方文档和官方源码说明，回答以下问题：

1. 随应用分发 FFmpeg 时需要保留哪些许可、源码和构建材料；
2. 如何记录版本、来源和构建参数，才能让发布物可审计；
3. 如何以固定参数执行音轨流复制，并区分“无音轨”和“提取失败”；
4. `ffprobe` 可以可靠验证什么，哪些判断仍需由本项目定义。

本记录不是法律意见。正式对外分发前仍应由熟悉 LGPL/GPL 和产品销售地区的软件律师复核。

## 结论摘要

- **不要把“下载了一个 Windows 版 ffmpeg.exe”视为完成许可审计。** FFmpeg 官网明确说明其自身只提供源码，官网列出的 Windows 可执行文件由第三方构建者提供。最终许可取决于实际打包二进制的配置、补丁和外部库。
- **发布清单必须绑定到二进制哈希。** 对 `ffmpeg.exe`、`ffprobe.exe` 分别记录 SHA-256、`-version`、`-buildconf`、上游版本或提交、构建者、下载地址、取得日期、对应源码包、补丁和许可结论；否则无法证明“源码与二进制完全对应”。
- **首选可再现、最小化的受控构建。** 若希望保持 FFmpeg 组件为 LGPL，应确保实际配置没有 `--enable-gpl`、没有 `--enable-nonfree`，并逐项审计外部库。若采用 GPL 构建，也必须按 GPL 对该 FFmpeg 构建履行义务；应用通过独立进程调用是否影响应用自身许可，不应仅凭本记录下结论。
- **提取采用第一条实际音频流的 stream copy。** 固定执行 `-map 0:a:0 -c:a copy -f ipod`，不解码、不重编码，因此速度快且不损失音质；但目标 M4A 容器无法接受源音频编码时，FFmpeg 会失败，此时应记录“音轨提取失败”，不能偷偷转码。
- **先探测、再提取、再独立探测。** 输入没有音频流时记录“无音轨”，不创建空文件，也不让必需的 MP4 归档失败。输出必须重新由 `ffprobe` 检查，不能只依赖 `ffmpeg` 的退出码。
- **“合理时长”不是 FFmpeg 官方阈值。** 建议比较源音频流与输出音频流的可用时长，容许 `max(1 秒, 源时长的 2%)` 的容器时间戳差异；这是本项目策略，需要用真实抖音样本验证后固化，而不是 FFmpeg 标准。

## 一、Windows 分发与许可材料

### 1. FFmpeg 的许可取决于实际构建

FFmpeg 官方许可说明给出的基本规则是：大部分 FFmpeg 为 LGPL 2.1 或更高版本；启用可选 GPL 部分时，整个 FFmpeg 构建适用 GPL；使用 `--enable-nonfree` 会得到不可再分发的构建。官方 LGPL 合规清单还明确要求：

- 不使用 `--enable-gpl` 和 `--enable-nonfree`；
- 提供 FFmpeg 源码，即使没有修改；
- 源码必须与分发的二进制完全对应；
- 保留修改差异和编译方式（例如完整 configure 参数）；
- 以压缩包提供源码，并与二进制在同一下载服务器持续提供；
- 在下载页、应用“关于”界面和 EULA 中作 FFmpeg 与 LGPL 提示；
- 不在 EULA 中禁止为 LGPL 目的进行逆向工程；
- 继续审计编入 FFmpeg 的每一个外部库。

官方清单主要以“应用链接 FFmpeg 库”的场景表述；本项目计划以独立子进程运行 `ffmpeg.exe` / `ffprobe.exe`。这能形成清晰的进程与接口边界，但**不消除对所分发 FFmpeg 二进制本身的 LGPL/GPL 义务**。

### 2. 推荐的二进制取得策略

按可审计性从高到低排序：

1. **项目控制的固定版本构建：** 从 FFmpeg 官方 release tarball 取得固定版本，验证官方 PGP 签名，使用仓库保存的构建脚本和锁定工具链生成 Windows 二进制。优点是源码、参数和二进制之间的对应关系最清楚。
2. **经过逐项审计的第三方固定构建：** 官网虽链接 Windows 构建者，但明确说 FFmpeg 自身只提供源码。采用第三方构建时，必须额外保存该构建者的准确发行标识、二进制哈希、构建脚本/参数、补丁和所有外部库许可；不能把“官网有链接”误认为 FFmpeg 项目为该二进制的配置和许可背书。
3. **不接受滚动 latest URL：** `latest` 无法稳定对应源码、构建参数和测试结果，也会让已经发布的安装包失去可追溯性。

截至本记录日期，FFmpeg 下载页展示 8.1.2 源码版本，并说明正式发布包有 PGP 签名可验证。Issue #6 不应仅因为它是当前版本就直接采用；最终版本还需要与 Windows 构建、项目测试和安全维护方案一起决定。

### 3. 每个发布版本必须保存的审计清单

建议在仓库和安装包中都保存一个机器可读清单，例如 `third-party/ffmpeg/manifest.json`，并生成便于用户阅读的 NOTICE。至少记录：

| 字段 | 要求 |
| --- | --- |
| 组件 | `ffmpeg.exe`、`ffprobe.exe` 分开列出 |
| 文件身份 | 文件大小、SHA-256 |
| 运行时身份 | 完整 `ffmpeg -version` / `ffprobe -version` 输出 |
| 构建配置 | 完整 `ffmpeg -buildconf` / `ffprobe -buildconf` 输出 |
| 上游来源 | 固定 release 版本或 Git 提交，不使用模糊分支名 |
| 获取记录 | 下载地址、取得日期、构建者/供应者 |
| 源码 | 与二进制完全对应的源码压缩包及其哈希 |
| 完整改动 | 补丁文件，未修改也明确记录“无项目补丁” |
| 重建资料 | 工具链版本、构建脚本、configure 参数、环境说明 |
| 许可 | FFmpeg 最终适用 LGPL/GPL 版本、许可全文、版权 NOTICE |
| 外部库 | 名称、版本、许可、源码地址以及是否改变 FFmpeg 最终许可 |
| 对应源码入口 | 与应用二进制同一发布站点上的稳定下载地址 |

`-version` 可以显示程序和库版本，`-buildconf` 会逐项显示编译配置，这两项是发布流水线应自动采集并与预期比对的官方接口。仅保存手写 configure 行不够，还应以最终二进制的 `-buildconf` 输出反向验证。

### 4. 发布门禁建议

发布脚本应在以下任一条件出现时直接失败：

- 缺少 `ffmpeg.exe` 或 `ffprobe.exe`；
- 二进制哈希与审计清单不一致；
- `-version` 或 `-buildconf` 与已批准材料不一致；
- 出现未批准的 `--enable-gpl`、任何 `--enable-nonfree`，或新增外部库；
- 找不到完全对应的源码包、许可全文、补丁或构建说明；
- Windows 安装包中缺少用户可见的第三方许可与对应源码信息。

这里不能机械地把 `--enable-gpl` 一律定义为产品不允许：GPL 构建可能仍可合法分发，但会改变审计和履约范围。项目若要采用，应先形成单独的法律与产品决策；`--enable-nonfree` 则依据 FFmpeg 官方说明不能用于再分发。

## 二、M4A 流复制方案

### 1. 为什么采用 stream copy

FFmpeg 官方文档将 streamcopy 定义为直接复制输入 elementary stream 的数据包，不进行解码、过滤或重新编码。它速度快、没有重编码质量损失，但官方也提醒：目标容器缺少必要信息或不接受该流时，复制可能失败。

M4A 扩展名属于 MOV/MP4/ISOBMFF 家族；FFmpeg 的 `ipod` muxer 被官方描述为只容纳音频流的 MPEG-4 音频文件格式。因此使用 `-f ipod` 可以在临时文件不是标准 `.m4a` 后缀时仍明确指定目标容器。

### 2. 输入探测

建议以参数数组直接启动子进程（下文命令仅为可读展示）：

```text
ffprobe.exe -v error \
  -select_streams a:0 \
  -show_entries stream=index,codec_name,codec_type,start_time,duration:format=format_name,start_time,duration \
  -of json \
  <archive.mp4>
```

处理规则：

1. 进程超时、非零退出、JSON 无法解析：输入探测失败，不等同于“无音轨”；
2. `streams` 为空：记录可选成果状态“无音轨”，不启动提取；
3. 存在首条音频流：保存其 codec、stream duration、format duration 作为输出验证基准；
4. duration 字段可能缺失或为 `N/A`，解析时必须允许缺省，不把格式异常当作数值；
5. 若未来遇到多音轨作品，`a:0` 代表第一条音频流。是否改选默认 disposition 音轨属于产品策略，Issue #6 应先对真实样本统计并写测试，不能假设所有 MP4 永远只有一条音频流。

FFprobe 官方文档说明 `-select_streams` 限定被展示的流，`-show_entries` 限定字段，JSON writer 提供机器可读输出。它也说明无法打开或识别输入时会返回正退出码，因此调用方必须同时检查退出码和 JSON，不能只判断 stdout 是否为空。

### 3. 固定提取命令

```text
ffmpeg.exe -hide_banner -loglevel error -nostdin -n \
  -i <archive.mp4> \
  -map 0:a:0 -vn -sn -dn \
  -c:a copy \
  -map_metadata -1 \
  -f ipod \
  <app-generated-temp-output>
```

参数意图：

- `-map 0:a:0`：明确选择输入 0 的第一条音频流，禁用自动流选择；
- `-c:a copy`：只做音频 stream copy，不能回退为重编码；
- `-f ipod`：固定为音频专用的 MPEG-4 容器，不依赖临时文件扩展名猜测；
- `-nostdin`：禁止后台进程接受交互输入；
- `-n`：不覆盖既有临时文件，调用前由应用按受控路径清理自己的残留临时文件；
- `-map_metadata -1`：不把输入容器元数据自动带入可选音轨成果；作品文案只能由 TXT 成果写入，不能进入命令或媒体元数据参数；
- `-hide_banner -loglevel error`：只收集必要诊断。stderr 仍可能包含路径或媒体信息，不能原样写入普通应用日志。

不要使用尾随 `?` 的可选 map 来统一处理无音轨情况。官方文档说明 `?` 会让不存在的映射被忽略，但音频专用输出在没有任何可写流时仍可能失败；预先探测才能给用户稳定、准确的“无音轨”语义。

### 4. 输出独立校验

提取进程退出 0 后，对临时输出重新运行：

```text
ffprobe.exe -v error \
  -select_streams a:0 \
  -show_entries stream=index,codec_name,codec_type,start_time,duration:format=format_name,start_time,duration \
  -of json \
  <temp-output>
```

最小通过条件：

1. 临时文件存在且非空；
2. ffprobe 在超时内以 0 退出且 JSON 可解析；
3. 至少存在一条 `codec_type=audio` 的流；
4. 输出 codec 与输入所选音频流一致，以证明没有意外转码；
5. 能取得的输出时长为有限正数；
6. 若输入音频时长也可取得，输出与输入差值不超过项目定义的容差；
7. 校验通过后才把临时文件原子移动到最终 `.m4a` 路径并登记成果。

建议时长取值优先级为“音频流 duration，其次容器 format duration”。建议初始容差为：

```text
abs(output_duration - source_duration) <= max(1.0 second, source_duration * 0.02)
```

这是为了容忍容器 edit list、起始时间和时间基换算造成的小偏差。该数值是**本项目的工程推断**，FFmpeg 官方没有规定这一阈值；上线前必须用短视频、长视频、不同起始时间和不同 AAC 配置样本验证。

`ffprobe` 验证的是容器和流的可解析结构，并不等价于逐包解码完整性检查。若风险样本证明仅探测不足，可另加较慢的深度校验模式，让 FFmpeg 把音频完整解码到 null muxer；这会增加 CPU 和等待时间，而且已经超出 Issue #6 的“存在音频流与合理时长”最低验收范围。

### 5. 状态与重试语义

建议可选音轨成果至少区分以下结果：

| 状态 | 含义 | 是否重试 |
| --- | --- | --- |
| `disabled` | 用户未启用音轨成果 | 否 |
| `no_audio` | 已可靠探测，归档 MP4 没有音频流 | 默认否，可在 MP4 被替换后重算 |
| `ready` | M4A 已提取并独立校验 | 否 |
| `probe_failed` | 无法可靠判断输入是否有音轨 | 是 |
| `extract_failed` | 有音轨，但 stream copy 或容器写入失败 | 是 |
| `validation_failed` | FFmpeg 返回成功，但输出独立校验未通过 | 是 |

任何可选成果失败都不能把已经健康的必需 MP4 改成失败。重试音轨时也不应重新下载 MP4；只读取受管归档中的健康视频，再生成缺失成果。

## 三、子进程与不可信媒体边界

Issue #6 的固定参数约束应落实为代码结构，而不只是约定：

- 用 `subprocess` 的参数数组调用，禁用 shell；
- 可执行文件路径来自应用资源目录，输入/输出路径只来自受管归档和应用生成的临时路径；
- 用户文案、命名模板、作品标题和 URL 都不得成为 FFmpeg 选项或滤镜表达式；
- 设置硬超时、终止整个子进程树，并限制捕获的 stderr 大小；
- 普通日志只记录内部错误码、退出码和归档 ID，不记录完整命令、媒体 URL、描述文本或绝对媒体路径；
- MOV/MP4 demuxer 的 `enable_drefs` 和 `use_absolute_path` 默认关闭，官方文档明确警告启用它们可能泄露信息或带来安全风险，本项目不得启用；
- 提取写入同目录的应用临时文件，校验后原子替换，失败时删除临时文件，不触碰用户目录中的任意同名文件；
- 将 `ffmpeg.exe` / `ffprobe.exe` 当作处理不可信媒体的本地解析器，纳入依赖升级和安全公告响应流程。

## 四、对 Issue #6 的可执行建议

1. 在代码中定义 `MediaToolchain`/`AudioExtractor` 端口，生产适配器只接受 `Path` 和固定操作类型；测试适配器返回结构化探测/提取结果，不在业务层拼命令。
2. 先用 ffprobe 判断 `no_audio`，再执行固定 stream-copy 命令，最后用另一次 ffprobe 独立校验；三个阶段各自拥有超时和错误码。
3. 输出先写受管目录内随机临时名，显式 `-f ipod`，校验通过后原子落为 `<base>.audio.m4a`。
4. TXT 文案成果直接按 UTF-8 写入原始作品 description，不经过 FFmpeg，也不得混入字幕、语音转写或平台音乐信息。
5. 将“补充成果”实现为对健康 MP4 的幂等操作：已有且校验健康则跳过，缺失/损坏才重建，不重新下载视频。
6. 把 FFmpeg 审计清单、许可文本、对应源码说明和构建说明纳入 Windows 构建测试；缺一项即构建失败。
7. 先以合成的“有 AAC 音轨 MP4”“无音轨 MP4”“损坏 MP4”“容器不接受的音频编码”“时长不匹配输出”覆盖状态机，再使用获得授权的真实样本做兼容性回归。

## 五、仍存风险与待确认事项

- **二进制供应尚未确定：** 仅靠 FFmpeg 官方资料无法确认某个第三方 Windows 构建的最终许可和对应源码。选定具体供应物后必须进行第二次、面向该 artifact 的审计。
- **GPL 与应用边界：** 独立进程调用通常比库链接边界清楚，但本记录不判断特定安装包、EULA 和分发方式下应用本身是否受 GPL 影响。
- **专利与地区法规：** FFmpeg 官方 legal 页面明确提醒专利问题与具体司法辖区相关，开源许可合规不等于自动解决编解码器专利问题。
- **M4A 编码兼容性：** 抖音 MP4 通常预期为 AAC 音频，但产品不能把它当作永久保证。stream copy 无法写入 `ipod` muxer 时，应准确失败而不是静默转码；是否未来提供显式转码选项需要另开需求。
- **多音轨选择：** `a:0` 的产品语义需要真实样本验证。如果存在旁白、多语言或非默认首音轨，需要明确选择策略和 UI 表述。
- **时长阈值：** 1 秒/2% 是初始工程策略，必须通过样本测试校准，并记录极短视频、edit list 和异常时间戳的处理。
- **深度完整性：** ffprobe 结构探测不能发现所有后段坏包；如果现实数据表明有漏检，需要引入完整包扫描或解码校验，并评估性能成本。

## 官方资料

- [FFmpeg License and Legal Considerations](https://ffmpeg.org/legal.html)
- [FFmpeg 源码许可说明](https://ffmpeg.org/doxygen/trunk/md_LICENSE.html)
- [FFmpeg Download / Release Verification](https://ffmpeg.org/download.html)
- [FFmpeg Releases Index](https://ffmpeg.org/releases/)
- [ffmpeg Documentation：streamcopy、map、固定参数与通用选项](https://ffmpeg.org/ffmpeg.html)
- [ffprobe Documentation：select_streams、show_entries 与 JSON writer](https://ffmpeg.org/ffprobe.html)
- [FFmpeg Formats Documentation：MOV/MP4/M4A demuxer 与 ipod muxer](https://ffmpeg.org/ffmpeg-formats.html)
- [FFmpeg Installing Documentation](https://ffmpeg.org/doxygen/trunk/md_INSTALL.html)
