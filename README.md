# Aspen Plus MCP

通过自然语言操控 Aspen Plus 进行化工流程模拟——参数调优、批量运行、读取结果，全部由 AI 代理完成。

## 工作模式

```
在 GUI 里搭好流程拓扑 → MCP 接管参数调优和运行分析
```

**MCP 不负责画流程图。** 需要在 Aspen Plus GUI 中手动放置模块和物流（或用 `connect`/`connect_port` 工具通过代码建立连接），然后 MCP 用来：

- ✅ 批量改参数、跑灵敏度分析
- ✅ 运行模拟、检查收敛状态
- ✅ 读取物流和模块结果数据
- ✅ 导出报告

---

## 安装

### 环境要求

- Windows（COM 自动化依赖）
- Aspen Plus v15 已安装（GUI 41.0 / 40.0）
- Python 3.10+

### 步骤

```bash
# 1. 克隆或解压项目
cd aspen-mcp

# 2. 创建虚拟环境
python -m venv venv

# 3. 激活虚拟环境并安装依赖
venv\Scripts\pip install -e .

# 4. 启动（验证安装）
venv\Scripts\aspen-mcp
```

启动后 MCP 服务器会通过 stdio 通信，适用于任何支持 MCP 协议的客户端（如 Qoder、Claude Desktop、VS Code）。

### 在 Reasonix 中使用

在 `reasonix.toml` 中添加：

```toml
[[plugins]]
name    = "aspen-mcp"
command = "path\\to\\aspen-mcp\\venv\\Scripts\\python.exe"
args    = ["-m", "aspen_mcp.server"]
```

### 在 Claude Desktop / VS Code 中使用

```json
{
  "mcpServers": {
    "aspen-mcp": {
      "command": "path\\to\\aspen-mcp\\venv\\Scripts\\python.exe",
      "args": ["-m", "aspen_mcp.server"]
    }
  }
}
```

### 环境变量（可选）

| 变量 | 作用 | 默认位置 |
|---|---|---|
| `ASPEN_MCP_AUDIT_DIR` | 审计日志目录 | 工作目录下 `.aspen_mcp_audit`；该目录不可写时回落 `%LOCALAPPDATA%\aspen-mcp\audit` |
| `ASPEN_MCP_STATE_DIR` | 执行账本 / checkpoint 状态目录 | 工作目录下 `.aspen_mcp_state`；不可写时回落 `%LOCALAPPDATA%\aspen-mcp\state` |

> 说明：MCP 服务进程的工作目录常为宿主程序目录（可能不可写），因此两个目录都内置了 `LOCALAPPDATA` 回落策略，一般无需配置。需要集中存放或迁移日志时再用环境变量显式指定。

---

## 工具总览

共 79 个工具，按用途分组如下。

### 模拟生命周期

| 工具 | 说明 |
|---|---|
| `open_file(path)` | 打开 `.apw` 文件 |
| `new_simulation()` | **创建空白新模拟（使用 Aspen InitNew()）** |
| `close_file()` | 关闭当前文件 |
| `run()` | 运行模拟（同步，受执行账本准入控制） |
| `run_async()` | 异步运行；派发成功返回 `run_id`，终态用 `report_run_outcome` 回收 |
| `report_run_outcome(run_id, outcome)` | **报告异步运行的终态（completed / failed / outcome_unknown）** |
| `stop_simulation()` | **停止当前运行** |
| `save(path?)` | 保存文件 |
| `reinit()` | 重置结果（重新运行前调用） |
| `reinit_and_run()` | **Reinit + Run 一步完成（受执行账本准入控制）** |
| `status()` | 查看连接和引擎状态 |
| `probe()` | **诊断 COM 状态（app / root / tree 三级检查）** |
| `visible(show)` | **显示/隐藏 Aspen 窗口** |
| `batch_refresh(off)` | **开关 GUI 刷新（批量操作加速）** |
| `run_script(path)` | 在 Aspen 内部执行 Python 脚本（建模任务活跃期间关闭，见"执行账本"） |

### 流程拓扑

| 工具 | 说明 |
|---|---|
| `list_all_blocks()` | 列出所有模块 |
| `list_all_streams()` | 列出所有物流 |
| `explore(path)` | 探索数据树节点 |
| `add_block(name, type)` | 添加模块（如 `HEATER`, `ICON1`） |
| `remove_block(name)` | 删除模块 |
| `add_stream(name)` | 添加物流 |
| `remove_stream(name)` | 删除物流 |
| `connect(source, dest, stream?)` | **连接源块→目标块**（自动创建流） |
| `connect_port(block, port, stream)` | **连接到指定端口**（用于非标准端口） |
| `disconnect(stream_name)` | **断开流并从流程中移除** |
| `list_block_ports(block)` | **查看块的端口和连接情况** |
| `flowsheet_topology()` | **显示流程拓扑：源块 --[流名]--> 目标块** |

### 参数与结果

> ⚠️ **注意：`set_param` 只适用于模块参数，物流参数必须用 `set_stream_param`。** 详情见下方"流股写入契约"章节。

| 工具 | 说明 |
|---|---|
| `get_block(name)` | 读取模块规格和结果 |
| `block_status(name)` | **读取模块收敛状态（BLKSTAT / PER_ERROR / PROPSTAT）** |
| `set_param(block, param, value, unit?)` | 设置模块参数（无 unit 时按 SI 解释） |
| `get_stream(name)` | 读取物流属性（温度、压力、流量等） |
| `set_stream_param(name, param, value, basis?, unit?)` | **设置物流参数（TEMP / PRES / VFRAC）**；TOTAL 通道已封锁，见"流股写入契约" |
| `set_stream_composition(name, component, flow)` | 设置物流中某组分的摩尔流量（受契约保护，详见"流股写入契约"） |
| `set_stream_composition_batch(name, components, basis?, total_flow?)` | 批量设置组成（受契约保护，详见"流股写入契约"） |
| `get_stream_composition_info(name)` | 读取物流组成元数据（COMPTYPE、分子量、流量） |

### 组分与物性

| 工具 | 说明 |
|---|---|
| `list_components()` | 列出所有组分 |
| `add_component(id)` | 添加组分（如 WATER, ETHANOL） |
| `remove_component(id)` | 删除组分 |
| `get_property_method()` | 读取当前全局物性方法 |
| `set_property_method(method)` | 设置物性方法（如 NRTL, PENG-ROB） |

### 反应与动力学

| 工具 | 说明 |
|---|---|
| `list_reaction_sets()` | 列出所有反应集 |
| `add_reaction_set(name, type)` | 创建反应集（POWERLAW/LHHW/EQUILIBRIUM） |
| `remove_reaction_set(name)` | 删除反应集 |
| `add_reaction(set, no, reactants, products, phase, exponents?)` | 添加反应及计量系数 |
| `remove_reaction(set, no)` | 删除单个反应 |

### 塔器（RadFrac）

| 工具 | 说明 |
|---|---|
| `set_column_stages(block, nstage)` | 设置理论板数 |
| `set_column_pressure(block, top_pres, dp_stage?, unit?)` | 设置压力分布 |
| `set_column_specs(block, rr=?, d=?, b=?, br=?)` | **设置操作规格（回流比+馏出率/塔底率/再沸比，恰好两项）** |
| `set_condenser_type(block, type)` | 设置冷凝器类型 |
| `set_reboiler_type(block, type)` | 设置再沸器类型 |
| `set_feed_stage(block, stream, stage)` | 设置进料位置 |
| `set_product_stage(block, stream, stage, phase?)` | 设置产品采出位置 |
| `add_side_duty(block, stage, duty, unit?)` | 设置侧线换热 |
| `remove_side_duty(block, stage)` | 删除侧线换热 |

### 分析与诊断

| 工具 | 说明 |
|---|---|
| `sensitivity(block, variable, values, ...)` | 灵敏度分析（**走独立 analysis 额度，需显式授权**，见"执行账本"） |
| `export_report_file(path)` | 导出 `.rep` 报告（回执校验产物存在并报字节数） |
| `generate_input_summary(path)` | **导出输入摘要 (.bkp)**（同上校验） |
| `find_incomplete_inputs()` | **扫描未填写的输入节点** |
| `validate_block(name)` | 验证模块输入完整性 |
| `simulation_warnings()` | 扫描常见配置问题 |
| `fill_trivial_params(objects?, params?, dry_run?)` | 填充基础设施/UI 参数；**默认 dry_run=True 只预览，写入需显式 objects+params+`dry_run=False`** |
| `list_tear_streams()` | 列出所有撕裂流股 |
| `set_tear_estimate(stream, temp?, pres?, total_flow?, unit?)` | 设置撕裂流股初值（一个 unit 只作用于一种量） |
| `configure_fsplit(block, outlet, frac)` | 配置 FSPLIT 出口分配比例 |
| `deep_probe(block_name?)` | 深度探测块输入节点 |
| `diagnose(keywords)` | 搜索收敛故障知识库（含实时状态） |
| `search_convergence_knowledge(keywords)` | 详细收敛知识搜索 |

### 公用工程

| 工具 | 说明 |
|---|---|
| `add_utility(name, type, block?, params?)` | 添加公用工程 |
| `batch_add_utilities(utilities)` | 批量添加公用工程 |
| `get_utility(name)` | 读取公用工程参数 |
| `list_utilities()` | 列出所有公用工程 |
| `remove_utility(name)` | 删除公用工程 |

### 单位制

| 工具 | 说明 |
|---|---|
| `get_unit_set()` | 获取当前单位制 |
| `set_unit_set(unit_set)` | 设置单位制（METCBAR / ENG / SI；**不转换已有数值**） |

### 通用路径访问

| 工具 | 说明 |
|---|---|
| `get_value(path, attribute?)` | **按反斜杠路径读值**；attribute 可选 `si`（内部 SI）/`value`（跟随单位制显示值）/`attr`/`unit` |
| `set_value(path, value, unit?)` | **按反斜杠路径写值**（无 unit 时按 SI） |

### 受控批配置

| 工具 | 说明 |
|---|---|
| `configure_model(manifest)` | **按 manifest 批量配置（feed / block 操作），静态检查整批零写、动态失败分级止损、回执逐项报告** |
| `inspect_configuration(manifest)` | **只读预览 manifest 与现状/回执的匹配决策（不写、不记账）** |

### 通用表格编辑

| 工具 | 说明 |
|---|---|
| `add_table_row(path, label?, col?)` | **向已有表格节点插入行**（如 RYIELD/RSTOIC 收率表），返回行号供 `set_value` 填值 |

---

## 执行账本与运行准入

原生运行（`run` / `reinit_and_run` / `run_async`）受**执行账本（execution ledger）**准入控制：额度、授权与任务沿袭绑定，AI 代理不能通过请求字段自行增额。

| 规则 | 值 |
|---|---|
| 首跑额度 | 1 次 |
| TUNE 追加 | 需显式授权（`authorize_tune`），累计最多 3 次（总原生提交 ≤4） |
| 分析运行额度 | `sensitivity` 等分析运行走独立额度（`authorize_analysis`，安全阀上限 50），不占用建模 4 次提交 |
| 任务 deadline | 默认 300 秒，自首次派发前启动；超时为 `outcome_unknown` |
| 额度回收 | `outcome_unknown` / `failed` 均**不退还额度**；记录缺失或归属不明一律拒绝 |
| `run_script` | 建模任务活跃期间关闭（防止绕行原生运行），仅无活动任务时可用 |

**任务沿袭**：运行必须在活动任务（model lineage）内执行。任务由服务端账本接口 `begin_task(task_id, model_lineage_id)` 开启（服务端 API，非 MCP 工具）；`authorize_tune` / `authorize_analysis` 授权通道同样只在服务端。MCP 工具面不提供开启任务或增额的入口——这是有意的防自行扩权设计。未建立任务时运行返回：

```
Error: RUN 被阻断（no_active_task）。额度、授权与沿袭绑定任务，不能通过请求增额；请人工确认后继续。
```

**推荐流程：**

```
open_file("case.apw")        # 或 new_simulation()
# …参数/拓扑修改…
reinit_and_run()             # 清缓存 + 运行；超时返回"结果未知"，不自动重试
block_status("C1")           # 检查收敛
```

**异步运行**（长任务）：

```
run_async()                  → "Async simulation started (run_id=…)。终态需显式回收…"
# 运行期间允许受控只读状态轮询；禁止第二跑与模型写入
report_run_outcome("<run_id>", "completed")   # 或 failed / outcome_unknown
```

---

## 流股写入契约

流股写入按"已原生验证的能力矩阵"控制：**未验证的原生语义一律显式拒绝写入，不做猜测性回退。**

### 可以写：TEMP / PRES / VFRAC

`set_stream_param` 支持 `TEMP`、`PRES`、`VFRAC` 三个参数，写入走 `MIXED` 子节点（Aspen 流股参数节点必须经 `MIXED` 写入，直接 `SetValue` 会报 `AE_UNDERSPEC`），并带**写后读回校验**：

```python
tree = aspen.find_node(r"\Data\Streams\H2O\Input\TEMP\MIXED")   # 写入位置
```

```
set_stream_param("H2O", "TEMP", 140, unit="C")
set_stream_param("H2O", "PRES", 1.5, unit="bar")
set_stream_param("S1", "VFRAC", 0.0)
```

- 值始终以内部 SI 存储（K / Pa / kmol/s），传 `unit` 时按工程单位换算（原生 `SetValueAndUnit`）；不传 `unit` 即按 SI 解释。
- 工具返回 write/verify 字典（写入与读回分列），不再是简单字符串。

### 已封锁：TOTAL / TOTFLOW

```
set_stream_param("FEED", "TOTAL", 100)
→ {"status": "unsupported", "error": "未验证 TOTAL/TOTFLOW 语义；请使用独立 total_basis 的完整组成接口"}
```

总流量通道未完成原生验证，已显式拒绝。**不要再用旧写法**传 TOTAL——`run()` 前置检查提示中的旧示例亦以此为准。

### 受契约保护：组成写入（unverified 拒绝）

`set_stream_composition` / `set_stream_composition_batch` / `set_stream_param(<组分名>, …)` 依赖"组成 ↔ 总流量"组合契约。该契约**尚未通过原生验证**，因此当前一律返回：

```
{"status": "unsupported", "error": "原生组合 unverified，禁止写入；需要独立验证的内部适配器"}
```

- 只在持有独立验证证据的内部适配器注入时才允许写入（离线 fixture 的能力不算数）。
- 查询能力矩阵现状：服务端 API `capability_matrix()`（非 MCP 工具），或直接调用 `inspect_configuration` 预览 feed 操作（只读，读回现状值）。
- 受控替代：用 `configure_model` 的 `feed` 操作提交完整组成向量（含 `total` / `total_basis` 声明），走批配置的校验与回执通道。

### basis 与 unit 消歧

- `basis` 取值：`MOLE-FLOW`（默认）/ `MASS-FLOW` / `MOLE-FRAC` / `MASS-FRAC`（别名 `MOLE` / `MOLEFRAC` 等自动归一化）。
- 分数 basis 必须与 `total_flow` 联用且闭合（误差 ≤1e-6，不自动归一化）；跨量纲总流量必须显式给出，禁止猜测分子量。
- 旧位置参数（basis/unit 顺序传参）会逐 token 消歧，**歧义、重复或未知一律拒绝**，建议始终用关键字参数。

---

## 受控批配置（configure_model）

用于"一次提交多个对象的多项修改"：整批静态检查（格式、类型、单位维度、闭合、重复 op_id、冲突目标、依赖环）**一次返回全部错误、整批零写入**；运行时失败只阻断受影响对象及依赖项，其余独立项继续；系统性失效（单位坐标无效、身份不明、COM 超时/结果未知）停止全部后续写入。

### manifest 示例

```json
{
  "schema_version": 1,
  "manifest_id": "M-1",
  "task_id": "T1",
  "model_lineage_id": "L1",
  "operations": [
    {
      "op_id": "f1", "kind": "feed", "target": "FEED",
      "dependencies": [], "specs": {}, "values": {},
      "components": {"METHANE": 1.0},
      "composition_basis": "MOLE-FLOW", "total": 1.0, "total_basis": "MOLE-FLOW"
    },
    {
      "op_id": "b1", "kind": "block", "target": "H1",
      "dependencies": ["f1"],
      "specs": {},
      "values": {"TEMP": {"type": "number", "value": 150, "unit": "C"}}
    }
  ]
}
```

- 顶部 `manifest` 要求：`schema_version=1`、非空 `manifest_id` / `task_id`、`model_lineage_id` 必须与当前活动任务一致（否则身份不符拒绝）。
- `kind` 只有 `feed` / `block` 两类；`values` 支持 `number`（带 `unit`）/`enum`/`text` 三种类型。
- 合法 JSON 文本或 dict 均可传入；JSON 文本拒绝重复键。

### 回执与决策语义

- 回执 `write_status`：`applied` / `failed` / `skipped` / `partial_applied`；已进入 COM 但无确定结果记 `outcome_unknown`，**不捏造终态**。
- `verify_status` 与写入分列：`pass` / `fail` / `unknown`。
- 请求指纹（规范 JSON 的 SHA-256）用于识别"同 op_id 同请求"：先核现状再复用，不重复写；同 op_id 不同请求拒绝；现状与记录不符时列人工差异并暂停，**不用旧 state 覆盖**。
- `inspect_configuration` 返回逐项决策：`would_apply` / `reuse` / `manual_divergence` / `unknown` / `different_request`，不写值、不记台账。

---

## 审计与状态

- **审计**：所有工具调用（请求参数、结果摘要、业务状态、耗时）写入 `<审计目录>/audit.jsonl`。审计落盘是旁路能力——写失败只降级警告，不影响业务调用。
- **状态**：执行账本 `<状态目录>/execution_ledger.json`（附 `.events.jsonl` 事件流）与 checkpoint 索引 `<状态目录>/checkpoints.json`。
- 目录解析：环境变量（`ASPEN_MCP_AUDIT_DIR` / `ASPEN_MCP_STATE_DIR`）→ 工作目录子目录 → `%LOCALAPPDATA%\aspen-mcp\<audit|state>` 回落。

---

## 常见工作流

### 1. 打开文件并运行

```
open_file("甲醇.apw")
run()
status()
```

> `run` 需要活动任务沿袭（见"执行账本"）。若无任务，先请管理侧开启任务，或改用 `run_script` 通道（无任务时可用）。

### 2. 改参数后重新运行

```
set_param("HEATER", "TEMP", 150, unit="C")
set_param("HEATER", "PRES", 5, unit="bar")
reinit_and_run()
get_stream("C-OUT")
```

### 3. 从零搭建流程（拓扑+参数+运行）

> ⚠️ **重要：`set_param` 只能设模块参数，物流参数必须用 `set_stream_param`。** 详情见"流股写入契约"章节。

```
# 步骤1：添加模块
add_block("MIXER", "MIXER")
add_block("REACTOR", "RPLUG")
add_block("SEP", "FLASH2")

# 步骤2：添加物流并连接
connect("MIXER", "REACTOR", "FEED")
connect("REACTOR", "SEP", "R-OUT")

# 步骤3：设参数（模块用 set_param，物流用 set_stream_param）
set_param("MIXER", "TEMP", 100, unit="C")
set_param("REACTOR", "LENGTH", 5)

# 物流参数必须用 set_stream_param（通过 MIXED 子节点写入）
set_stream_param("FEED", "TEMP", 150, unit="C")
set_stream_param("FEED", "PRES", 5, unit="bar")

# 组成写入当前受契约保护（unverified → 显式拒绝），
# 受控替代：configure_model 的 feed 操作提交完整组成向量
configure_model({
  "schema_version": 1, "manifest_id": "M-feed", "task_id": "T1",
  "model_lineage_id": "<当前活动沿袭>",
  "operations": [
    {"op_id": "f1", "kind": "feed", "target": "FEED",
     "dependencies": [], "specs": {}, "values": {},
     "components": {"METHANE": 1.0},
     "composition_basis": "MOLE-FLOW", "total": 1.0, "total_basis": "MOLE-FLOW"}
  ]
})

# 步骤4：运行并看结果（先用 reinit 清缓存，再 run）
reinit()
run()
get_stream("R-OUT")
```

更方便的替代：**直接用 `reinit_and_run()` 一步完成**。

### 4. 添加组分并切换物性方法

```
# 添加新组分
add_component("METHANE")
add_component("ETHANOL")

# 查看现有组分
list_components()

# 切换物性方法
set_property_method("NRTL")
get_property_method()
```

### 5. 灵敏度分析（需分析额度授权）

```
sensitivity("H1", "TEMP", [100, 150, 200], targets=["Streams\\PROD\\MOLEFRAC\\MIXED\\METHANE"])
```

> `sensitivity` 每个扫描点消耗一次分析额度；未授权时返回"分析运行被阻断（analysis_not_authorized）"并停止后续点。

---

## 操作要点（COM 经验总结）

### 删除块和物流的顺序 ⚠️

**先删块，再清物流。** 绝不能反过来。

✅ 正确：
```
remove_block B1          # 块先删，连接自动断裂
disconnect CL2           # 清理残余孤儿物流
disconnect H2O
```

❌ 错误（会导致 COM 服务器异常，无法恢复）：
```
disconnect CL2           # 块还在但进料少了一条 → 块变无效
disconnect H2O           # 块完全孤零零
remove_block B1          # 💥 COM 服务器异常
```

**原理**：Mixer 等块要求至少有一条连接才能存在。全拔光后再删块，Aspen 内部校验不通过，COM 直接崩。

### COM 错误分级

| 错误现象 | 含义 | 应对 |
|---|---|---|
| `尚未调用 CoInitialize。` | 子线程未初始化 COM | 修复代码即可，不致命 |
| `发生意外。` + (0, None, ...) | 参数/操作型错误 | 可重试，COM 状态不受影响 |
| `服务器出现异常情况。` | COM 服务器状态已脏 | ❌ **立即 close_file → open_file** |
| `远程过程调用失败。` | Aspen Plus 未运行 | 检查 Aspen Plus 是否已打开 |
| `对象没有连接到服务器` | COM 代理已过期（进程重启后） | 重启 MCP 客户端 |

**`close_file() → open_file()` 可解决绝大多数 COM 稳定性问题。**
COM bridge 内置自动重连机制，连接丢失时会自动尝试重新建立。若仍失败，重启 MCP 客户端即可。

### 合法搭建流程

```
1. new_simulation / open_file           # 创建或打开文件
2. set_property_method                   # 先改物性
3. add_component                         # 添加组分
4. add_block + add_stream + connect      # 搭建流程拓扑
5. 设参数（模块用 set_param，物流用 set_stream_param）
6. reinit_and_run                        # 运行
```

### HEATER 的温度设置

HEATER 默认 `HEATOPT=CONST-DUTY`（按热负荷计算）。
**使用 `set_param("H1", "TEMP", 150)` 时，MCP 会自动将模式切换为 `CONST-TEMP`**，无需手动操作。

### 保存文件

`save()` — 原地保存（覆盖当前文件）
`save("路径.apw")` — 另存为新文件
两种方式均已验证可用。保存后 Aspen 会临时持有文件锁，不影响保存结果。

### 流连接

物流连接不走 `Connections`（只读），而走 **`Block → Ports → {port名} → Elements.Add(流名)`**。
- 连接前如果端口已有流，会自动断开旧连接
- 标准端口名：`F(IN)` 输入物流、`P(OUT)` 输出物流、`V(OUT)` 汽相出口、`L(OUT)` 液相出口
- 不确定端口名时先用 `list_block_ports("模块名")` 查看

### 添加/删除组分

组分通过 `Components → Specifications → Input → TYPE` 表操作。该表为 **2 列**：列 0 为组分标签，列 1 为组分类型（Aspen Plus 自动填充 `CONV`）。只需用 `InsertRow` + `SetLabel(0,0)` 设置名称即可，**不要手动设置第 2 列**（会导致该值被当作新增组分名）。
长名称组分（>8字符）会自动生成短标签并写入 `ANAME`，Aspen 自动从数据库解析别名。
重复名称会自动追加编号后缀确保唯一。

### 设置物性方法

`set_property_method("NRTL")` 会同时写 `GBASEOPSET` 和 `GOPSETNAME` 两个节点。
建议先设好物性方法再添加模块，避免模块级 `OPSETNAME` 与全局设置冲突。

### 反应动力学完整流程

> ⚠️ **重要：`add_reaction` 函数创建 2D 计量系数表（COEF/COEF1）时，标签设置有时会失败。**
> 如果计量系数写入后验证为空（`COEF\\1` 下的 Elements 为空或值为 None），需要**手动补填**。
> 下面提供了两种方式，推荐用**方式二（手动填表）**，更可靠。

#### 方式一：用工具函数（快速，但不一定写进去）
```
add_reaction_set("R-METH", "POWERLAW")
add_reaction("R-METH", 1, reactants={"CO2":1, "H2":4}, products={"CH4":1, "H2O":2}, phase="V")
# 设动力学
aspen.find_node(r"\Data\Reactions\Reactions\R-METH\Input\PRE_EXP\1").SetValue(0, 1.0e8)
aspen.find_node(r"\Data\Reactions\Reactions\R-METH\Input\ACT_ENERGY\1").SetValue(0, 14300)
```
**运行后必须验证**计量系数是否写入：
```
coef = aspen.find_node(r"\Data\Reactions\Reactions\R-METH\Input\COEF\1")
print(f"COEF Count={coef.Elements.Count}")  # 应为 >0，且能读到组分名
```

#### 方式二：手动填表（推荐，100%可靠）

完整示例 —— 在一个已打开的模拟中添加甲烷化副反应：

```python
from aspen_mcp.tools.components import tool_add_component
from aspen_mcp.tools.reactions import *

# 1. 添加新组分（原文件有的组分不需要再加）
tool_add_component("CH4")

# 2. 创建反应集
rxns = aspen.find_node(r"\Data\Reactions\Reactions")
rxns.Elements.Add("R-METH!POWERLAW")

# 3. 设置反应类型和相态
rt = aspen.find_node(r"\Data\Reactions\Reactions\R-METH\Input\REACTYPE")
rt.Elements.InsertRow(0, 0)
rt.Elements.SetLabel(0, 0, False, "1")
aspen.find_node(r"\Data\Reactions\Reactions\R-METH\Input\REACTYPE\1").SetValue(0, "KINETIC")
aspen.find_node(r"\Data\Reactions\Reactions\R-METH\Input\PHASE\1").SetValue(0, "V")

# 4. 填充 COEF（反应物计量系数，正值 = 消耗）
coef1 = aspen.find_node(r"\Data\Reactions\Reactions\R-METH\Input\COEF\1")
for i in range(2): coef1.Elements.InsertRow(0, i)
coef1.Elements.SetLabel(0, 0, False, "CO2")
coef1.Elements.SetLabel(1, 0, False, "MIXED")
coef1.Elements.SetLabel(0, 1, False, "H2")
coef1.Elements.SetLabel(1, 1, False, "MIXED")
aspen.find_node(r"\Data\Reactions\Reactions\R-METH\Input\COEF\1\CO2\MIXED").SetValue(0, 1.0)
aspen.find_node(r"\Data\Reactions\Reactions\R-METH\Input\COEF\1\H2\MIXED").SetValue(0, 4.0)

# 5. 填充 COEF1（产物计量系数）
coef1_1 = aspen.find_node(r"\Data\Reactions\Reactions\R-METH\Input\COEF1\1")
for i in range(2): coef1_1.Elements.InsertRow(0, i)
coef1_1.Elements.SetLabel(0, 0, False, "CH4")
coef1_1.Elements.SetLabel(1, 0, False, "MIXED")
coef1_1.Elements.SetLabel(0, 1, False, "H2O")
coef1_1.Elements.SetLabel(1, 1, False, "MIXED")
aspen.find_node(r"\Data\Reactions\Reactions\R-METH\Input\COEF1\1\CH4\MIXED").SetValue(0, 1.0)
aspen.find_node(r"\Data\Reactions\Reactions\R-METH\Input\COEF1\1\H2O\MIXED").SetValue(0, 2.0)

# 6. 设动力学参数
aspen.find_node(r"\Data\Reactions\Reactions\R-METH\Input\PRE_EXP\1").SetValue(0, 1.0e8)
aspen.find_node(r"\Data\Reactions\Reactions\R-METH\Input\ACT_ENERGY\1").SetValue(0, 14300)
```

#### 分配反应集到反应器模块

RPLUG/RCSTR 通过 `RXN_ID` 表分配反应集。**必须先清空再添加**：

```python
rxn_id = aspen.find_node(r"\Data\Blocks\RPLUG\Input\RXN_ID")
els = rxn_id.Elements
# 清空所有现有行
while els.Count > 0: els.RemoveRow(0, 0)
# 添加第一个反应集
els.InsertRow(0, 0)
aspen.find_node(r"\Data\Blocks\RPLUG\Input\RXN_ID\#0").SetValue(0, "R-1")
# 添加第二个反应集（可有多个）
els.InsertRow(0, 1)
aspen.find_node(r"\Data\Blocks\RPLUG\Input\RXN_ID\#1").SetValue(0, "R-METH")
```

RXN_ID 表**不支持** `SetLabel`（会报错"该维度无标签"），直接用 `#0`, `#1` 索引路径赋值。

#### 读取反应结果

反应器出口的详细组成在 `Streams → {name} → Output → MOLEFLOW → MIXED → {组分名}`：

```python
mf = aspen.find_node(r"\Data\Streams\R-OUT\Output\MOLEFLOW")
for i in range(10):
    c = mf.Elements(0).Elements(i)  # Elements(0) = MIXED 子流
    if c.Value and float(c.Value) > 0:
        print(f"  {c.Name} = {c.Value}")
```

总摩尔流量变化（R-IN vs R-OUT）可以判断反应是否发生：若出口总摩尔数 < 入口，说明发生了分子数减少的反应。

#### 温度对反应的影响

RPLUG 的 `TYPE` 参数决定温度模式：
- `ADIABATIC` — 绝热（不设温度，由反应热决定）
- `T-SPEC` — 指定温度剖面（需要设 `TEMP`）
- `CONSTANT-T` — 恒温（注意：`CONSTANT-T` 不是有效值，实际用 `T-SPEC`）

设温度：`aspen.find_node(r"\Data\Blocks\RPLUG\Input\TEMP").SetValue(0, 350.0)`

### 批量操作加速

大批量改参数前调 `batch_refresh(True)` 关闭 GUI 刷新，改完再 `batch_refresh(False)` 恢复。

---

### 运行策略：Reinit + Run

修改参数或拓扑后，运行的正确流程是：

```
reinit()   → 清空旧计算结果和引擎缓存
run()      → 执行计算
```

或一步到位：
```
reinit_and_run()   → Reinit() + Run2(0) 组合
```

**实测验证：** `Reinit()` 可以强制 Aspen 引擎在下一步 `Run2()` 时重建计算顺序（Calculation Order）和撕裂流股（Tear Streams），不需要人工手动运行。

之前有用户反馈"必须在 GUI 中手动运行一次后才能自动运行"——这个问题的原因是：
1. 参数通过直接 `SetValue` 写入失败（AE_UNDERSPEC），实际参数没写入
2. 缺少 `Reinit()` 步骤，旧缓存与修改后的拓扑不匹配

**解决这两个问题后，模拟可以完全通过 COM 自动化运行，无需人工干预。**

---

### 启动：`new_simulation()`

`new_simulation()` 使用 Aspen 内置 `InitNew()` 创建空白模拟。

**初始状态：**
- **单元制**：ENG（°F / psia / lbmol/hr）—— 可用 `set_unit_set("METCBAR")` 切换为公制
- **组分**：none（通过 `add_component` 添加）
- **物性方法**：PENG-ROB（可用 `set_property_method` 更改）
- **拓扑**：空（无模块、无物流）

```
new_simulation()         → 创建空白模拟
set_unit_set("METCBAR")  → （可选）切换单位制
add_component(...)       → 添加组分
add_block / connect      → 搭建流程拓扑
reinit_and_run()         → 运行
```

---

### COM 单例限制

MCP 服务器持有一个全局 Aspen Plus COM 会话。每次重启 MCP 客户端（或 MCP 插件）会丢失之前的所有状态，需要重新 `open_file` 或 `new_simulation`。
同一进程内的文件切换（close + open_file / new_simulation）可正常进行，不受影响。

### 模块参数文档

项目 `docs/blocks/` 目录下有 34 种 Aspen Plus 模块的详细参数文档（每种含基础 + `-advanced` 补充，共 68 个模块文档，另加 `index.md` 与 `common-advanced.md`），来源于 Aspen Plus v15 官方模板。

---

## 开发与测试

### 离线测试

单元测试完全离线（fake COM bridge，不依赖 Aspen Plus）：

```bash
venv\Scripts\python -m pytest tests/unit -q
```

> 注意：`tests/` 根目录下另有 4 个早期独立测试文件（`test_bugfix_units.py`、`test_attrvalue_diag.py`、`test_units.py`、`test_unitset_switch.py`），其自带 mock 尚未跟进最近的单位/契约改造，且与 `tests/unit` 混跑会互相干扰；运行测试请限定 `tests/unit`。

### 代码结构

```
src/aspen_mcp/
├── server.py            FastMCP 注册入口（79 工具）+ 统一审计包装
├── bridge/              COM 桥：windows（真实 COM）/ stub（非 Windows 内存模拟）
├── tools/               工具实现（simulation / streams / blocks / columns / reactions /
│                        flowsheet / analysis / configuration / components / utilities / …）
├── execution.py         执行账本（任务沿袭、运行额度、deadline）
├── tool_audit.py        工具调用审计（audit.jsonl）
├── stream_contract.py   流股写入契约与能力矩阵（未验证语义拒绝写入）
├── checkpoint.py        checkpoint 存档与续跑对账
├── knowledge/           收敛故障知识库
├── cache.py             结果 JSON 缓存
└── _directories.py      审计 / 状态目录回落策略
```

---

## License

MIT