# CLAUDE.md

## 本地测试资源

局域网内有一台跑本地翻译模型的测试机（IP: 192.168.32.62，用户名 zhou），部署在
`~/sakura_deploy/ollama`，跑 ollama 服务，监听 `11434` 端口，同时暴露 ollama 原生
API（`/api/chat`、`/api/tags`）和 OpenAI 兼容 API（`/v1/chat/completions`、
`/v1/models`）。当前只装了 `sakura-galtransl:latest`（Qwen2 7.6B，Q5_K_S 量化，
声称 context length 131072，实际可用上限没测过）。GPU 是 RTX 3060 12GB。登录密码
不记录在这里，找用户当面要；测试用 `paramiko` 连接（项目 venv 里没默认装）。

对应 `sakura_prompt.py` 的 `SAKURA_PROMPT_STRATEGY` 适配的就是这个模型系列，改
prompt/协议可以直接拿这台机器实测，不用等真机跑几万行才发现协议不对。

## git push 连不上代理

本机 git 全局配置了走代理 `http://127.0.0.1:7890`，代理没开时 `git push` 会报
`Failed to connect to 127.0.0.1 port 7890`。不要改 `.gitconfig` 里的代理设置——
账号级配置，会影响这台机器上所有仓库，即使用户口头授权也不例外。遇到这个报错先
试一次性绕开代理、不改配置文件：`git -c http.proxy="" -c https.proxy="" push`
（这台机器实测不挂代理也能直连 GitHub）；如果这条也连不上，再让用户手动把代理
进程启动起来。

## 翻译 context / 批量策略结论

已实测并落地的结论（对应代码见 `batch_translator.py` 的 `_chunk_jobs_by_group`/
`DEFAULT_BATCH_SIZE`、`sakura_prompt.py` 里引用本节的注释）：

- 人称代词解析对 context 量不敏感，但角色名音译的一致性会在**多次独立 API 调用
  之间**漂移，同一次调用内部反而是自洽的——所以同一事件页面的连续台词现在按顺序
  打包进同一次请求（段落进段落出），不再给每条各自拼一份 context。跨请求的译名
  漂移得靠术语表/记忆库解决，不是打包本身能顺带解决的。
- 打包提交比拆成多条各带一点 context 的单独请求省 token 得多（差距接近一个数量
  级），主因是拆开后每次都要重复付一遍固定模板/system prompt 的 token。
- 打包也不是越大越好：批次越大，命中"至少一条译文格式跑偏导致整批解析失败"的
  概率越高。默认批量大小已从 50 调到 20（本地/在线模型都受益）。

DeepSeek/SiliconFlow 云端模型那侧当时的对照测试没跑完，如果要验证以上结论在云端
模型上是否一致，需要重新跑一遍。
