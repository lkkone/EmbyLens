# EmbyLens

多 Emby 服务器状态面板，沿用 ProxyLens 的 Vue 3、Python 标准库、SQLite 和界面。首页公开展示账号可见的媒体数量与线路状态，`/admin` 登录管理。主线路与两个备用服务器分别填写名称和 HTTPS 地址；服务器名称留空自动获取。

## 本地开发

```sh
npm --prefix frontend ci
cp .env.example .env
# 编辑管理账号、密码和安全码
./run-local.sh
```

访问 `http://localhost:18791`。启动脚本读取 `.env`；未指定管理员时，在 `.local/admin.json` 生成本地账号。开发数据位于 `.local/data/embylens.db`，包含 Emby 凭证与令牌，必须保护备份。`.env`、数据库、截图和编译产物不进入仓库。

## 接入与采集

- 默认 Forward 请求格式；可以按服选择已允许的客户端标识。没有全行业通用白名单，能连接不等于服主允许自动统计。
- 新增与保存修改优先安排首次采集；后台浏览只读取本地缓存。
- 主备顺序检测，固定逻辑设备 ID，复用成功登录令牌；同一线路请求至少间隔 0.5 秒。
- 正常检测至少间隔 10 分钟，带少量错峰；数量约每小时更新。
- 403 不轮换客户端身份，下一轮正常检测再试。401 在令牌续期失败后暂停该线路，保存配置后重试。429 遵循 `Retry-After`；连接故障逐次延长重试间隔。
- 不请求播放流，不测速、下载、预加载、转码或修改播放列表、观看记录。采集不能代替观看保号。
- HTTPS 校验证书，跨地址重定向不转发凭证。主备返回不同服务器身份时提示异常。

## 数据口径

电影、剧集、单集只包含该账号可见的条目；总数是电影加单集，不跨库或跨版本去重。单集接口异常时尝试总数减去电影数，不在认证失败或限流时追加查询。

今日新增按北京时间的 `DateCreated` 计算电影和单集，最多读取 400 个最近条目。超过范围、缺失日期或查询失败时显示“—”，不会把部分数量当成精确值。这个字段取决于服主的入库设置，可能与实际上架时间不同。失败保留上次成功统计，并标注更新时间和错误。

状态条覆盖最近 5 小时，每格 5 分钟。每格取该时间段最后一条真实采集记录，悬停显示原始记录时间；没有记录则留灰，不沿用旧值，也不与当前状态强制同步。超过 30 分钟没有完成检测视为采集中断。响应耗时包含账号验证，不代表播放速度。

## 密码与复制配置

查看已保存密码和复制配置需要管理员登录及 `.env` 中的 `SECURITY_CODE`。5 分钟内错误 5 次暂时锁定。密码显示 30 秒后隐藏，编辑、切换服务器、保存、退出时清除；修改密码留空保留原密码。

复制内容只包含主路线、两个副路线、用户名、密码，每行 `字段:值`，不加引号，空备用地址保留空行；未指定 HTTPS 端口时补 `443`。安全码和访问令牌不在复制内容中。密码和安全码不下发到普通管理列表或公开 API。

## 部署

生产沿用 ProxyLens：预构建 amd64 镜像、Docker Compose 持久化卷、Caddy HTTPS 反向代理。公网域名为 `https://emby.likaikai.com`，管理入口 `/admin`。

```sh
docker build --platform linux/amd64 -t embylens:0.1.0-amd64 .
mkdir -p dist
docker save embylens:0.1.0-amd64 | gzip > dist/embylens-0.1.0-linux-amd64.tar.gz
# 将镜像、compose.deploy.yaml 和私密 .env 传到服务器
docker load -i embylens-0.1.0-linux-amd64.tar.gz
```

加载镜像时使用 `dist/embylens-0.1.0-linux-amd64.tar.gz` 的实际路径。在部署目录执行 `docker compose -f compose.deploy.yaml up -d`。Caddy 加入 `caddy_default` 网络，配置 `reverse_proxy embylens:8080`。生产仅映射 `127.0.0.1:18792`，启用 Secure Cookie、只读容器文件系统和权限收缩。配置账号密码或安全码后重新运行 `up -d`。

数据保存在 `embylens-data` 命名卷。升级重建容器保留数据，不要执行 `down -v`。默认保留 30 天采样历史。迁移时使用 SQLite 在线备份，清除旧管理会话；同一账号不要同时运行开发与生产两个采集器。可在本地 `.env` 设置 `COLLECTOR_ENABLED=false` 保留本地页面但停用采集。

## 验证

```sh
python3 -m pip install -r requirements-dev.txt
ruff check server scripts tests
black --check server scripts tests
python3 -m unittest discover -s tests
npm --prefix frontend run build
```

Docker 构建同时验证后端测试，镜像健康检查访问 `/healthz`；GitHub Actions 执行静态检查、测试、前端与镜像构建。测试使用临时数据库，不访问真实 Emby 服务。

每条线路可独立启用宿主机代理，配置方式见 [线路代理说明](docs/route-proxy.md)。
