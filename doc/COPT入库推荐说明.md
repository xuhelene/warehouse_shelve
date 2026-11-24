# COPT 入库推荐与命令行测试

## 三种算法对比

### 第五阶段：拆分预处理耗时与批次几何缓存

新增 `--copt-distance-cache off|batch`，默认off保留逐次精确几何重建。batch仅用于COPT combined目标，在本次命令行进程内复用精确货位距离和通道节点水平距离，不缓存库存、候选可行性、最优解、距离整数单位、偏好权重或数值安全结论。每次仍按当前候选集合和原始拆托数重做整数化、K及系数上界检查。

缓存每次校验完整的节点坐标、备货区STAGING、通道拓扑和边费用、货位节点与层号、层间权重及内置距离方法。任意变化清空原几何缓存并重算；只保留最后一种几何，缓存规模受该布局节点和货位数量限制，不跨进程、不落盘、不跨页面共享。不同日期库存占用变化本身不影响几何，新的候选货位首次遇到时才计算并补入缓存。候选减少时只整数化本次候选，不把历史缓存中其他货位混进来。

只对标准WarehouseLayout及内置距离方法缓存；自定义/覆盖的方法绕过缓存并走原验证流程，报告bypassed及逐次enabled=False。直接原地修改布局对象时，调用方仍须正确维护布局自己的shortest缓存；对比脚本每次新建布局，不复用该对象的旧最短路径。此缓存不改变路径算法本身。

新增四个计时字段，均是 modeling_s 的子项，不能重复加到total_s：

| 字段 | 内容 |
| --- | --- |
| distance_cache_check_s | 完整布局签名构造、比较与缓存选择 |
| distance_geometry_s | 本次所需的精确几何距离重建，含命中检查和字典取值 |
| distance_integerize_s | 当前候选的共同分母、公共因子、整数费用与数值安全检查 |
| combined_expression_s | 构造组合目标的COPT表达式 |

modeling_s减去以上四项，还包含原拆托、聚合、模型变量约束构造以及阶段间模型设置。结果中的objective_strategy.preprocess_timings、CSV、runs及summary.backends.copt.timings均可查看，异常时尽可能保留已完成计时；未完成的整段可能仍计入other_s。

建议本轮同时测试原三阶段、合并无缓存、合并有缓存，避免平均小幅变快但中位数仍退化时误判。所有模式保持聚合、环境复用、批量读取、相同参数：

```bash
for variant in sequential combined_off combined_batch; do
  strategy=combined
  cache_mode=off
  case "$variant" in
    sequential) strategy=sequential ;;
    combined_batch) cache_mode=batch ;;
  esac
  ../python/bin/python3.11 -m tools.compare_inbound_solvers \
    --data-dir "data/武汉联众_20260901-20261009_每日库存与订单" \
    --specs data/sku.xlsx --start-date 2026-09-01 --end-date 2026-10-10 \
    --time-limit 60 --threads 8 --repeats 3 \
    --model-variant aggregate --copt-env reuse --copt-read bulk \
    --copt-objectives "$strategy" --copt-distance-cache "$cache_mode" \
    --backends copt --output "results/testing/phase5_$variant"
done
```

目录须不存在；重跑换新目录名。沿用已确认的全部低等级、每日独立快照和缺主数据失败策略。第一至第四阶段已同步时，本阶段只需同步：

```text
modules/slotting/inbound_objectives.py
solvers/warehouse_inbound_copt.py
tools/compare_inbound_solvers.py
```

不新增依赖，不改变OR-Tools求解路径或Web默认行为。每次脚本从空缓存开始，首次构建成本与后续布局校验均计入算法调用；没有把预计算移出计时。模型变量规模和目标优先级保持，组合模式继续使用零相对间隙。

新增 distance_cache.json，summary顶层distance_cache同内容，记录模式、checks、generations、invalidations、bypassed、slot_hits、slot_misses以及缓存的货位/节点数。代数首次生成计入generations，不算invalidations。同一几何下首次访问的货位是miss，已缓存的是hit；不能要求第一次日期覆盖后续所有候选，所以后续仍可能少量miss。客观判断收益须包含冷启动、校验及整数化开销。

逐次CSV/JSON新增distance_cache_requested、distance_cache_enabled、distance_cache_changed、distance_cache_hits、distance_cache_misses；changed也包含首次冷启动。summary.backends.copt.distance_cache汇总通过运行的命中/重建数。顶层全批次统计可能还包含失败运行，不要混淆两个范围。sequential不调用该预处理，其四个子项为0，也不会产生缓存命中。

先核对目标、最优性、变量规模及实际目标策略，再比较整体均值、中位数、P95，以及上述四个预处理子项，尤其检查combined_batch是否消除了combined_off的小场景退化。缓存只针对几何计算，若主要耗时在combined_expression_s，可能不会明显提速。测试后请提供三组summary.json、comparison.csv、runs.json、copt_environment.json及distance_cache.json。

### 第四阶段：严格合并距离和层位偏好目标

新增 `--copt-objectives sequential|combined`，默认sequential保持原三阶段。combined仍先单独最大化上架箱数并证明最优，再固定该数量，执行一次“整数距离×K＋层位偏好违背数”的最小化。K为原始拆托数＋1，偏好违背数不超过原始拆托数，因此距离整数费用改善1单位优先于全部偏好变化。页面层间距离权重不变，仍用于“水平距离＋层间权重×垂直距离”。没有更改上架量口径、混托、货主、容量或分配变量。

整数距离由实际STAGING通道路径坐标的有理数差及1.7×层间权重重建，取共同分母再除公共因子，不按固定小数位近似舍入。distance_unit记录一个整数距离单位代表的实际加权距离。重建值必须与现有距离在1e-8绝对误差内一致，否则拒绝合并。为控制数值规模，合并系数保守上限为1e7，组合目标上界保守限制为1e12。超过限制、非支持的几何费用、没有候选变量等情况，会保留原三阶段并在objective_fallback_reason记录原因；非平凡回退也写入warnings。

组合目标阶段强制RelGap=0（数量阶段也为0），不能沿用偏好阶段的0.5%间隙：组合目标的相对间隙可能允许牺牲更高优先级的距离。原sequential保持原终止规则。只有组合目标证明最优、目标和界相符，才同时标记距离和偏好已证明最优；超时可行解不会冒称两个目标分别已最优。

在同一客户服务器项目根目录对照：

```bash
for strategy in sequential combined; do
  ../python/bin/python3.11 -m tools.compare_inbound_solvers \
    --data-dir "data/武汉联众_20260901-20261009_每日库存与订单" \
    --specs data/sku.xlsx --start-date 2026-09-01 --end-date 2026-10-10 \
    --time-limit 60 --threads 8 --repeats 3 \
    --model-variant aggregate --copt-env reuse --copt-read bulk \
    --copt-objectives "$strategy" --backends copt \
    --output "results/testing/phase4_$strategy"
done
```

输出目录必须不存在；重测换目录名。需同步四个文件（包含新增模块）：

```text
modules/slotting/inbound_model.py
modules/slotting/inbound_objectives.py
solvers/warehouse_inbound_copt.py
tools/compare_inbound_solvers.py
```

无需新增依赖，不修改OR-Tools策略、Web默认目标或出库求解。依赖本工程现有路径几何；特殊布局/高精度权重不安全时明确回退。自定义输入仍可用--input，已知缺托含量日期依然失败并继续其他日期。

报告新增：

- copt_objectives_requested / copt_objectives_actual：请求及实际使用的目标策略。
- copt_solve_calls：实际调用model.solve次数（含异常尝试）；对应summary.backends.copt.objective_strategies.solve_calls的通过运行汇总。
- distance_unit / preference_weight：精确距离单位及K。
- max_combined_coefficient / combined_objective_upper_bound：数值规模检查。
- objective_fallback_reason：未能启用组合目标的原因。
- combined_solve_s：组合目标求解耗时，与quantity_solve_s一起参与实际求解耗时统计。
- objective_strategy.combined_stage：runs.json及完整result内记录实际组合目标值、界、gap、状态、用时和最优性；该组合目标值不是米数。

为保持业务目标可读性，solver_stages仍显示quantity、distance、preference。组合模式的后两项带 derived_from=combined，是从同一组合解分解出的实际距离和偏好数，没有单独调用solve，也没有单独计时。在未证明组合最优时，两项best_bound/mip_gap均为null，实际组合目标界和gap只能看combined_stage，不能把组合界直接当作距离界。

因此组合模式的 distance_solve_s、preference_solve_s 为0，**不是这两项工作没有成本**，相关成本统一计在combined_solve_s中。最优性和三个业务目标值核对无误后，再比较摊销总用时、建模整数化开销、quantity_solve_s＋combined_solve_s与原三个阶段之和。整数化和加权可能增加建模或搜索成本，不保证一定提速。

若13个有效日期×3轮全部正常完成且无回退，sequential应有117次solve、combined有78次solve；批量读取调用也相应由117变为78。读取变量值总数会减少，因为实际求解阶段少一个，不要求与原三阶段读取总数相同。模型变量规模及三个业务目标必须不变。请保留两版summary.json、comparison.csv、runs.json和copt_environment.json用于分析。

### 第三阶段：COPT批量读取变量解

新增 `--copt-read scalar|bulk`，默认scalar保留逐变量 `.x` 读取。bulk在每个有可行解的非平凡阶段使用公开接口 `model.getInfo(COPT.Info.Value, list(x.values()))`，只读取分配变量，再按传入列表与原键顺序还原。不使用全模型变量列表或变量编号，避免混托辅助变量与分配变量错位；每个阶段重新读取，不复用上一阶段的解值。解向量长度不符、含NaN/Infinity或接口错误都会记录失败，不静默退回scalar。

本阶段保持整托聚合和批次环境复用，三个目标及参数不变。新选项 `--backends copt` 可只运行COPT，避免重复OR-Tools测试；不指定仍保持三种算法对比。下面两版均只测COPT，可直接比较。不要把之前混合运行三种算法的时间直接当作本轮scalar基线。

```bash
for read_mode in scalar bulk; do
  ../python/bin/python3.11 -m tools.compare_inbound_solvers \
    --data-dir "data/武汉联众_20260901-20261009_每日库存与订单" \
    --specs data/sku.xlsx --start-date 2026-09-01 --end-date 2026-10-10 \
    --time-limit 60 --threads 8 --repeats 3 \
    --model-variant aggregate --copt-env reuse --copt-read "$read_mode" \
    --backends copt --output "results/testing/phase3_$read_mode"
done
```

目录须不存在；重复测试换新目录名。第一、二阶段文件已同步时，本阶段只需同步 solvers/warehouse_inbound_copt.py 和 tools/compare_inbound_solvers.py，不新增依赖，不修改OR-Tools求解器、Web默认行为或出库算法。

新增报告项：

- `copt_read_mode`：逐次实际读取方式；完整result中的 `solution_read.mode` 对应此值；environment.json 的config.copt_read_mode记录命令行选择。
- `variable_read_calls`：Python层变量值读取API调用尝试数；scalar每个变量一次，bulk每个已执行阶段一次。该计数不是实际网络请求数，COPT内部可能缓存或另行实现传输。
- `variable_values_read`：成功读取并检查的分配变量值个数，两种模式相同阶段应一致。
- `variable_read_s`：变量值读取、长度/有限值校验及选择映射耗时。它是原 `result_read_s` 的子项，**不能再相加**。原result_read_s仍包含阶段状态/目标/界/gap读取及最终业务结果构造。

这些字段进入逐次CSV/JSON，完整结果的timings、solution_read和各阶段记录也有相应信息。summary.json 的 backends.copt.solution_read 汇总模式、调用尝试数和成功读取值数，timings.variable_read_s 汇总用时；读取统计包含带有该字段的失败运行，需核对runs和错误。只运行COPT时summary.backends只含copt，pairs为空，不表示未执行算法有失败。

若现有13个有效日期各3轮、全部完成三个非平凡阶段且分配变量规模与第二阶段相同，预期两版读取值数均为122220，scalar读取调用数为122220，bulk为117。未执行、平凡或无可行解阶段不读取变量；某阶段读取异常时会计一次bulk尝试但不累计成功值数，故须结合stages判断，不能强制要求所有数据固定117。

先核对两版目标值、最优性和模型规模，再对比 variable_read_s、result_read_s 以及 copt_environment.amortized_seconds。55ms的旧结果读取时间还包含其他工作，不能预设批量读取能省去全部55ms。如果variable_read_s下降但总用时未改善，应检查其他阶段波动。测试后提供两版 summary.json、comparison.csv、runs.json、copt_environment.json。

### 第二阶段：同一批次复用COPT环境

新增 `--copt-env fresh|reuse`，默认 fresh，保持每次调用新建环境。reuse 只在此次命令行进程、创建环境的同一线程内串行使用；第一次通过输入准备的COPT调用才创建环境，后续调用复用，每次创建独立模型、独立设置参数。脚本结束（包括正常Python异常或Ctrl+C）关闭环境；强制杀进程、系统掉电不保证finally执行。当前Web页面不共享这一批次环境，未将环境生命周期绑定页面，也未改动出库求解。

本阶段保持 --model-variant aggregate，不叠加其他求解改动。客户服务器在项目根目录执行：

```bash
for mode in fresh reuse; do
  ../python/bin/python3.11 -m tools.compare_inbound_solvers \
    --data-dir "data/武汉联众_20260901-20261009_每日库存与订单" \
    --specs data/sku.xlsx --start-date 2026-09-01 --end-date 2026-10-10 \
    --time-limit 60 --threads 8 --repeats 3 \
    --model-variant aggregate --copt-env "$mode" \
    --output "results/testing/phase2_$mode"
done
```

目录必须不存在；重测时换新目录名。继续保留两个缺托含量日期的失败，不会为这些输入创建环境。无需额外依赖，第一阶段文件已同步的前提下，本次只需同步 solvers/warehouse_inbound_copt.py 和 tools/compare_inbound_solvers.py。

逐次新增 environment_reused、model_cleanup 和 model_release_s。优先调用版本支持的 model.dispose()；旧版没有该方法时使用公开的 model.clear() 清空变量、约束和目标，函数返回后丢弃模型引用（不调用私有析构接口）。clear 不等同于 dispose，原生句柄的最终释放由该版本对象生命周期管理，因此当前先限于有限串行批次测试。模型清理耗时已计入每次调用，两种模式使用同样的清理逻辑。环境支持dispose时，在close后还会dispose；不要求客户离线升级COPT。

某次建模、求解或模型清理异常时，废弃并关闭该批次环境，下一次有效COPT调用重新创建；不自动重试失败任务，原失败仍保留。如果模型结果已返回后才在外部业务校验失败，该模型已清理，环境继续保留。环境失效导致的原生API异常也按失败处理，不能保证任何类型的服务端断连都可无损恢复。

新增 copt_environment.json，同时复制到 summary.json 顶层的 copt_environment：

- mode：fresh或reuse。
- created / reused_calls / close_attempts：reuse模式的环境成功创建次数、复用调用次数、关闭尝试次数；create_attempts还包含失败尝试。
- create_seconds / close_seconds：环境生命周期函数累计时间，便于排查；这些时间已在调用或最终关闭计时中统计，不要再重复加到总时间。
- sum_call_seconds：全部已进入求解调用的COPT seconds之和，包含求解失败，不含数据准备失败。
- final_close_seconds：脚本finally块中完成最终环境关闭的耗时。
- accounted_seconds：sum_call_seconds + final_close_seconds。
- amortized_seconds：accounted_seconds / timed_calls，包含初始化和最终关闭的摊销耗时；timed_calls包含已进入算法的失败调用。只有两边计时样本及失败分布一致时才直接比较。
- cleanup_errors：最终或异常后关闭时的错误；不为空时脚本返回非零退出码。

reuse的初始化计入第一个有效COPT调用的environment_s；其他调用该项主要为取得已有环境的微小开销。最终关闭发生在全部算法测试之后，不归入任何一条运行的environment_close_s，但计入上述accounted_seconds和amortized_seconds。原backends.copt.mean_seconds仍只统计通过运行的调用时间，因此判断总收益应同时检查copt_environment，不能把初始化/最终关闭遗漏。

以当前13个有效日期、3轮为例，无新增求解错误时应为 created=1、reused_calls=38、close_attempts=1。逐行第一条COPT结果的environment_reused=False，其余为True。如created>1，检查是否发生模型/连接异常。比较两版上架箱数、距离、偏好违背数、optimality及model_stats应保持；结果的具体等价货位可以不同。优先关注摊销用时、environment_s下降及model_release_s是否抵消收益，不预设一定能节省102ms。

测试后请保留两版 summary.json、comparison.csv、runs.json 和 copt_environment.json。先验证批次复用效果，再评估Web后端环境管理；多进程、多用户的服务端环境池不在本阶段实现范围内。

### 模型提速第一阶段：等价整托聚合

新增 `--model-variant baseline|aggregate`（默认 baseline）。两种版本保持相同的三个目标、层间距离权重及求解参数；aggregate 同时用于 COPT 和强制 SCIP 对比，启发式不变。页面及不带新选项的调用暂时仍使用原模型，待客户环境测出实际收益后再决定是否设为默认。

aggregate 仅将同仓库、同货主、同SKU、主单位与转换率相同、每托数量/容量相同、等级与合法候选货位相同的整托合成一类。类的变量仍为每个货位一个二元变量，但该类可占用的货位数上限为原整托数。尾托不聚合；混托触发仍使用原始拆托数。最终分配按原拆托顺序稳定展开，保留订单号、批次、时间及精确数量，部分上架时未选成员仍逐托输出未上架明细。等价托的货位与原始行对应关系可能变化，三项目标和业务合法性必须保持。

在同一服务器上依次运行两版，其他参数保持一致（目录须不存在）：

```bash
for variant in baseline aggregate; do
  ../python/bin/python3.11 -m tools.compare_inbound_solvers \
    --data-dir "data/武汉联众_20260901-20261009_每日库存与订单" \
    --specs data/sku.xlsx --start-date 2026-09-01 --end-date 2026-10-10 \
    --time-limit 60 --threads 8 --repeats 3 \
    --model-variant "$variant" --output "results/testing/phase1_$variant"
done
```

每版的 `environment.json` 记录 aggregate_full 开关。对比原始及聚合版的上架箱数、distance、preference_violations、optimality 和分项耗时；目标相同才比较提速，未证明最优应单独分析。不要只比较求解状态。首次输出目录已存在时换新目录名。

`comparison.csv`、`runs.json` 和完整结果新增模型规模信息，`summary.json` 的 backends.<算法>.model_stats 汇总通过运行的字段：

- original_groups / model_groups：原始拆托数、聚合后模型类数（含单独尾托）。
- aggregated_classes：真正包含多个整托的类数。
- assignment_variables：实际分配二元变量数，不含其他辅助变量。
- baseline_assignment_variables：相同候选条件下原逐托模型需要的分配变量数。
- removed_assignment_variables：削减的分配变量数。

第一阶段需要同步 modules/slotting/inbound_model.py、solvers/warehouse_inbound_copt.py、solvers/warehouse_inbound_ortools.py、tools/compare_inbound_solvers.py。客户每日数据入口仍需此前的 tools/inbound_customer_cases.py 等已有文件。不新增依赖。9/10、9/21缺托含量时两版仍会返回失败并继续其他日期，这类输入错误不应计为聚合提速或退化。

后续阶段将分别评估完整上架可行性证明后跳过数量求解、严格整数化合并距离与偏好目标；本阶段尚未启用，避免多项变化叠加而无法归因。聚合已经去掉同类整托的编号排列，暂不再叠加lex约束；不同类或不等价托盘不能强行排序作为对称性处理。

### 分项计时与客户每日数据

COPT 新增 `timings`，在完整结果、`runs.json`、`metrics.json`、`comparison.csv` 中均可查看；`summary.json` 的 `backends.copt.timings` 汇总各项 total/mean/median/p95（秒）：

| 字段 | 含义 |
| --- | --- |
| import_s | COPT Python 接口导入 |
| environment_s | create_env 创建环境，包含在该调用内发生的许可校验、集群客户端初始化和连接 |
| modeling_s | 拆托、模型创建、变量、约束、目标及阶段之间的目标固定 |
| quantity_solve_s | 最大化上架箱数阶段调用 solve 的墙钟时间 |
| distance_solve_s | 最小化距离阶段调用 solve 的墙钟时间 |
| preference_solve_s | 层位偏好阶段调用 solve 的墙钟时间 |
| result_read_s | 求解状态、变量与目标属性读取，以及业务结果构造 |
| environment_close_s | env.close 的墙钟时间 |
| other_s | total_s 中未归入以上项的时间；失败时也可能包含未完成的建模或读取步骤 |
| total_s | COPT函数内部总用时，包含关闭环境 |

实际集群通信也可能在模型创建、提交或结果读取时发生，因此 environment_s 不是全部网络耗时，各阶段 solve 计时也不等于远端求解内核时间。无候选变量的平凡阶段为0；未执行阶段为0并须结合 solver_stages 判断。COPT异常会尽可能保留已记录的分项时间。分项汇总包括带有该字段的失败运行，runs列明样本数；原有平均 seconds 仍只统计业务校验通过的运行。

SCIP 新增三个阶段的 solve_time_s，对比脚本提取为相同的 quantity_solve_s/distance_solve_s/preference_solve_s；没有 COPT 环境创建/关闭项。启发式无这三阶段，分项列留空，保留统一的 seconds 耗时。所有分项使用 perf_counter 高精度墙钟计时。原 solve_time_s 保持旧口径，不包含COPT环境关闭；新的 total_s 包含关闭，用 seconds 对照整个算法调用。

无需手工转JSON，可直接读取当前每日Excel目录（必须同时含“库存快照”和“入库订单”子目录，文件名含YYYY-MM-DD）：

```shell
../python/bin/python3.11 -m tools.compare_inbound_solvers --data-dir "data/武汉联众_20260901-20261009_每日库存与订单" --specs data/sku.xlsx --start-date 2026-09-01 --end-date 2026-10-10 --time-limit 60 --threads 8 --repeats 1
```

首次建议1轮确认数据及许可，再将 --repeats 改为3。--start-date/--end-date 均包含边界；缺省时读目录内全部日期。--specs 缺省为项目 data/sku.xlsx。按已确认的测试口径，所有SKU按低周转等级；同一天使用同一份期初库存，各算法独立测试，不把推荐结果回写次日。仅支持单文件对应单日；同一天出现多个库存或订单文件会明确报错。

新输出 `dataset_report.json` 列出订单文件实际起止日期、各日文件、空订单跳过及加载失败信息。没有入库订单文件的日期不补造订单，也不进入测试统计；空表跳过；缺库存、格式错误、SKU缺托含量等保留失败，其他日期继续测试。主数据缺失不自动补值，也不删除相应SKU。当前随项目文件实际为9/1—10/9，10/10只有回推锚点，设置结束日10/10不会生成该日场景；如有10/10订单和期初库存，按同样命名补进目录即可。

客户数据入口需要同步：tools/compare_inbound_solvers.py、tools/inbound_customer_cases.py、solvers/warehouse_inbound_copt.py、solvers/warehouse_inbound_ortools.py，另需现有 tools/test_inbound_copt.py、业务模块、data/sku.xlsx及每日Excel目录。不新增第三方依赖。

在项目根目录运行：

```shell
../python/bin/python3.11 -m tools.compare_inbound_solvers --time-limit 60 --threads 8 --repeats 3
```

普通 Python 环境可将 `../python/bin/python3.11` 替换为 `python`。默认使用内置入库需求场景及三个目标回归场景；需要同时带上新增的 `tools/compare_inbound_solvers.py` 和更新后的 `solvers/warehouse_inbound_ortools.py`。不新增依赖，COPT 仍需有效许可。

自定义数据沿用下文场景 JSON 格式：

```shell
../python/bin/python3.11 -m tools.compare_inbound_solvers --input data/testing/my_inbound.json --time-limit 120 --threads 4 --repeats 3 --output results/testing/customer_compare_01
```

`--output` 必须为尚不存在的目录，避免覆盖历史测试；不指定则自动生成时间戳目录。仅一轮可设 `--repeats 1`（默认）。执行次数为场景数×重复次数×3，时限按每次算法调用分别分配。启发式保持现有单线程实现，忽略 threads/mip_gap；OR-Tools 禁用匹配快捷路径，明确使用 SCIP。页面的 OR-Tools 调用仍保留原有快捷求解行为。

结果保存在 `results/testing/inbound_compare_<时间戳>/`：

- `comparison.csv`：逐场景、逐轮、逐算法的耗时、上架箱数、未上架箱数、距离、偏好违背数、搬运次数、最优性及错误。
- `summary.json`：各算法通过/失败数、证明最优/未证明最优次数、部分上架次数；校验通过运行的累计、平均、中位数及 P95 耗时；三组算法两两比较的更优/同优次数。
- `runs.json`：逐次统计，包含各阶段目标、界、gap 和最优性结论。每次运行后更新，完整跑完后才生成 CSV 和汇总。
- `environment.json`：Python、系统、架构、相关库版本及求解配置。
- 场景编号子目录：同一输入，以及各轮各算法完整 `result.json`、`metrics.json`。

控制台打印每次运行及最后的汇总。单个算法运行失败后会继续运行其余算法，最终只要有失败就返回退出码 1；全部规则校验通过返回 0。合法部分上架也算通过，**通过不等于最优**。不使用启发式专用的 expected 或 COPT 专用期望值来强制另一算法达到同样结果；共用校验检查库存、数量、容量、层位、混托、路径等业务约束。

比较顺序为上架箱数越大越好、箱数相同距离越小越好、前两项相同偏好违背数越少越好。仅比较同一场景同一轮两边都通过的结果；失败不能判定为另一方在解质量上获胜。距离比较容差为 1e-7，箱数使用 Decimal 精确比较。启发式原策略优先层位偏好，与精确算法的目标优先级不同，报告展示的是实际方案差异。

seconds 是调用算法的墙钟用时：不含布局与输入准备、规则校验和文件保存，包含调用内部的许可检查、集群连接和建模。算法内部延迟导入也可能计入首轮用时。solve_time_s 是后端自身计时（如提供），以 seconds 为统一耗时口径。各算法顺序轮换；平均/中位数/P95按所有通过的场景轮次统计，不只统计三方共同通过的样本，因此失败数不同时请同时检查逐场景数据。两边同优时再比较对应运行的耗时。

内置小场景用于正确性及目标行为验证。性能测试应另用客户实际规模数据，并记录 CPU、负载及 COPT 集群节点配置；远程 COPT 与本机 SCIP 的用时体现整个调用链路，不代表相同硬件上的纯求解器性能。

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

算法设置选择入库 COPT，设置时间、线程数和相对间隙后保存。入库页面及 `POST /api/slotting/plan` 会调用新模型，结果沿用独立展示页面及 `results/warehouse/<run_id>/` 存储。COPT 运行错误和许可证失效会生成失败记录。

模型在 `modules/slotting/inbound_model.py`，COPT 求解与结果转换在 `solvers/warehouse_inbound_copt.py`，命令行入口在 `tools/test_inbound_copt.py`。COPT API 使用方式参考 [杉数官方 Python 文档](https://guide.coap.online/copt/en-doc/pythoninterface.html)。

无有效许可证时的开发回归：

```shell
python -m pytest tests/test_inbound_copt.py tests/test_inbound_requirements.py -q
```

开发测试通过测试适配器使用 OR-Tools/SCIP 求解同一模型，验证业务约束、阶段调度和结果转换；不替代有效许可证平台上的真实 COPT 测试。

## Web 优化组合接入（2026-11-24）

Web 入库选择 COPT 后，自动启用整托聚合、环境复用、批量读取解值、距离与层位偏好的精确合并目标、距离缓存。仍先最大化上架箱数，再最小化距离，最后比较层位偏好。层间距离权重仍进入距离计算；合并目标不改变这三个目标的优先级。整数化安全检查未通过时，自动使用原来的分阶段目标，并记录原因。合并阶段相对间隙为 0；页面 mip_gap 仅用于回退方案的末阶段。

调度在 `solvers/warehouse_inbound_runtime.py`：每个服务进程一个专属线程，同时执行一个入库 COPT 请求，最多另排队 8 个。超出时生成失败记录，提示“COPT入库任务繁忙，已有8个请求排队，请稍后重试”。排队时间独立记录，不占用求解 time_limit_s。每次请求仍创建、释放独立模型；只复用环境及几何距离缓存，不复用订单、库存、业务规则或解。请求提交时复制配置上下文，避免多组织仓库之间串用。

第一次实际求解才创建环境；打开页面不占用环境，关闭页面也不释放环境。正常服务关闭时，等待已接收任务完成，并由原线程关闭环境。异常会废弃环境，下一次请求重新创建，不自动重试失败请求。强制终止进程不保证执行清理。缓存只保留最近一套布局，布局、路径、货位或层间权重改变时重建。

本方案的串行与排队上限是**每进程**的；多个服务进程分别持有环境和队列，可能额外占用许可证资源。当前建议单服务进程部署。默认求解器仍为启发式；出库和 OR-Tools 调度不受此次接入影响。命令行对比工具仍保留各实验开关，便于回归旧组合。

输入快照和结果新增 `effective_solver_config`，记录实际采用的组合；结果的 `copt_runtime` 包括进程/线程编号、排队时间、累计环境创建和复用次数、缓存统计。原有 `timings`、`environment_reused`、`objective_strategy`、`solution_read` 保留。原始页面设置仍存入 `settings`。最终环境关闭发生在服务退出时，不计入某一次请求的耗时。

部署与验收：

1. 同步当前代码，保留第五阶段版本的模型、求解器和距离缓存文件，并包含新增的 `solvers/warehouse_inbound_runtime.py`，以及修改的 `modules/slotting/planning_service.py`、`apps/warehouse_service.py`、`apps/web_portal.py`、`apps/static/settings.js`。完整重启 Web 服务，不只刷新页面。
2. 在算法设置中选择入库 COPT 并保存；对同一数据连续执行两次。检查 `results/warehouse/<run_id>/input.json` 和 `result.json`。
3. 两次结果的 `effective_solver_config` 应含 `aggregate_full=true`、`copt_read_mode="bulk"`、`copt_objectives="combined"`、`copt_distance_cache="batch"`；第二次应为 `environment_reused=true`，同一进程中 `environment_created` 通常仍为 1。若期间求解失败导致重建，累计次数会增加。
4. 比较两次上架箱数、距离、未上架原因与规则校验结果；关注第二次缓存命中及建模用时。第一次包含冷启动，不能直接套用第五阶段的平均值。更改层间权重后再次运行，缓存应失效重建。
5. 正常停止服务，确认没有环境关闭异常。缺少有效许可证的本地环境仅能执行以下开发回归；客户环境仍需以上真实 COPT 验收。

```shell
python -m pytest tests/test_inbound_runtime.py tests/test_inbound_copt.py tests/test_copt_environment_reuse.py tests/test_copt_bulk_read.py tests/test_copt_combined_objectives.py tests/test_copt_distance_cache.py -q
```
