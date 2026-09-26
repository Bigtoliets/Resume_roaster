# 基础设施部署（MySQL + Milvus，不含 Langfuse）

## 1. 为什么单独一份 compose

Langfuse 已经跑在服务器上（`http://192.168.10.99:3000`，v4.38.0），它自带的编排含 minio / redis / postgres / clickhouse，并占用了一批宿主机端口。把项目基础设施并进去会互相踩端口、互相牵连升级。所以这里只负责 **MySQL + Milvus（standalone = etcd + minio + milvus）**。

## 2. 端口冲突规避（关键）

Langfuse 已占用的宿主机端口：

| 端口 | 被谁占用 |
| --- | --- |
| 3000 | langfuse-web |
| 3030 | langfuse-worker |
| 8123 / 9000 | clickhouse |
| 9090 / 9091 | langfuse 自带 minio |
| 6379 | redis |
| 5432 | postgres |

本项目只使用这三个：

| 端口 | 用途 | 说明 |
| --- | --- | --- |
| 3306 | MySQL | 对外暴露，本机开发要连 |
| 19530 | Milvus gRPC | 对外暴露 |
| 19091 | Milvus 健康检查 | 原本应在 9091，会与 Langfuse minio 冲突，改绑 `127.0.0.1:19091` |
| 8001 | Attu（Milvus 界面） | Attu 容器内固定监听 3000，宿主 3000 已被 Langfuse 占用，因此改到 8001 |

etcd 的 2379 与 Milvus 自己的 minio 的 9000 只走容器内网，不发布到宿主机。

## 3. 需要新拉的镜像

你服务器上有 `mysql:latest`，但**没有 Milvus 相关镜像**，需要拉三个：

```bash
docker pull milvusdb/milvus:v2.5.4
docker pull quay.io/coreos/etcd:v3.5.18
docker pull minio/minio:RELEASE.2024-12-18T13-15-44Z
docker pull zilliz/attu:v2.5
```

Milvus 版本要求 ≥2.4（稀疏向量 + hybrid search 依赖它），推荐 2.5+。若 `v2.5.4` 拉不到，换成仓库里最新的 2.5.x / 2.6.x tag 即可。

服务器上已有一个 `cgr.dev/chainguard/minio`，那是 Langfuse 在用的；Milvus 建议用官方 minio 镜像，避免 entrypoint 差异导致的启动问题。

## 4. 部署

```bash
mkdir -p /opt/resume-roaster/deploy
cd /opt/resume-roaster/deploy
# 只需把 docker-compose.infra.yml 拷进来，不需要任何 env 文件

docker compose up -d
docker compose ps
```

参数全部写死在 compose 里，`docker compose up -d` 可直接运行。内置的默认凭据：

| 项 | 值 | 需要修改时改哪里 |
| --- | --- | --- |
| MySQL root | `ResumeRoot2026` | compose 中 `mysql.environment.MYSQL_ROOT_PASSWORD` |
| MySQL 应用账号 | `resume` / `ResumeApp2026` | 同上；**改完要同步改开发机项目根 `.env`** |
| MinIO（Milvus 内部用） | `minioadmin` / `MinioRoot2026` | compose 中 `minio.environment.MINIO_ROOT_PASSWORD` 与 `milvus.environment.MINIO_SECRET_ACCESS_KEY`，两处必须一致 |

如果服务器只有你自己用，保持默认即可；若内网还有其他人，建议改掉。

## 5. 验证

```bash
# MySQL：库是否建出来
docker exec -it resume-mysql mysql -uroot -p -e "show databases;"

# Milvus：健康检查（宿主机回环）
curl -s http://127.0.0.1:19091/healthz      # 期望 OK

# Milvus：服务端版本（需先装 pymilvus）
python -c "from pymilvus import utility; print(utility.get_server_version())"
```

回到本机后，把地址填进项目根目录 `.env`，执行 `python scripts/check_env.py`，全绿即可开工。

## 5.1 Attu 可视化界面

启动后浏览器打开 `http://192.168.10.99:8001`，登录页的 Milvus 地址填 `192.168.10.99:19530`（`MILVUS_PUBLIC_URL` 会自动预填）。

用途：人工核对语料是否入库正确——看 collection 的 schema、行数、抽样数据，以及后续手工调试检索。

两个注意点：

1. Attu 是纯前端应用，**浏览器直接连 Milvus**，所以填的必须是浏览器可达的地址（不能用容器名 `milvus:19530`）。
2. Attu 镜像 tag 尽量与 Milvus 大版本一致（Milvus 2.5.x 配 `zilliz/attu:v2.5`），跨大版本可能字段显示异常。

## 6. 安全建议（别跳过）

1. **Milvus 默认没有认证**，把 19530 暴露到局域网等于谁都能读写你的向量库。二选一：
   - 防火墙只放通你的开发机 IP：`firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=<你的IP> port port=19530 protocol=tcp accept'`；
   - 或开启 Milvus 认证（容器内 `milvus.yaml` 设 `common.security.authorizationEnabled: true` 并设 root 密码），再在项目 `.env` 填 `MILVUS_USER` / `MILVUS_PASSWORD`。
2. **MySQL 不要暴露到公网**，只在内网使用；两个密码都用强密码。
3. Langfuse 的 secret key 已经以明文形式出现过，配置稳定后建议在后台轮换一次。

## 7. 资源与备份

内存：Milvus standalone 建议预留 2~4GB，MySQL 约 0.5GB；Langfuse 那套（clickhouse 等）另需 4GB 以上。部署前确认服务器余量。

备份（建议加进 crontab）：

```bash
docker exec resume-mysql sh -c 'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --databases resume_roaster' > backup_$(date +%F).sql
```

Milvus 数据落在命名卷 `milvus_data` / `etcd_data` / `minio_data` 中，随卷一起备份即可。

## 8. 常见问题

**报错 `required variable XXX is missing a value`**

说明用的是旧版带变量插值的 compose。当前 compose 已改为全部写死，把最新的 `docker-compose.infra.yml` 覆盖过去，直接 `docker compose up -d` 即可。

**Milvus 起不来 / 一直 unhealthy**

按顺序排查：`docker compose logs etcd` → `docker compose logs minio` → `docker compose logs milvus`。常见原因是内存不足（Milvus standalone 建议预留 2~4GB，且服务器上还跑着 Langfuse 那一套）或镜像没拉全。

**Attu 打开后连不上 Milvus**

Attu 是纯前端应用，浏览器直连 Milvus。登录页地址要填 `192.168.10.99:19530`（宿主机地址），不能填容器名 `milvus:19530`。

**3306 / 19530 / 8001 端口被占用**

`ss -lntp | grep -E '3306|19530|8001'` 查出占用者，再改 compose 里对应的宿主机端口（改完开发机 `.env` 也要同步）。

## 9. 启动与重启行为（手动管理）

**本 compose 的容器不做自动恢复**：每个服务都是 `restart: "no"`。含义是：

- Docker 守护进程重启或服务器重启后，这些容器**不会**自己起来；
- 需要你手动执行 `docker compose up -d`（已存在的容器会被重新启动，数据卷不受影响）。

日常命令：

```bash
cd /root/Resume_roaster/deploy
docker compose up -d        # 启动（或重启已停止的容器）
docker compose ps           # 查看状态
docker compose restart      # 重启
docker compose stop         # 停止（保留容器）
docker compose down         # 停止并删除容器（数据卷保留）
```

Docker 自身是否开机自启与本 compose 无关，按需设置：

```bash
systemctl is-enabled docker      # 是否为 enabled
systemctl enable --now docker    # 需要开机自启时执行
```

所以：**服务器重启后，端口"积极拒绝"是预期现象**，手动 `docker compose up -d` 即可恢复。若想开机自动恢复，把各服务的 `restart: "no"` 改成 `unless-stopped`（或直接 `systemctl enable docker` 并保持 `restart: always`）。
