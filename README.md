# warehouse_shelve

物流仓储优化 demo，仅包含货位推荐和拣货路径推荐。
立体库启发式已接入入出库页面规划；使用方法及接口见 [启发式算法使用说明](doc/启发式算法使用说明.md)。

## 功能

- 货位推荐：基于已有库存和新增入库货物推荐货位，支持启发式、COPT、OR-Tools。
- 拣货路径推荐：基于订单、库存和通道网络生成路径，支持启发式、OR-Tools；COPT 后端尚未实现。
- 三维货架预览：展示立体库布局。

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

浏览器访问 http://127.0.0.1:8010/。COPT 需要有效本机许可。
货位推荐页面 `/modules/slotting` 支持 CSV/XLSX 上传、样例加载及三维库存预览。
库存样例读取 `data/slotting/existing_inventory.csv`；订单样例读取 `data/slotting/orders.csv` 中 2026-09-22 的入库记录。
页面支持启发式规划及独立结果查看；使用前在算法设置中选择启发式。原有算法接口保留。
出库拣货页面 `/modules/picking-path` 使用相同布局、上传及库存悬浮提示；订单样例仅取 2026-09-22 的出库记录。
出库采用 FIFO 选货，按批次开关控制按单或整次请求波次规划；支持一托容量、货主隔离及缺货明细。原有拣货算法接口保留。
分层职责和数据接口见 `doc/前后端分层说明.md`。

首页“算法设置”进入 `/settings`，入库和拣货两个 tab 独立保存求解器、时间上限、线程数；入库精确后端还可设置相对最优间隙。
初始值为杉数求解器、60 秒、8 线程及入库相对间隙 0.5%。配置保存到运行目录的 `config/algorithm_config.json`。新立体库规划读取该配置，当前仅支持启发式；线程数及相对间隙不生效，不改变旧任务接口的参数。

首页“SKU 周转等级”进入 `/sku-turnover`：支持 CSV/XLSX 完整订单历史手动更新，以及使用 `data/slotting/orders.csv` 初始化。
滚动30天包含截止日，样例以最新出库日期为截止日，上传默认以当天（UTC+8）为截止日，也可指定日期。
按主单位数量÷转换率计算入库＋出库箱数（1件=1箱），按仓库+货主+SKU分别统计；各仓库内按搬运量降序统一排名，累计贡献80%/95%分级，跨阈值条目归当前等级，零搬运量最低；同量先按SKU、再按货主编码排序。
对窗口内有入库或出库记录的条目评级，每次使用本次完整文件重新计算，不累加旧文件，不自动去重；源文件应避免重复作业记录。
`GET /api/sku-turnover` 读取最新等级，`POST /api/sku-turnover/sample` 初始化，`POST /api/sku-turnover/update` 接收 multipart `file` 和可选 `end_date`（YYYY-MM-DD）。
每次输入及计算结果保存在 `results/sku_turnover/<运行编号>/`，最新结果为 `results/sku_turnover/latest.json`；入库规划使用最近一次历史重算等级，周期更新尚未启用。

周转阈值分析脚本：`python tools/analyze_turnover_thresholds.py`。可指定 `--orders`、`--end-date YYYY-MM-DD`、`--output`；默认按样例最新作业日期统计连续30天。
输出 `results/turnover_analysis/analysis.md`、`summary.csv`、`details.csv` 和 `report.json`，比较仅出库与入库+出库箱数、原三等分、条目比例及累计贡献阈值；属于候选分析，不替换正式分类算法。


拣货默认订单为 data/picking_path/orders_wave.csv。
默认库存为 results/slotting_solution.csv；首次使用需上传库存文件，或上传货位推荐生成的结果。
Web 求解结果保存在 results/web_jobs/<任务编号>/outputs/ 下。

命令行入口：apps/run_slotting.py、apps/run_picking_path.py。
