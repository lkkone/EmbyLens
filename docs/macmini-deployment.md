# Mac mini 部署

服务部署在 `~/github/lkk-one/yuhome/embylens/deploy`，沿用 yuhome 的 Docker Compose 与 `home-embylens` 命名。使用 ARM64 镜像，SQLite 保存在 `deploy/data/embylens.db`，环境配置在权限为 600 的 `deploy/.env`。

将 `compose.macmini.yaml` 保存为该目录的 `docker-compose.yaml`，设置 ADMIN_USERNAME、ADMIN_PASSWORD、SECURITY_CODE，加载 ARM64 镜像后执行 `docker compose up -d`。数据目录须由镜像中的 embylens 用户拥有；运行前可通过镜像以 root 执行 `chown -R embylens:embylens /data` 完成初始化。

云端 Caddy 将 `emby.likaikai.com` 转发到 `10.24.0.3:18792`；ProxyLens 使用 `10.24.0.3:18790`。TLS 和公网入口仍在云端，应用的 COOKIE_SECURE 保持 true。Mac mini 应持续运行 Docker，并确保云端可通过组网访问以上端口。

迁移使用 SQLite 在线备份，迁移后停止云端两个应用容器，避免双重采集。原云端卷和受限权限的备份保留以供回滚。ProxyLens agent 的公网地址不变，无需重装 agent。

容器不会自动继承 macOS 系统代理。ONE 是否恢复须通过 Mac mini 容器实际采集验证；不能仅凭本机第三方客户端成功判断。
