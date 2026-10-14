# warehouse_shelve

物流仓储优化 demo，仅包含货位推荐和拣货路径推荐。
保留当前模型和示例规则；立体库规则适配尚未完成。

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
页面支持上传输入文件、选择求解器、设置时间上限与线程数，并下载结果。

拣货默认订单为 data/picking_path/orders_wave.csv。
默认库存为 results/slotting_solution.csv；首次使用需上传库存文件，或上传货位推荐生成的结果。
Web 求解结果保存在 results/web_jobs/<任务编号>/outputs/ 下。

命令行入口：apps/run_slotting.py、apps/run_picking_path.py。
