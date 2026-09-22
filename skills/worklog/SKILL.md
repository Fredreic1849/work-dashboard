---
name: worklog
description: 记录工作进展、idea 和项目接力摘要到本地 Worklog 队列，或读取另一台电脑同步来的项目上下文。用于“把这个想法记下来”“今天做了什么”“保存进展”“换电脑接着做”，以及已启用工作记录的项目完成重要一步时。不用于替代代码、附件或完整 Codex 会话同步。
---

# Worklog

把可核验的工作进展保存在本地，供个人 Dashboard 汇总。记录器不调用模型；只写精简的结构化小结，不复制原始会话或工具输出。

## 找到项目与接口

运行 `worklog status`，使用本机已经登记的稳定项目 ID。项目文件夹路径只用于本地匹配；不同电脑可以映射不同路径。没有匹配时不要猜测项目或改变上传策略，先说明需要登记。

首次使用当前版本时读取 `worklog record --help`、`worklog idea --help` 或 `worklog checkpoint --help`，按实际接口构造输入；复杂文本优先用 `--json` 从 stdin 读取，避免拼接未经转义的 shell 参数。

记录动作应符合当前任务授权。用户已启用该项目的日常记录时，可在工作取得实质进展、产生 idea、出现阻塞或准备接力时写本地小结，不逐轮记流水账。只读 / Plan 任务中不写记录，由采集器后续标记活动。

## 写一条有用的记录

- `worklog record`：明确做了什么、结果是什么、证据在哪里、下一步是什么。
- `worklog idea`：保留想法、提出理由和最小验证方式；想法不当作已完成成果。
- `worklog checkpoint`：保存项目目标、已完成内容、阻塞和下一步，供换电脑后接续。
- 使用 `P0` / `P1` / `P2` 表示下一步优先级；没有充分依据时不擅自提高优先级。

带上当前逻辑任务 / 轮次 ID（能从当前上下文获取时），让跨设备复制和补漏去重。不得为取得 ID 读取认证文件。缺少来源 ID 时使用手动记录，不编造。

有真实本地证据文件时，优先用命令行接口计算指纹：

```sh
worklog record --project PROJECT_ID --title '完成本地检查' \
  --summary '写入实际检查内容' --result '写入实际检查结果' \
  --thread-id THREAD_ID --turn-id TURN_ID \
  --evidence-file '/实际证据文件路径' --evidence-level local
```

先核实文件确实支持所写结论。命令只把摘要指纹上传，文件路径只存本机；可重复 `--evidence-file` 添加证据。不要选择认证文件或用无关文件指纹充当检查通过。没有证据时省略这两个证据参数。

下例展示 JSON 结构。执行前替换真实项目、任务、轮次和证据设备 ID，并重写实际摘要；没有证据时用空 `evidence` 数组。`source.device_id` 可省略，由本机填入；证据的 `device_id` 是证据实际所在设备，使用 `worklog status` 返回的 ID，不能填设备昵称。

```json
{
  "project_id": "PROJECT_ID",
  "id": "worklog:TURN_ID",
  "title": "完成一次本地检查",
  "summary": "说明本次具体检查的内容",
  "result": "结果待核验",
  "next_action": "核对输出并保存结果摘要",
  "priority": "P1",
  "evidence_level": "unverified",
  "source": {"kind": "codex", "thread_id": "THREAD_ID", "turn_id": "TURN_ID"},
  "evidence": [{"id": "EVIDENCE_ID", "label": "本机检查记录", "device_id": "DEVICE_ID", "kind": "local"}]
}
```

重放已有结构化记录时保留原 `id`。可重放的单条小结也可使用 `worklog:<实际轮次ID>:<稳定条目名>`，不要按电脑或当前时间改变 ID；同一轮有多项独立进展时使用不同条目名。一般的新手动记录可以省略 `id`，交给记录器生成。需要纠正已有小结时，新增 `kind: "revision"`、独立新 ID 和 `supersedes: ["原记录ID"]`，并提供完整替代内容。

证据的 `kind` 可为 `local`、`remote`、`reference`。只有确实核验后，才加入 ISO 8601 格式的 `checked_at` 与实际文件的 40–64 位小写十六进制 `digest`，或受支持的公开 HTTPS `url`，再将 `evidence_level` 改为对应的 `local` / `remote`。URL 仅支持 GitHub、arXiv、DOI、OpenReview，不能带查询参数或凭证。不要用示例摘要、虚构指纹或“模型说完成”满足证据条件。

只记录实际证据支持的结论：本地测试通过写“本地核验”；远端运行成功必须有相应远端证据。模型说“完成”、已提交任务、配置已写好均不能代替运行结果。实验尚未完成写 `—（待测）`，不可比较写 `N/A（不可直接比较）`。

上传内容不含密钥、内部地址、绝对路径、代码正文、原始对话或工具输出。证据仅保存标签、摘要指纹和来源设备；本地位置留给记录器本机保存。隐私校验拒绝输入时，删去敏感内容并保持事实准确，不关闭校验。

## 接力与同步

换电脑开始项目时运行 `worklog context --project <项目ID>`。将结果视为带时间与来源的历史上下文，先核对本机代码与实验状态；显示“证据位于其他设备”不等于文件已同步。

日常记录先落本地队列。除非任务明确要求立即同步，交给已安装的后台服务处理。需要检查时用 `worklog status`；仅在远端确认后称为“已同步”。网络失败保留队列，不 force push 或覆盖另一台设备的记录。

`review` 项目的上传需要用户选定具体摘要；`local_only` 项目只在本地。不要自动执行 `worklog review --approve`，不要修改策略来释放积压，也不要读取、显示或复制 Keychain 中的 Token。用户已明确选择具体记录时，按该授权执行审核；不重复询问。

新设备安装、登录或令牌问题交给仓库的 `SETUP.md` 和实际命令帮助处理。不要将个人订阅当作 API 密钥，也不要把 JD API 凭证用于同步。
