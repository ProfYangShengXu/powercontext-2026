# 记仇的酒馆 NPC —— Dify × PowerContext

> 赛题：Dify —— Just for Fun（20 分）
> 一句话：你在酒馆里每整店小二阿福一次，他都**真实写进 PowerContext**；下一轮他会把旧账一件件念出来，并据此升级反应。

---

## 0. 三十秒看懂

| 你做什么 | 发生什么 |
| --- | --- |
| 在 Dify Chatflow 里说「我把盐当成糖放进了你的茶里」 | ① 写进 PowerContext 记忆 ② 全文检索 ③ 读回该会话全部记忆，阿福记下第 1 笔账 |
| 同一会话再说「我又把你的板凳腿锯短了一截」 | 阿福念出第 1 笔 + 第 2 笔，台词升级 |
| 换一个新对话 | Dify 生成新的 `conversation_id` → sidecar 映射到新的 PowerContext scope → 阿福「失忆」，从头开始 |

记忆不是 Dify 的 conversation variable，而是真实落在 PowerContext 的 Memory 里；停掉 sidecar 后流程会**报错**，不会静默编答案。

---

## 1. 硬要求对照

| 题面要求 | 本交付 |
| --- | --- |
| 必须使用 Dify | `dify_tavern_npc.yml`：Dify 1.18.0 Chatflow（`mode: advanced-chat`），导入即用 |
| 实际使用 seekdb / PowerContext 至少一种 | PowerContext 1.2.0：每个会话真实调用 `POST /v1/memory/remember`、`/v1/memory/search`、`/v1/memory/entries/list`、`/v1/memory/changes` |
| 至少两个 HTTP Request 节点真调外部服务 | 3 个：`/memory/write`、`/memory/search`、`/memory/list` |
| 配套 sidecar（Python 标准库） | `sidecar/`：只用 `http.server` + `urllib`，无第三方依赖 |
| DSL / 运行说明 / 验证样例 | 本 README + `samples/` |
| 技术结合声明 | 见 §2、§3 |

---

## 2. 技术结合声明（一句话）

**PowerContext 是唯一的记忆存储与检索层**：Dify Chatflow 的 `① 写入记忆` 节点把本轮输入经 sidecar 写入 PowerContext 的会话 scope；`② 检索记忆` 节点对同一 scope 做全文检索；`③ 读取全部记忆` 节点读回该 scope 的全部 Memory 条目；Code 节点把这两份真实返回合成阿福的回复——检索结果直接决定回复里念出哪几笔旧账。

一轮的数据流：

```
Dify Chatflow (advanced-chat)
  Start
   ├─ HTTP ①  POST {SIDECAR}/memory/write   body=用户输入  header X-Session-Id={{sys.conversation_id}}
   │            sidecar → PowerContext POST /v1/memory/remember
   ├─ HTTP ②  POST {SIDECAR}/memory/search  body=用户输入
   │            sidecar → PowerContext POST /v1/memory/search (FTS)
   ├─ HTTP ③  POST {SIDECAR}/memory/list
   │            sidecar → PowerContext POST /v1/memory/entries/list + /v1/memory/changes（还原插入顺序）
   ├─ Code    用 ①②③ 的真实 JSON 合成回复；任何一侧失败就抛错
   └─ Answer  {{#compose_reply.reply#}}
```

sidecar 只做三件事：会话 id → PowerContext scope 的映射、输入校验、错误码透传。它**不返回任何假数据**：每个成功响应都来自一次真实的 PowerContext 调用。

---

## 3. 目录

```
dify/
├── dify_tavern_npc.yml        # Dify DSL（导入这个文件）
├── README.md                  # 本文件
├── powercontext.env.example   # PowerContext server 最小配置
├── run_sidecar.sh             # 一键启动 sidecar
├── sidecar/
│   ├── pc_client.py           # 标准库 PowerContext HTTP 客户端
│   ├── npc_persona.py         # 回复合成 main()（DSL Code 节点的唯一来源）
│   └── server.py              # 标准库 HTTP sidecar
├── tools/
│   ├── build_dsl.py           # 由 npc_persona.py 生成 DSL
│   ├── validate_dsl.py        # 用 graphon 0.8.0 官方模型校验 DSL
│   └── dify_smoke.py          # 对已部署的 Dify 跑三轮冒烟（key 走环境变量）
├── samples/
│   ├── demo.jsonl             # 7 轮、3 个会话的可回放输入
│   ├── replay.py              # 按 DSL 同样三次调用回放
│   ├── replay_output.jsonl    # 回放的真实原始响应
│   ├── transcript.md          # 回放生成的三段交互记录
│   ├── dify_run.jsonl         # 在真实 Dify 1.18.0 上跑出的三轮记录
│   └── dify_failure.txt       # 真实 Dify 停 sidecar 的报错记录
└── deploy/  .runtime/  .cache/ # 本机 Dify 部署/构建临时目录（.gitignore，不进提交）
```

---

## 4. 前置条件

- **Dify 1.18.0**（自托管，Docker Compose）。本 demo 的图里**没有 LLM 节点**，所以不需要任何模型 API Key。
- **Python 3.11+**（sidecar 只用标准库）。
- **PowerContext 1.2.0**：`uv tool install "powercontext[cli,server]==1.2.0"`。默认 SQLite 存储 + 全文检索，不需要模型、不需要独立数据库服务。
- 可选：想用 seekdb 作为 PowerContext 的存储后端，见 §10。

---

## 5. 从零到可玩

### 5.1 启动 PowerContext

```bash
uv tool install --force "powercontext[cli,server]==1.2.0"

cp powercontext.env.example powercontext.env
# 编辑 powercontext.env：把 DATABASE_URL 换成你的绝对路径（注意是四个斜杠）
#   POWERCONTEXT_SERVER_DATABASE_URL=sqlite+aiosqlite:////<your-absolute-path>/powercontext.db

powercontext config validate --env-file powercontext.env
powercontext server run --env-file powercontext.env
```

看到下面两行即为就绪（端口以 `powercontext.env` 为准，示例用 18080）：

```
PowerContext Server is ready
Uvicorn running on http://127.0.0.1:18080
```

健康检查：

```bash
curl -s http://127.0.0.1:18080/health/ready -o /dev/null -w '%{http_code}\n'   # 200
```

### 5.2 启动 sidecar

```bash
./run_sidecar.sh
# 等价于：python3 sidecar/server.py --port 8787 --powercontext-url http://127.0.0.1:18080
```

```bash
curl -s http://127.0.0.1:8787/health
# {"ok": true, "sidecar": "dify-tavern-sidecar", "powercontext": {"url": "...", "status": 200, "ready": true}, ...}
```

sidecar 监听 `0.0.0.0:8787`，供 Dify 容器访问。

### 5.3 启动 Dify

用 Dify 官方 1.18.0 的 `docker/` 目录：

```bash
git clone --depth 1 --branch 1.18.0 https://github.com/langgenius/dify.git
cd dify/docker
cp .env.example .env
```

至少改这几项：

```dotenv
# 1) 必填：1.18 的 agent_backend / local_sandbox 启动令牌（openssl rand -base64 32）
DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN=<随机串>

# 2) 端口（避免和本机 80 冲突）
EXPOSE_NGINX_PORT=8088
EXPOSE_NGINX_SSL_PORT=8443

# 3) 允许 HTTP 节点访问本机 sidecar
#    Docker Desktop 用 host.docker.internal；Linux 需要给 api 容器加
#    extra_hosts: ["host.docker.internal:host-gateway"]
SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=host.docker.internal
SSRF_PROXY_ALLOW_PRIVATE_IPS=172.16.0.0/12,10.0.0.0/8,192.168.0.0/16
```

> **为什么必须改第 3 项**：Dify 1.18 的 HTTP Request 节点出站走 squid `ssrf_proxy`，默认拒绝私网/回环。不改会表现为「连接失败」，很容易误判成 sidecar 没起。

```bash
docker compose up -d
# 首次启动会跑数据库迁移，等 1~3 分钟
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8088/   # 200
```

打开 `http://127.0.0.1:8088`，首次进入按提示创建管理员账号（**不需要配置任何模型**）。

> 如果 `docker compose exec api curl http://host.docker.internal:8787/health` 返回 000（本机这套 Docker Desktop/WSL2 就是如此），不要纠缠 DNS：把 `SIDECAR_BASE_URL` 改成宿主机在容器网络里可达的 IP（WSL 用 `hostname -I` 的第一项，或宿主 LAN IP），并确保该网段在 `SSRF_PROXY_ALLOW_PRIVATE_IPS` 里。squid 只做 CIDR 放行，不做端口映射。

### 5.4 导入 DSL

1. 右上角头像 → **设置 / Settings** 里确认没有模型也能跳过；
2. 顶部 **工作室 / Studio** → **创建空白应用** 旁的下拉 → **导入 DSL / Import DSL File**；
3. 选择本目录的 `dify_tavern_npc.yml`；
4. 导入后打开应用 → 右上角 **环境变量**，确认 `SIDECAR_BASE_URL`：
   - Dify 在 Docker、sidecar 在宿主机：`http://host.docker.internal:8787`
   - sidecar 也在同一个 compose 网络：`http://sidecar:8787`
   - Dify 和 sidecar 都直接跑在宿主机：`http://127.0.0.1:8787`
5. 点 **运行 / Run**（预览即可，无需先发布）。

> 本机已用 Dify 1.18.0 实际导入并跑通（见 §7 ⑦）。本机 Dify 在 Docker、sidecar 在 WSL，`host.docker.internal` 在这套 Docker Desktop 上没有解析，因此验证时把应用环境变量临时改成了 WSL 的 IP（`http://<wsl-ip>:8787`）；**提交的 DSL 默认值仍是 `host.docker.internal`**，评审按自己的部署形态改这一处即可。

### 5.5 开始玩

在预览对话里依次输入（正好是 `samples/demo.jsonl` 的前三条）：

```
我把盐当成糖放进了阿福的茶里
我又把你的板凳腿锯短了一截
今天茶里我什么都没放，真的。
```

你会看到阿福每一轮都把之前的账念出来。**保持在同一个对话里**才会累积；点「新对话」等于换了一个 PowerContext scope，阿福会「失忆」。

---

## 6. 验证样例（本机真实跑出来的记录）

完整记录见 [`samples/transcript.md`](samples/transcript.md)，原始响应见 [`samples/replay_output.jsonl`](samples/replay_output.jsonl)。下面三段取自其中。

### 样例 A：`tea` 会话，三轮累积（第 2、3 轮读到了第 1 轮写的记忆）

**你**：我把盐当成糖放进了阿福的茶里

```
（阿福把抹布往肩上一搭，斜眼看你）
「又——是——你。」
「你在我这儿留下的 1 笔账，我可一件没忘：」
  1. 我把盐当成糖放进了阿福的茶里
「第一次，我当你手滑。」
（PowerContext · scope scp_7bjh6jr9ctas… · 记忆 revision 1 · 本次检索命中 1 条）
```

**你**：我又把你的板凳腿锯短了一截

```
（阿福把抹布往肩上一搭，斜眼看你）
「又——是——你。」
「你在我这儿留下的 2 笔账，我可一件没忘：」
  1. 我把盐当成糖放进了阿福的茶里
  2. 我又把你的板凳腿锯短了一截
「第二次，我当你喝多了。」
（PowerContext · scope scp_7bjh6jr9ctas… · 记忆 revision 2 · 本次检索命中 1 条）
```

**你**：今天茶里我什么都没放，真的。

```
（阿福把抹布往肩上一搭，斜眼看你）
「又——是——你。」
「你在我这儿留下的 3 笔账，我可一件没忘：」
  1. 我把盐当成糖放进了阿福的茶里
  2. 我又把你的板凳腿锯短了一截
  3. 今天茶里我什么都没放，真的。
「第三次……你猜我为什么把茶壶换成了铁的？」
「上次你提『我把盐当成糖放进了阿福的茶里』的时候，我就该防着你。」
（PowerContext · scope scp_7bjh6jr9ctas… · 记忆 revision 3 · 本次检索命中 2 条）
```

### 样例 B：`stool` 会话，第二轮读回第一轮

**你**：我把你的抹布藏到酒窖最里面了

```
（阿福把抹布往肩上一搭，斜眼看你）
「又——是——你。」
「你在我这儿留下的 1 笔账，我可一件没忘：」
  1. 我把你的抹布藏到酒窖最里面了
「第一次，我当你手滑。」
（PowerContext · scope scp_5nj0k0fn7rn9… · 记忆 revision 1 · 本次检索命中 1 条）
```

**你**：对了，你放在门口的扫帚我拿去扫马厩了

```
（阿福把抹布往肩上一搭，斜眼看你）
「又——是——你。」
「你在我这儿留下的 2 笔账，我可一件没忘：」
  1. 我把你的抹布藏到酒窖最里面了
  2. 对了，你放在门口的扫帚我拿去扫马厩了
「第二次，我当你喝多了。」
（PowerContext · scope scp_5nj0k0fn7rn9… · 记忆 revision 2 · 本次检索命中 1 条）
```

### 样例 C：`dup` 会话，重复的话不会重复记账

**你**：我在你账本上画了一只乌龟

```
（阿福把抹布往肩上一搭，斜眼看你）
「又——是——你。」
「你在我这儿留下的 1 笔账，我可一件没忘：」
  1. 我在你账本上画了一只乌龟
「第一次，我当你手滑。」
（PowerContext · scope scp_58kd0jb0kmvg… · 记忆 revision 1 · 本次检索命中 1 条）
```

**你**：我在你账本上画了一只乌龟

```
（阿福把抹布往肩上一搭，斜眼看你）
「又——是——你。」
「你在我这儿留下的 1 笔账，我可一件没忘：」
  1. 我在你账本上画了一只乌龟
「第一次，我当你手滑。」
「『我在你账本上画了一只乌龟』这句你上次就说过——我记着呢，别想赖账。」
（PowerContext · scope scp_58kd0jb0kmvg… · 记忆 revision 1 · 本次检索命中 1 条）
```

> 最后一条是 PowerContext 的内容哈希去重：完全相同的一条记忆不会新增，HTTP 仍 200 但 `entry=null`。sidecar 把它翻译成 `duplicate: true`，回复改成「这句你说过了」。

### 回放命令

```bash
# 先保证 PowerContext + sidecar 都在跑
python3 samples/replay.py --run-id my-run

# 断言（脚本内置，失败 exit 1）：
#   1) 每个后续轮次都能在 list 里看到前面轮次写进去的文本
#   2) 非重复轮次的记忆条数必须增长
# 输出：samples/replay_output.jsonl + samples/transcript.md
```

`--run-id` 会拼到会话名后（`tea--my-run`），保证每次回放用全新的 scope，不污染旧数据。

### 在真实 Dify 上复跑（可选）

```bash
export DIFY_BASE_URL=http://127.0.0.1:8088
export DIFY_APP_KEY=app-...          # Dify 应用 → API 访问 → 创建密钥
python3 tools/dify_smoke.py
```

脚本走 Dify 官方服务 API `/v1/chat-messages`，跑 3 轮并断言：第 N 轮回复里必须有 `N 笔账`、且第 1 轮的文本仍在。本机执行结果写进了 [`samples/dify_run.jsonl`](samples/dify_run.jsonl)（三轮同一个 `conversation_id`）。

---

## 7. 自验记录（原始输出）

以下命令本机真实执行过，输出原样摘录。

**① sidecar 健康**

```bash
$ curl -s http://127.0.0.1:8787/health
{"ok": true, "sidecar": "dify-tavern-sidecar", "powercontext": {"url": "http://127.0.0.1:18080", "status": 200, "ready": true}, "cached_scopes": 0}
```

**② 写记忆（第一轮）**

```bash
$ curl -s -X POST http://127.0.0.1:8787/memory/write \
    -H 'X-Session-Id: manual-test-1' --data-binary '我把盐当成糖放进了阿福的茶里'
{"ok": true, "scope_id": "scp_7t8680bsdrak7mdhmmk7dntyg6", "revision": 1,
 "entry_id": "mem_ent_d7acd760be184dceb11be293fce6792c", "duplicate": false,
 "text": "我把盐当成糖放进了阿福的茶里"}
```

**③ 检索（真实 FTS）**

```bash
$ curl -s -X POST http://127.0.0.1:8787/memory/search \
    -H 'X-Session-Id: manual-test-1' --data-binary '茶'
{"ok": true, "scope_id": "scp_7t8680bsdrak7mdhmmk7dntyg6", "mode": "fts", "count": 1,
 "hits": [{"text": "我把盐当成糖放进了阿福的茶里", "score": 0.01639344262295082, "matched_by": ["fts"]}]}
```

**④ 同一会话第二轮取到第一轮**

```bash
$ curl -s -X POST http://127.0.0.1:8787/memory/list -H 'X-Session-Id: manual-test-1'
{"ok": true, "scope_id": "scp_7t8680bsdrak7mdhmmk7dntyg6", "revision": 2, "count": 2,
 "memories": [
   {"text": "我把盐当成糖放进了阿福的茶里", "kind": "prank", "entry_id": "mem_ent_d7ac…", "version": 1, "state": "active"},
   {"text": "我把阿福的板凳腿锯短了一截", "kind": "prank", "entry_id": "mem_ent_b90c…", "version": 1, "state": "active"}]}
```

**⑤ 回放 7 轮全过**

```bash
$ python3 samples/replay.py --run-id readme-final
...
[replay] wrote replay_output.jsonl and transcript.md
$ echo $?
0
```

**⑥ 停掉 sidecar，回放层必须报错**

```bash
$ python3 samples/replay.py --run-id failtest
REPLAY FAILED:
 - line 1: sidecar/PowerContext call failed: {'ok': False, 'error': {'code': 'sidecar_unreachable', 'message': '<urlopen error [Errno 111] Connection refused>'}} ...
$ echo $?
1
```

**⑦ 真实 Dify 1.18.0：导入 → 发布 → 三轮对话**

```bash
$ curl ... -X POST /console/api/apps/imports -d '{"mode":"yaml-content","yaml_content":"..."}'
{"id":"...","status":"completed","app_id":"9676...","app_mode":"advanced-chat",
 "current_dsl_version":"0.7.0","imported_dsl_version":"0.7.0","error":"","warnings":[]}

$ DIFY_APP_KEY=app-... python3 tools/dify_smoke.py
--- turn 1 ---
「你在我这儿留下的 1 笔账，我可一件没忘：」
  1. 我把盐当成糖放进了阿福的茶里
--- turn 2 ---
「你在我这儿留下的 2 笔账，我可一件没忘：」
  1. 我把盐当成糖放进了阿福的茶里
  2. 我又把你的板凳腿锯短了一截
--- turn 3 ---
「你在我这儿留下的 3 笔账，我可一件没忘：」
  1. 我把盐当成糖放进了阿福的茶里
  2. 我又把你的板凳腿锯短了一截
  3. 今天茶里我什么都没放，真的。
[dify-smoke] wrote dify_run.jsonl      # exit 0
```

**⑧ 真实 Dify：停掉 sidecar，运行必须失败**

```bash
$ (sidecar stopped)
$ curl -X POST http://127.0.0.1:8088/v1/chat-messages ... 
{"code":"invalid_param","message":"Run failed: Request failed with status code 503","status":400}
[http 400]
$ (restart sidecar; same request)
... 1 笔账 ... [http 200]
```

Dify 侧的失败语义来自三个 HTTP 节点显式开启的 `retry_config.retry_enabled: true`。graphon 的语义是「非 2xx 且没开 retry/error_strategy 时节点仍算成功」，开了 retry 后非 2xx / 连接失败会真正 FAILED，工作流报错，不会拿旧数据编答案。

**⑨ sidecar 重启后，旧 Dify 会话仍能续上**

```bash
# 重启 sidecar 后，沿用重启前的 conversation_id 再发一句
「你在我这儿留下的 4 笔账，我可一件没忘：」
  1. 我把盐当成糖放进了阿福的茶里
  2. 我又把你的板凳腿锯短了一截
  3. 今天茶里我什么都没放，真的。
  4. 我把你的茶壶藏起来了
```

scope 用 `idempotency_key` 幂等创建，sidecar 内存里的映射丢了也能重新落到同一个 PowerContext scope。

---

## 8. 输入边界

sidecar 在转发前就把这些挡住（避免把 PowerContext 的空 query 500 透传成业务错误）：

| 输入 | sidecar 行为 |
| --- | --- |
| 缺 `X-Session-Id` | 400 `missing_session` |
| 会话 id 含空格/非法字符 | 400 `invalid_session` |
| 写入文本为空白 | 400 `empty_text` |
| 写入文本 > 8192 字节（PowerContext 上限） | 413 `text_too_long` |
| 检索 query 为空白 | 400 `empty_query`（PowerContext 对空白 query 会 500） |
| PowerContext 不可达 | 503 `powercontext_unreachable` |
| PowerContext 4xx/5xx | 透传状态 + `powercontext_<code>` |

---

## 9. 本机验证范围（诚实说明）

| 项 | 状态 | 证据 |
| --- | --- | --- |
| PowerContext 1.2.0 起服务、记忆写/检/列 | ✅ 本机实测 | §7 ②③④ |
| sidecar 三接口、错误码、边界 | ✅ 本机实测 | §7、§8 |
| 回放脚本复现三段记录 | ✅ 本机实测 | `samples/transcript.md`，exit 0 |
| 停 sidecar 回放层报错 | ✅ 本机实测 | §7 ⑥，exit 1 |
| DSL 通过 graphon 0.8.0 官方节点模型校验 | ✅ 本机执行 | `python3 tools/validate_dsl.py` → `OK … 6 nodes (3 HTTP)` |
| Dify 1.18.0 导入 DSL | ✅ 本机实测 | §7 ⑦：`status=completed`，无 warning |
| Dify 端到端三轮对话 | ✅ 本机实测 | `samples/dify_run.jsonl`，exit 0 |
| Dify 停 sidecar 必失败 | ✅ 本机实测 | §7 ⑧：HTTP 400 `invalid_param` |
| sidecar 重启后旧会话续记忆 | ✅ 本机实测 | §7 ⑨：4 笔账 |
| PowerContext 换 seekdb 后端 | ⚠️ 未在本机跑 | 本机用 SQLite + FTS；seekdb 只是 PowerContext 的存储后端开关，见 §10，sidecar 无感知 |

> **环境说明**：本机验证时 Dify 1.18.0 跑在 Docker（Docker Desktop/WSL2），PowerContext 与 sidecar 跑在 WSL。这套环境里容器内 `host.docker.internal` 不解析，所以验证时把应用环境变量临时改成 WSL 的 IP；提交的 DSL 默认值仍是 `host.docker.internal`。Dify 的 HTTP 节点经 `ssrf_proxy` 出站，已把该网段加入 `SSRF_PROXY_ALLOW_PRIVATE_IPS`。
>
> **未做的事**：没有接任何 LLM（本 demo 的回复由 Code 节点确定性合成，这是为了「零 Key 可玩」）；没有验证 seekdb 作为 PowerContext 后端（SQLite 后端已完整跑通，且题面只要求 seekdb / PowerContext 二选一）。

---

## 10. 排障

1. **HTTP 节点报连接失败 / 超时**
   - 确认 sidecar 监听 `0.0.0.0:8787`（不是 127.0.0.1）；
   - 确认 `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=host.docker.internal`（或把 sidecar 所在网段加进 `SSRF_PROXY_ALLOW_PRIVATE_IPS`），改完重启 `ssrf_proxy` 与 `api`；
   - 在 api 容器里验证：`docker compose exec api curl -s http://host.docker.internal:8787/health`。

2. **`host.docker.internal` 在 Linux 上不解析**
   给 `api` 服务加 `extra_hosts: ["host.docker.internal:host-gateway"]`，或让 sidecar 也进 compose 网络、把 `SIDECAR_BASE_URL` 改成 `http://sidecar:8787`。

3. **导入时提示版本待确认（PENDING）**
   本 DSL 的 `version` 是 `"0.7.0"`（Dify 1.18.0 的当前版本）。如果你用的是更老的 Dify，把 `version` 改小或按 UI 提示确认迁移。

4. **阿福不记事**
   - 先 `curl /memory/list -H 'X-Session-Id: <conversation_id>'` 看 count；
   - 每次「新对话」都会换 scope，是预期行为；
   - PowerContext 的日志在 **stdout**，用响应头 `X-PowerContext-Request-ID` 关联 `operation=remember_memory/search_memory`。

5. **想用 seekdb 作为存储后端**
   PowerContext 支持 `[cli,server,seekdb]` 额外依赖，把 `powercontext.env` 改成：
   ```dotenv
   POWERCONTEXT_SERVER_DATABASE_KIND=seekdb
   POWERCONTEXT_SERVER_DATABASE_PATH=/absolute/path/to/seekdb
   ```
   其余步骤不变；sidecar 不需要感知后端变化。

6. **`db_postgres` 反复重启，日志是 `chmod ... Operation not permitted`**
   仓库放在 Windows 盘符挂载的 9p/drvfs 文件系统时，Postgres 无法对绑定挂载的数据目录改权限。给 `db_postgres`、`redis`、`weaviate`、`api`、`plugin_daemon` 的数据目录改用 Docker 命名卷即可（本机就是用一个 `docker-compose.override.yaml` 的 5 个命名卷跑起来的）。

---

## 11. 设计取舍

- **不用 LLM，是为了「无 Key 也能玩」**。阿福的回复由 Code 节点确定性合成，所以三段样例可复现、评审零配置即可体验。想升级成 LLM 版：把 Code 节点换成 LLM 节点，把 `③ 读取全部记忆` 的 JSON 塞进 system prompt，答案节点模板不变。
- **记忆按 Dify 会话隔离**：`sys.conversation_id` → SHA-256 → PowerContext `scope_id`。scope 用 `idempotency_key` 幂等创建，sidecar 重启不会丢映射。
- **列表顺序用 change log 还原**：`/v1/memory/entries/list` 没有文档化排序，sidecar 再读 `/v1/memory/changes?since_revision=0`，按 `add` 出现的顺序排列，账本才稳定。
- **不编答案**：sidecar 和 Code 节点在异常时都抛错/透传错误码；HTTP 节点开 retry，让失败显式暴露。

---

## 12. 重新生成 / 校验 DSL

```bash
# 修改了 sidecar/npc_persona.py 后重新生成 DSL
python3 tools/build_dsl.py

# 用 graphon 官方模型校验（需要 pip install graphon==0.8.0 pyyaml）
python3 tools/validate_dsl.py
# OK dify_tavern_npc.yml: 6 nodes (3 HTTP), graphon 0.8.0 schema validated
```
