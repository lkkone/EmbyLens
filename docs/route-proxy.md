# 每条线路独立使用宿主机代理

后台的主线路、备用1、备用2都有“使用代理”开关，默认关闭。开启后该线路的账号认证、健康检测、数量采集统一使用 `OUTBOUND_PROXY_URL`；关闭时忽略 HTTP_PROXY、HTTPS_PROXY 等系统设置，明确直连。代理不可用时不会回退直连，也不会自动变更客户端标识。正常线路仍可用于数量采集，主线路优先。

代理地址只在服务端环境变量中配置，不会返回后台接口或公开接口，也不会加入复制配置内容。支持 HTTP 代理（HTTPS 目标通过 CONNECT 隧道），以及提供 HTTP 协议的 mixed 端口；SOCKS-only 或要求 TLS 连接代理本身的端口不能直接填写，请使用代理软件的 HTTP/mixed 监听端口。带认证时可填写 `http://user:password@host:port`，特殊字符需 URL 编码。

## Docker Desktop：macOS / Windows

在部署 `.env` 中添加：

```dotenv
OUTBOUND_PROXY_URL=http://host.docker.internal:7897
```

端口根据实际代理软件配置填写，不固定为 7897。Docker Desktop 提供宿主机域名；容器中的 127.0.0.1 指向容器本身。代理是否允许 Docker 虚拟网络连接需要实际验证。如果代理仅监听回环且当前 Docker Desktop 无法连接，请调整代理的监听范围，或使用一个受限的宿主机转发入口。

## Linux Docker Engine

Docker Engine 20.10+ 使用宿主机网关映射：

```sh
docker compose -f compose.yaml -f compose.host-gateway.yaml up -d
```

代理地址仍可以使用 `http://host.docker.internal:端口`。宿主机代理须监听 Docker 网桥可达的地址，并允许相应网段连接。仅监听 127.0.0.1 的 Linux 代理一般不能被桥接容器访问，可限定监听 Docker 网桥地址。自定义 Compose 服务名时调整覆盖文件中的服务名；生产部署可用相同覆盖文件叠加 compose.deploy.yaml。旧 Docker 可显式映射实际网桥网关地址，而不是写死 172.17.0.1。

## 直接运行 Python

在进程环境或 scripts/run.py 使用的 `.env` 中配置 `OUTBOUND_PROXY_URL=http://127.0.0.1:端口`。也可指定部署环境中可达的其他代理地址。

## 操作与排查

修改代理地址后重新创建容器使环境生效；修改线路开关后点击保存会立即安排采集。未配置或地址格式错误时，开启代理的保存操作会返回明确错误。NO_PROXY 不会覆盖已明确开启的线路开关。

容器能连接代理并不代表目标服务一定可用：代理软件自身的规则决定实际出口，HTTP 525 等错误仍可能来自目标服务。不要为了某一线路随意修改整个宿主机代理的全局模式。
