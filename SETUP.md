# 安装、同步与验收

需要 GitHub 账号，以及每台 Mac 上的 Python 3.9+、Git 和登录 Keychain。网页和记录器无第三方运行依赖；不需要 OpenAI / JD API 密钥、数据库服务器或购买域名。

以下 `YOUR_GITHUB`、`你的项目目录` 为占位值。身份数值 ID 可在已登录的 GitHub API 用户资料中查看，或运行 `gh api user --jq .id`；它不是用户名。

## 1. 建立两个仓库并发布网页

在 GitHub 的个人账号下建立：

| 仓库 | 可见性 | 内容 |
|---|---|---|
| `work-dashboard` | **Public** | 本项目代码、`docs/` 网页与虚构演示数据 |
| `work-journal` | **Private** | 初始化时创建一个 README，使用 `main` 分支；不添加其他协作者 |

将本项目推送到 `work-dashboard`。发布前确认 Git diff 里没有本地数据库、Token、真实项目名、真实小结或对话。`.gitignore` 只是辅助手段，不能替代检查已暂存文件。

在公共仓库 **Settings → Pages → Build and deployment** 选择 **Deploy from a branch**，来源选择 **main /docs**。等待 Pages 显示发布地址：

```text
https://YOUR_GITHUB.github.io/work-dashboard/
```

不要给私有 `work-journal` 开启 Pages，也不要让公共发布流程拉取私有仓库。公共仓库部署所需的全部资源已经在 `docs/` 内。

## 2. 网页只读 Token

在 GitHub **Settings → Developer settings → Personal access tokens → Fine-grained tokens** 创建专用 Token：

- Resource owner：自己的账号。
- Repository access：**Only select repositories → work-journal**。
- Repository permissions：**Contents: Read-only**；保留 GitHub 必需的 Metadata 读取权限。
- 设置有效期。无需授权其他仓库、Actions、管理权限或账号写入权限。

先完成下文主 Mac 的初始化和首次 `worklog sync`，生成 `projects.json`，再打开网页，填写 GitHub owner、私有仓库名、账号数值 ID 和只读 Token。网页检查当前账号 ID、仓库私有属性和读取权限，再读取数据；首次同步后没有工作记录时显示空状态。只有 README 的新仓库尚不能加载真实记录，可以先使用演示。

Token 仅在当前标签页内存中存在。不要把 Token 放进网址、截图、配置文件、源码或聊天。页面不会记住 Token；可以用自己的密码管理器保存。退出、刷新或关闭标签页都会清除访问状态。GitHub 撤销 / 过期 Token 后，下次请求失败并清空展示。

网页无法可靠替你证明输入的 Token 只有只读权限，因此务必在创建时按以上范围设置。Pages 登录页本身公开，私有数据受 GitHub 服务端授权保护。这里不提供 OAuth / SSO。

## 3. 主 Mac：安装与身份配置

在该仓库根目录运行；安装器将运行文件复制到 `~/Library/Application Support/Worklog/app/`：

```sh
./install.sh
worklog --help
worklog init --repo YOUR_GITHUB/work-journal --device 'Mac A' --primary
worklog auth
```

如果当前 shell 找不到 `worklog`，运行 `export PATH="$HOME/.local/bin:$PATH"` 为当前终端加入命令目录，或直接使用 `~/.local/bin/worklog`。也可以在仓库根目录使用 `./bin/worklog` 或 `python3 -m worklog`。

`init` 在本地建立配置、SQLite 和数据仓库工作区，不代表已上传。尚无认证时保留本地初始化结果；配置认证后再同步。

为这台 Mac 另建 fine-grained Token，同样只选择 `work-journal`，权限为 **Contents: Read and write**。运行 `worklog auth` 后，在隐藏输入提示中粘贴 Token。命令验证账号与私有仓库后保存在系统 Keychain，不写入 JSON / SQLite / Git remote。每台 Mac 使用不同的 Token，便于单独撤销。

不要把 Token 写在命令参数或 Git HTTPS URL 里。自动化输入只使用 `worklog auth --token-stdin` 从受保护的来源传入；不要在终端运行含真实 Token 的 `echo`、赋值或脚本。令牌设置属于 GitHub 账号操作，需要本人在 GitHub 完成。

本地状态默认在：

```text
~/Library/Application Support/Worklog/
```

这里包含项目路径映射、本地队列、游标和证据位置，因此仍属于私人数据。可通过全局 `--home` 或 `WORKLOG_HOME` 选择独立目录；不要把它放进公共代码仓库，也不要让两台电脑共用同一个 SQLite 文件。`--home` 只切换 Worklog 状态，不改变 Codex 数据目录。

## 4. 选择项目和采集目录

主 Mac 登记个人项目，并明确将名称 / 目标发布到私有项目目录：

```sh
worklog project add --id my-project --name '我的个人项目' \
  --path '/你的项目目录' --policy auto --publish \
  --goal '完成一组可以复现的核心对照实验'
```

同一项目的其他 checkout 或 worktree 通过重复 `--path` 登记。路径只留本地。使用稳定的项目 ID，之后不要因电脑或目录名变化而新建一个 ID。`--goal` 设置网页项目页的目标，和名称一样需要适合上传；`worklog status` 可查看已登记的项目 ID 与策略。

工作资料项目应选择 `review` 或 `local_only`：

```sh
worklog project add --id work-project --name '工作项目' \
  --path '/你的工作目录' --policy review
```

| 策略 | 行为 |
|---|---|
| `auto` | 结构化摘要通过隐私校验后可自动上传 |
| `review` | 本地保留，必须逐条选择后才可上传 |
| `local_only` | 仅本地，即使调用者请求上传也不会放行 |

默认是 `local_only`。JD 项目不得因使用哪个模型、在哪台电脑或网络可达就改为自动上传。`--publish` 明确将项目名称与目标写入私有 `projects.json`，主 Mac 才能维护这个共享目录；不发布的项目名称仅留本地。改为更严格的策略会关闭尚未上传记录的资格，已经同步的 Git 历史仍然存在。

仅登记需要补漏的 Codex 数据目录；常见个人配置位置为：

```sh
worklog source add --path "$HOME/.codex"
worklog scan --days 7
worklog status
```

其他 Codex 实例若使用不同数据目录，也需显式登记。不读取认证文件，不扫描未登记目录，不读取完整历史来自动生成摘要。初次检查限最近七天；无法识别的会话结构会显示异常。采集不到的桌面版本或其他工具可先手动记录。

采集器也读取已登记 Git 项目的提交元数据。工作成果状态仍需结构化小结与证据，提交次数不能证明实验成功。未知项目的活动留在本地待分类。

## 5. 技能与后台运行

`install.sh` 已把仓库的 `skills/worklog/` 安装到当前 `CODEX_HOME` 对应的技能目录，未设置时使用 `~/.codex/skills/worklog/`。若已有内容不同的同名技能，安装器会保留并提示，需先检查差异。其他独立 Codex 配置目录也需要在各自 `skills/` 下安装同一技能。重新打开 Codex 后可显式调用 `$worklog`。

技能按上下文自动发现，可使用：

> 把刚才完成的工作记下来，结果写清楚，实验没跑就标待测。
>
> 把这个 idea 记到 my-project，顺便写最小验证方法。
>
> 读取 my-project 的接力摘要，看看下一步是什么。

自动发现不保证每个任务都会主动调用技能。缺少小结时，扫描器只标记“活动已发现，摘要待补”。它不会复制最终回复，也不会额外调用 GPT。

确认单次扫描与同步正常后启用后台服务：

```sh
worklog sync
worklog service install
worklog status
```

服务在当前用户登录后运行，约每五分钟扫描并同步。Mac 关机或休眠时不运行，恢复后继续；离线时仍可采集并写入本地队列，远端同步失败后保留记录，联网后补传。没有变化时不制造空提交。服务使用 Keychain 已配置的认证，不需要模型可用。

停止后台服务用 `worklog service uninstall`。它不删除日志、Keychain 凭证或 GitHub 仓库。不要同时从多个进程修改同一个本地数据仓库；同步器会通过本地锁避免并发执行。

## 6. 写记录、审核与第二台 Mac

普通记录例子：

```sh
worklog record --project my-project --title '完善数据检查' \
  --summary '补齐缺失值与重复项检查' --result '本地检查通过；正式数据待测' \
  --next-action '用正式数据跑一次检查并保存摘要' --priority P1
worklog context --project my-project
```

仅声称“通过”不构成证据。没有证据时记录保持未核验。真实本地证据可以加 `--evidence-file '/实际结果文件路径' --evidence-level local`：命令计算 SHA256，云端仅保存指纹与设备 ID，路径保存在本地 SQLite。先检查文件内容确实支持结论；哈希一个文件不自动证明实验成功。来源关联使用 `--thread-id` / `--turn-id`。复杂文本可以用 `--json` 从 stdin 输入，字段示例在安装的 `worklog` 技能里；不要把绝对路径、原始日志或代码正文塞进摘要。

对需审核的摘要：

```sh
worklog review
worklog review --approve RECORD_ID
worklog sync
```

先阅读具体内容再选择记录 ID。过滤规则不能判断所有商业机密；上传到私有 GitHub 仍是上传。`local_only` 记录不能靠逐条审核放行，策略变更应是针对项目的明确决定。

第二台 Mac 重复安装、创建独立写入 Token 和认证，使用不同设备名，**不要加 `--primary`**：

```sh
worklog init --repo YOUR_GITHUB/work-journal --device 'Mac B'
worklog auth
worklog project add --id my-project --name '我的个人项目' \
  --path '/这台电脑上的项目目录' --policy auto
worklog source add --path "$HOME/.codex"
worklog sync
worklog context --project my-project
worklog service install
```

不要把 Mac A 的 Worklog 状态目录整体复制到 Mac B；新设备需要新的设备 ID 和独立队列。项目 ID 保持相同。代码由项目自己的 Git 更新；证据标为位于 Mac A 时，文件不会自动出现在 Mac B。

记录分设备写入：

```text
projects.json
devices/<设备ID>/days/YYYY-MM-DD.jsonl
devices/<设备ID>/snapshot.json
```

网页在一个固定 commit 上加载索引和历史。跨设备重复来源通过逻辑 ID 合并；不同修改同时修订同一记录时保留冲突。已经提交的记录不靠原地改历史纠错，应添加修订。停止某台 Mac 不会删除历史。

## 7. 故障、备份和验收

| 现象 | 处理 |
|---|---|
| 离线 / GitHub 不通 | 继续记录；查看本地 pending，恢复网络后 `worklog sync` |
| Token 过期 | 重新创建对应用途的 Token；Mac 运行 `worklog auth`，网页重新解锁 |
| 并发 push 失败 | 程序有界重试；持续失败查看状态，不 force push |
| 数据仓库有真实冲突 | 保留队列与工作区，先检查具体冲突；不要删除设备目录或覆盖远端 |
| 网页显示旧数据 | 查看同步页与远端提交；刷新页面数据；后台没有上传时网页不能补采 |
| API 限流 | 等待 GitHub 指示恢复；不要缩短轮询间隔或增加 Token 轮换 |
| Codex 格式不支持 | 保留异常提示，升级解析器；先手动补记，不上传原始会话 |
| 日志含敏感信息 | 不再上传，检查本地审核队列；已泄露 Token 先撤销 |

Git 的删除会保留历史。上传敏感内容后，只删最新文件不足以清除过去提交；需要单独处理仓库历史与本地副本，不能把普通删除当作永久擦除。

运行备份：

```sh
worklog backup
```

后台按周保留本地 Git bundle。备份包含私有日志，也必须存放在私人位置。Git bundle 可以恢复已提交仓库，**不包含尚未提交 / 尚未同步的 SQLite 队列、Keychain、项目代码或附件**。迁移前应先同步并检查状态。手动恢复验证可在临时私人目录使用 `git clone /实际备份路径/备份.bundle /私人恢复目录`，比对提交和日志后再决定替换。

首次启用需要核对以下行为；不要把本地模拟测试填成真实双机结果：

| 验收项 | 完成证据 |
|---|---|
| 页面权限 | 主人可读；匿名和另一无权限账号均无法读取私有记录 |
| 单 Mac 贯通 | 一条虚构工作、一条 idea 上传后网页出现，来源设备正确 |
| 离线补传 | 断网写记录，恢复后 remote 确认且网页只出现一次 |
| 双 Mac | 同时写入互不覆盖；Mac B 能读到 Mac A 的接力摘要 |
| 隐私 | 工作资料摘要待审核，未分类 / 仅本地项目未上传 |
| 认证撤销 | 撤销测试 Token 后下次读取失败，网页清空私有展示 |
| 备份恢复 | 在临时私人目录恢复 bundle，记录与提交一致 |
| 连续七天 | 对照实际工作，核对漏记、重复、归属、摘要质量和耗时 |

七天试用期间只根据真实观察调整采集和小结习惯；自动化活动索引不能覆盖会议、纸面阅读或没有被主动记下的想法。
