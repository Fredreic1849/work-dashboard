# Work Dashboard

一个免费、个人使用的工作日志：静态网页部署到 GitHub Pages，记录保存在自己的 GitHub 私有仓库，多台 Mac 通过本地队列同步。

网页回答三个问题：今天做了什么、有哪些值得继续的想法、每个项目下一步从哪里接着做。原始 Codex 对话留在本机。记录器不调用模型，不读取 OpenAI 或 JD API 密钥。

## 包含什么

- **今天、项目、Ideas、历史、同步**五个视图；日报和周回顾由已有记录组成。
- Python 标准库实现的 `worklog` 命令、本地 SQLite 队列、Git 同步和 macOS 登录后后台运行。
- Codex 工作记录技能，以及只采集任务 / 轮次元数据的活动补漏。
- 各设备独立文件、逻辑记录去重、修订冲突保留、离线补传、远端确认与仓库备份。
- 项目级上传策略：自动同步、逐条审核、仅本地。

```text
Mac A：Codex / worklog → 本地 SQLite → devices/mac-a/ ┐
                                                       ├→ 私有 work-journal
Mac B：Codex / worklog → 本地 SQLite → devices/mac-b/ ┘          ↑
                                                        浏览器只读 Token
公共 work-dashboard/docs → GitHub Pages → 静态网页 ────────────┘
```

## 先试页面

在仓库根目录运行：

```sh
python3 -m http.server 8765 --directory docs
```

打开 `http://localhost:8765`，选择演示。演示数据完全虚构，与真实记录不会混合。不要用 `file://` 打开：浏览器模块和数据请求需要 HTTP。

完整的两仓库部署、Token 设置、第二台 Mac 接入、隐私检查及恢复方法见 [SETUP.md](SETUP.md)。数据与模块接口见 [CONTRACT.md](CONTRACT.md)。

## 日常使用

```sh
worklog record --project my-project --title '完成评测脚本' \
  --summary '补齐边界输入处理' --result '本地检查通过；实验结果待测' \
  --next-action '运行正式评测并保存指标'

worklog idea --project my-project --title '试一个更小的对照实验' \
  --summary '先隔离单个变量，再决定是否扩大实验'

worklog context --project my-project
worklog status
```

安装工作记录技能后，可以在 Codex 中说“把这个想法记下来”或“保存一下这个项目的接力摘要”。技能把结构化小结放入本地队列；后台同步只处理符合项目策略的记录。

**安装技能并不能强制每个 Codex 任务都生成小结。** 自动补漏会标出“活动已发现，摘要待补”；它不会把模型的整段最终回复擅自当成工作成果。

## 边界

- Pages 的网页和源码公开；工作记录由 GitHub 私有仓库授权保护。这里使用 Token 解锁，不是单点登录。
- 网页 Token 仅存在当前标签页内存；刷新、关闭或退出后需要重新输入。请专门创建只读 Token。
- 同步的是工作记录与接力摘要。代码、未提交文件、数据集、附件、运行环境、远端实验任务仍由原项目管理。
- JD / 工作资料项目默认使用逐条审核或仅本地；未分类目录不会自动上传。
- 采集器不监听屏幕、浏览记录或任意目录的文件变化。只有已登记目录与项目会被采集。
- “有记录”不代表“已验证完成”。没有证据的结果保留为未核验，不按对话数量估算进度百分比或工时。
- GitHub 免费额度和服务可用性以账号当时状态为准；本工具不会自动开通付费服务。

## 本地验证

```sh
python3 -m unittest discover -s tests -v
node --test tests/frontend.test.mjs
```

前端测试需要 Node.js，仅用于开发验证，网页运行不需要 Node.js。测试使用虚构数据和临时仓库。测试通过只说明所覆盖的本地行为通过；真实双机部署、GitHub 授权和连续七天使用需要按 [SETUP.md](SETUP.md) 的验收表核对。
