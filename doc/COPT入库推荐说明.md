# COPT 入库推荐与命令行测试

在项目根目录执行，Windows、Linux 使用相同命令：

```shell
python -m pip install -r requirements.txt
python -m tools.test_inbound_copt
```

需要该平台有效的 COPT 许可证。测试直接调用真实 COPT，不启动 Web 服务、不调用远程平台，也不会自动回退启发式。缺依赖、许可证失效、求解无可行解或规则校验失败，均返回退出码 1；全部场景校验通过返回 0。合法的部分上架方案通过规则校验时也返回 0，部分上架状态记录为 `partial`。超时但已有合法可行解也可以通过测试，是否证明最优需查看 `solver_stages` 和 `warnings`。

默认运行 `data/testing/inbound_requirement_cases.json` 的全部场景，另加三个 COPT 专用目标场景。按数量守恒、库存不移动、货主隔离、托容量、整托层位、混托触发条件、追加资格、路径及距离校验。原场景的 `expected` 是启发式专用断言，不用于 COPT；COPT 专用场景额外检查预期上架箱数与距离。

默认每个场景 60 秒、8 线程；全部场景需要多次求解。可调整：

```shell
python -m tools.test_inbound_copt --time-limit 120 --threads 4 --mip-gap 0.005 --output results/testing/my_copt_run
python -m tools.test_inbound_copt --input data/testing/my_inbound.json
```

输出目录保存 `summary.json`，以及每个场景编号目录下的 `input.json` 和 `result.json`，失败也保留记录。请为每次运行使用不同的 `--output` 目录，避免覆盖旧记录。自定义输入可以是单个场景对象或场景对象数组，字段格式与内置场景相同：

- `inventory`、`orders`、`specs`：库存、入库订单、SKU 主数据；沿用当前页面数据格式。
- `slots`：可选的货位编号列表；省略时使用完整立体库，空列表表示无可用布局。
- `grades`：可选数组，例如 `[["WH", "O", "A", "高"]]`；未指定条目按低周转等级。
- `layer_weight`：可选的层间距离权重，默认 1。
- `copt_expected`：可选，例如 `{"assigned_boxes": "40", "total_distance": 10.35}`，指定后严格校验。

命令行场景直接使用输入中的周转等级，便于重放；页面使用完整订单历史重算等级，两者共用同一个模型与 COPT 求解实现。

## 模型规则

每条订单按托含量拆成整托和尾托，每个拆托整体指派到一个货位或留作未上架，不跨订单合并整托，不移动已有库存。整托不能放一层；尾托可以退让到高层。仅当待入库托数大于空位数，或尾托数大于一层空位数时，开启合法尾托混托和库存追加。

新尾托混托仅允许同货主、不同 SKU、位于一层；容量不超过各 SKU 托含量的最小值。库存追加仅针对一层、同货主、原先只有单一 SKU 且未满的货位；已有混托、上层货位及满托禁止追加。库存是否冻结不影响占位。每个接收货位计一次从 STAGING 出发的单程搬运，混托也计一次，与现有启发式距离口径一致。

目标依次为：最大化上架箱数（主单位数量÷转换率）、最小化总搬运距离、最小化不符合层位偏好的拆托数。最后一项目标仅用于前两项同优的方案。尾托偏好一层，高周转整托偏好二层，中周转整托偏好二至四层，低周转整托偏好五层。

各阶段共用 `time_limit_s`，包含建模时间，不含输入准备与结果保存。前两阶段相对间隙设为 0，只有证明最优后才固定目标进入下一阶段；配置的 `mip_gap` 用于最后层位偏好阶段。超时返回最近阶段的可行方案并提示未证明最优；完全没有可行方案时返回失败。模型使用浮点 MIP 系数，业务数量和结果仍以 Decimal 保存，规则测试检查实际数量和容量。

## 页面与后端

算法设置选择入库 COPT，设置时间、线程数和相对间隙后保存。入库页面及 `POST /api/slotting/plan` 会调用新模型，结果沿用独立展示页面及 `results/warehouse/<run_id>/` 存储。COPT 运行错误和许可证失效会生成失败记录。OR-Tools 的新版立体库入库接口仍未接通。

模型在 `modules/slotting/inbound_model.py`，COPT 求解与结果转换在 `solvers/warehouse_inbound_copt.py`，命令行入口在 `tools/test_inbound_copt.py`。COPT API 使用方式参考 [杉数官方 Python 文档](https://guide.coap.online/copt/en-doc/pythoninterface.html)。

无有效许可证时的开发回归：

```shell
python -m pytest tests/test_inbound_copt.py tests/test_inbound_requirements.py -q
```

开发测试通过测试适配器使用 OR-Tools/SCIP 求解同一模型，验证业务约束、阶段调度和结果转换；不替代有效许可证平台上的真实 COPT 测试。
