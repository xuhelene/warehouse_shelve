# warehouse_shelve

物流仓储优化 demo，仅包含货位推荐和拣货路径推荐。
立体库启发式已接入入出库页面规划；使用方法及接口见 [启发式算法使用说明](doc/启发式算法使用说明.md)。

## 功能

- 货位推荐：基于已有库存和新增入库货物推荐货位，支持启发式、COPT、OR-Tools。
- 拣货路径推荐：立体库页面支持启发式和 COPT，旧版通道网络入口支持启发式和 OR-Tools。在算法设置中为拣货路径选择 COPT 后，系统先按尾板优先和 FIFO 选货，再联合优化已拆分拣货明细的分托和托内访问顺序。结果保存在 `results/warehouse/`，包含完整路线和各任务求解状态。
- 三维货架预览：展示立体库布局。

## 命令行测试立体库拣货路径

安装 `requirements.txt` 中的依赖并配置有效的 COPT 许可后，从项目根目录执行以下命令。默认使用 COPT 跑四个内置场景，覆盖跨层路线、多趟卸货、波次货主隔离和缺货。

```bash
python -m tools.check_outbound_planning
```

传入自有数据时，JSON 格式与出库规划 API 请求相同，支持单个请求或请求数组。

```bash
python -m tools.check_outbound_planning --input outbound.json --time-limit 30 --threads 4 --output results/testing/my-outbound
```

使用 `--backend heuristic` 可运行启发式对照。命令逐场景打印 PASS 或 FAIL，并在输出目录保存 `summary.json` 及各次运行的 `input.json`、`result.json`。默认输出目录位于 `results/testing/`。全部通过时退出码为 0，场景失败（含 COPT 回退初始方案）为 1，参数或输入加载错误为 2。

## 目录

- apps/：启动入口、页面、后台任务和结果展示
- core/：公共模型、数据读取和路径管理
- data/slotting/：货位与库存示例
- data/picking_path/：订单和拣货网络示例
- modules/slotting/、modules/picking_path/：业务建模与服务
- solvers/：求解后端
- results/：求解结果和上传文件
- doc/、packaging/：需求、预览说明和打包配置

## 启动

安装 requirements.txt 中的依赖后，在项目目录运行：

```powershell
python apps/web_portal.py --host 127.0.0.1 --port 8010
```

浏览器访问 http://127.0.0.1:8010/whshelve/。客户网关需将 `/whshelve` 及其子路径转发到本服务，并保留该前缀。COPT 需要有效本机许可。
货位推荐页面 `/whshelve/modules/slotting` 支持 CSV/XLSX 上传、样例加载及三维库存预览。
库存样例读取 `data/slotting/existing_inventory.csv`；订单样例读取 `data/slotting/orders.csv` 中 2026-09-22 的入库记录。
页面支持启发式、COPT 入库规划及独立结果查看；使用前在算法设置中选择后端。原有算法接口保留。
出库拣货页面 `/whshelve/modules/picking-path` 使用相同布局、上传及库存悬浮提示；订单样例仅取 2026-09-22 的出库记录。
出库采用 FIFO 选货，按批次开关控制按单或整次请求波次规划；支持一托容量、货主隔离及缺货明细。原有拣货算法接口保留。
分层职责和数据接口见 `doc/前后端分层说明.md`。

首页“算法设置”进入 `/settings`，入库和拣货两个 tab 独立保存求解器、时间上限、线程数；入库精确后端还可设置相对最优间隙。
初始求解器为启发式。入库推荐默认 10 秒、1 线程及相对间隙 0.5%，拣货路径推荐默认 60 秒、8 线程。配置保存到运行目录的 `config/algorithm_config.json`。立体库入库支持启发式、COPT 和 OR-Tools。启发式为单线程。COPT 入库及跨平台命令行测试见 [COPT入库推荐说明](doc/COPT入库推荐说明.md)。

首页“SKU 周转等级”进入 `/sku-turnover`：支持 CSV/XLSX 完整订单历史手动更新，以及使用 `data/slotting/orders.csv` 初始化。
滚动30天包含截止日，样例以最新出库日期为截止日，上传默认以当天（UTC+8）为截止日，也可指定日期。
按主单位数量÷转换率计算入库＋出库箱数（1件=1箱），按仓库+货主+SKU分别统计；各仓库内按搬运量降序统一排名，累计贡献80%/95%分级，跨阈值条目归当前等级，零搬运量最低；同量先按SKU、再按货主编码排序。
对窗口内有入库或出库记录的条目评级，每次使用本次完整文件重新计算，不累加旧文件，不自动去重；源文件应避免重复作业记录。
`GET /whshelve/api/sku-turnover` 读取最新等级，`POST /whshelve/api/sku-turnover/sample` 初始化，`POST /whshelve/api/sku-turnover/update` 接收 multipart `file` 和可选 `end_date`（YYYY-MM-DD）。
每次输入及计算结果保存在 `results/sku_turnover/<运行编号>/`，最新结果为 `results/sku_turnover/latest.json`；入库规划使用最近一次历史重算等级，周期更新尚未启用。

周转阈值分析脚本：`python tools/analyze_turnover_thresholds.py`。可指定 `--orders`、`--end-date YYYY-MM-DD`、`--output`；默认按样例最新作业日期统计连续30天。
输出 `results/turnover_analysis/analysis.md`、`summary.csv`、`details.csv` 和 `report.json`，比较仅出库与入库+出库箱数、原三等分、条目比例及累计贡献阈值；属于候选分析，不替换正式分类算法。


拣货默认订单为 data/picking_path/orders_wave.csv。
默认库存为 results/slotting_solution.csv；首次使用需上传库存文件，或上传货位推荐生成的结果。
Web 求解结果保存在 results/web_jobs/<任务编号>/outputs/ 下。

命令行入口：apps/run_slotting.py、apps/run_picking_path.py。
