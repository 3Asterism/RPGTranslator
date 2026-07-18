# 翻译失败自动重试 + 手动重试按钮 + 设置按钮视觉修复

## 背景

现有断点续传机制（db 级别，`units.db` 里 `status="pending"` 的条目不会被覆盖）已经支持"关掉软件重开、重新点开始翻译续译剩余部分"。但失败条目要等用户手动重新点一次「开始翻译」（会重新走 extract + 术语抽取两步）才会重试，没有同一轮内的自动重试，也没有跳过 extract/术语抽取、直接只重试失败项的入口。

另外，「设置」目前是挂在原生菜单栏上的纯文字 `QAction`，和其它区块的卡片+按钮视觉语言脱节，容易被当成不可点的标签。

## 范围

1. `translate_units` 完成一轮 pending 批次后，若有失败条目，自动原地重试最多 2 轮，轮间隔 5 秒。
2. GUI 翻译完成后如果仍有失败条目，展示「重试失败项」按钮，点击直接起 `TranslateWorker`（跳过 extract/术语抽取）。
3. 「设置」从菜单栏文字项改成右上角的 `QPushButton`（复用 `secondaryButton` 样式）。

不在范围内：改变失败判定逻辑本身（仍由 `LLMClient` 决定哪些错误可重试/换 provider）、改变翻译包导出格式、新增设置项（重试轮数/间隔不做成可配置项）。

## 架构 / 数据流

### 1. 自动重试（`translate/batch_translator.py`）

`translate_units` 现在的结构：分组 → 建 job → 分批 → `asyncio.gather` 跑完所有批次 → 返回 `failures`。

改动：跑完所有批次后，如果 `failures` 非空，进入重试循环（最多 2 轮）：

```
attempt 0（已有逻辑，不变）：跑完所有 pending 分组，得到 failures_0
for retry_round in [1, 2]:
    if not failures_0:
        break
    if cancelled(): break          # 停止请求：不进入/不再等待重试
    await asyncio.sleep(5)（用可中断的等待，等待期间也响应 cancel）
    if cancelled(): break
    failed_source_texts = {f[0] for f in failures_0}
    retry_jobs = 从原 jobs 里挑出 source_text 在 failed_source_texts 里的
    failures_0 = 重新跑这些 retry_jobs（复用现有单条/批量翻译路径），得到本轮新的失败列表
返回最终 failures_0
```

关键点：
- 重试对象是**同一批 `_Job`**（已经 `protect()` 过的 protected_text/mapping），不重新从 store 读取——避免重试期间 store 状态被并发改动导致的竞态；写回时仍然用 `store.update_translation` / `_write_result`，逻辑复用现有 `_translate_batch`/`_translate_single_job`。
- `on_progress` 回调：重试轮不重置 `completed/total` 计数——`total` 在函数一开始就固定为 `len(jobs) + len(cache_hits)`，重试轮里每条最终成功或最终失败都不再重复计入 completed（此前第一轮失败时已经计过一次）。为避免进度条被重复推进，重试轮的成功/失败改为直接更新 store + 只发一条日志级别的 `on_progress` 提示（不改变 completed 计数），细节在实现时用一个 `is_retry` 标记跳过 `completed += 1`。
- `_StopRequested`（用户点停止导致的中断）不计入 `failures`，重试循环也不会处理它们——它们本来就还是 `pending`，等下次正常重跑。
- 等待用 `asyncio.sleep`，配合 `cancel_check` 轮询（参考 `_CANCEL_POLL_INTERVAL` 的做法，把 5 秒等待拆成多个小睡眠 + 检查，保证点停止后不用傻等 5 秒）。

### 2. GUI 「重试失败项」按钮（`gui/main_window.py` + `gui/workers.py`）

- `TranslateWorker` 不用改（已经是"只翻 pending"，重试就是再跑一次）。
- `MainWindow._on_translate_done(unit_count, failures)`：
  - `failures` 非空时，除了现有日志输出，把新按钮 `self._retry_failed_button` 设为可见（初始隐藏，`secondaryButton` 样式，放在 `start_row` 里、`_stop_button` 右边）。
  - `failures` 为空时隐藏该按钮。
- 新增 `_on_retry_failed_clicked`：直接复用 `_on_extract_glossary_done` 里起 `TranslateWorker` 那段逻辑（抽成一个 `_start_translate_worker()` 私有方法，两处调用），不再触发 `ExtractAndGlossaryWorker`。
- 点击后同样接到 `finished_ok -> _on_translate_done`，如果还有失败会再次显示按钮，允许反复手动重试。

### 3. 设置按钮视觉修复（`gui/main_window.py`）

- 删除 `settings_action = self.menuBar().addAction("设置")`。
- 新增一个顶部 header row：`QHBoxLayout`，左侧 `QLabel("RPG Maker 汉化工具")`（可选，用 objectName 加个标题样式）+ `addStretch(1)` + 右侧 `QPushButton("⚙ 设置")`（`objectName="secondaryButton"`），插入到 `central` layout 最前面（`project_box` 之前）。
- 点击信号仍接 `self._open_settings`。
- 不新增图标资源、不新增 QSS 规则——`⚙` 是 unicode 字符，字体走现有 `"Microsoft YaHei UI", "Segoe UI"`，`secondaryButton` 样式已存在。

## 错误处理

- 重试轮内部的异常处理路径和现有 `_translate_batch`/`_translate_single_job` 完全一致（复用，不新写一套）：可重试的网络错误已经在 `LLMClient.chat` 内部处理过重试+故障转移，`translate_units` 层面看到的“失败”本来就是已经用尽底层重试手段的结果——上层重试的意义是应对“当时所有 provider 恰好都在限流/抖动，隔几秒会恢复”的场景，而不是重复无意义的立即重试。
- 3 轮（1 正常 + 2 重试）全部失败的条目，最终行为不变：保留 `status="pending"`，计入返回的 `failures`，供 GUI 展示 + 手动「重试失败项」按钮继续处理。
- GUI 侧「重试失败项」按钮点击后如果又失败，按钮保持/重新显示，不限制手动重试次数。

## 测试

- `tests/test_batch_translator.py` 新增：
  - 一个 stub 前 N 次调用报错、之后成功，验证 `translate_units` 自动重试后最终标记为 `translated`，且不再出现在返回的 `failures` 里。
  - 一个恒定失败的 stub，验证重试满 2 轮后仍然失败，返回 `failures` 且 store 里保持 `pending`；调用次数符合 `1 + 2` 轮预期（结合 dedup/batch 规则）。
  - 结合 `cancel_check`：重试等待过程中置位取消，验证不会真的等满 5 秒 / 不会发起新一轮重试请求，剩余条目保留 `pending`。
- GUI 部分（`tests/test_gui.py`，如果现有测试有覆盖 `_on_translate_done` 之类的逻辑）视现有测试基建情况补最小验证：`failures` 非空时重试按钮可见性、点击后调用了 `TranslateWorker` 而不是 `ExtractAndGlossaryWorker`。
- 设置按钮改动是纯视觉/布局改动，不需要新增自动化测试，跑一遍 GUI 手动确认按钮可点、样式和其它 `secondaryButton` 一致即可。
