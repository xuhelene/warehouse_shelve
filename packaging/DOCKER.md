# Linux Docker 交付

目标平台：64 位 x86_64（linux/amd64）。服务器需预装 Docker Engine 和支持 `up --wait` 的 Docker Compose v2，开放实际使用的服务端口。

## 构建

在项目根目录运行 `powershell -ExecutionPolicy Bypass -File packaging/build-docker.ps1`。
构建机器需要联网下载基础镜像和 Python 依赖。输出目录为 `dist/docker-linux-amd64/`，包含离线镜像 tar、Compose 配置、启动脚本和 SHA256 校验值。
脚本构建后会启动隔离容器，等待健康，执行页面与入出库启发式 HTTP 冒烟测试，并用新容器验证挂载目录中的结果文件仍可读取。测试通过后才导出镜像，日志保存为 `container-test.log`。测试数据保存在构建机器的 `.tmp/docker-test-*`，不会进入交付镜像。

镜像包含现有工作区应用代码及运行所需的示例数据，不包含历史结果、本机算法配置、许可证、开发环境和测试产物。首次启动默认使用启发式。

## 客户部署

将整个交付目录复制到服务器，在该目录执行：

```sh
sha256sum -c SHA256.txt
sh deploy.sh
```

启动脚本会导入离线镜像并等待服务健康。浏览器访问 `http://服务器IP:8010/whshelve/`。
自定义端口：`WAREHOUSE_PORT=8080 sh deploy.sh`。后续 Compose 管理命令也应使用相同端口环境变量。
镜像加载和服务启动不需要访问镜像仓库或 Python 包源。
网关必须保留 `/whshelve` 前缀。

结果与上传文件存储在交付目录 `results/`，算法设置存储在 `config/`，容器重建后保留。备份这两个目录即可保留运行数据。
CSV/XLSX 可通过页面上传；镜像已包含默认数据。如需替换默认数据，可另行通过 Compose 挂载完整数据目录至 `/app/data`。

```sh
docker compose ps
docker compose logs --tail 100
docker compose down
docker compose up -d --wait
```

## 功能范围与验证边界

此次打包不改动求解逻辑。立体库入出库页面使用启发式；原有任务入口的 OR-Tools 后端保留。
现有 COPT 后端通过本地 coptpy 调用，尚未实现远程 COPT API 适配。镜像保留依赖，但不包含任何许可。
客户远程 COPT API 暂不可访问，本次不验证 COPT 授权或远程求解；选择 COPT 不代表已接通客户 API。
远程库存及 SKU 平台接口同样仍是原有待联调状态，当前使用文件快照方式。
