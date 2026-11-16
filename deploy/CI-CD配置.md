# 拾光电商 Agent CI/CD 配置指南

推送 `main` 后，GitHub Actions 会运行后端和前端测试，构建 Linux amd64 镜像并推送到阿里云 ACR。设置好 SSH 配置并启用部署后，Actions 会让 Ubuntu 拉取本次镜像摘要并更新容器。部署只传很小的配置包，模型密钥和会话密钥留在服务器 `.env`。

本项目使用 Compose 项目名 `xiaohan-commerce`，公网端口默认 `8891`，服务器目录为 `/home/ubuntu/apps/xiaohan-commerce-agent`。这套配置与复习项目的 `8888` 端口和镜像仓库名分开。

## 1. 创建镜像仓库

在阿里云容器镜像服务控制台，选择有公网地址的实例和命名空间，在命名空间下创建两个私有镜像仓库：

- `xiaohan-commerce-backend`
- `xiaohan-commerce-frontend`

在控制台的访问凭证页面设置仓库密码，并记录 Docker 登录用户名。仓库地址应使用控制台提供的公网域名，例如 `crpi-xxxx.cn-guangzhou.personal.cr.aliyuncs.com`；不要填写 `https://`、命名空间或仓库名。

## 2. 配置 GitHub Actions

打开 GitHub 仓库的 **Settings → Secrets and variables → Actions**。本仓库需要单独配置以下值，其他仓库的 Secrets 不会自动共享。

在 **Secrets** 中添加：

| 名称 | 内容 |
| --- | --- |
| `REGISTRY_USERNAME` | ACR 控制台 Docker 登录命令中的用户名 |
| `REGISTRY_PASSWORD` | ACR 仓库密码或有推送权限的访问凭证 |
| `SSH_HOST` | Ubuntu 服务器的公网 IP 或域名 |
| `SSH_USER` | SSH 用户，当前服务器是 `ubuntu` |
| `SSH_PRIVATE_KEY` | 可登录该服务器的私钥完整内容，不是 `.pub` 公钥 |
| `SSH_KNOWN_HOSTS` | 已核实的服务器 SSH 主机公钥记录 |

在 **Variables** 中添加：

| 名称 | 内容 |
| --- | --- |
| `REGISTRY_HOST` | ACR 公网域名，例如 `crpi-xxxx.cn-guangzhou.personal.cr.aliyuncs.com` |
| `REGISTRY_NAMESPACE` | ACR 命名空间，例如 `xiaohan-study` |
| `DEPLOY_ENABLED` | 首次先填 `false`；服务器准备好后改为 `true` |
| `DEPLOY_PATH` | `/home/ubuntu/apps/xiaohan-commerce-agent` |
| `SSH_PORT` | `22` |

不要把任何密码、私钥或模型 Key 放入 Variables、仓库文件或工作流日志。

## 3. 准备 Ubuntu

在服务器上以运行 Docker 的 `ubuntu` 用户执行。首次准备项目目录和数据目录：

```bash
mkdir -p ~/apps/xiaohan-commerce-agent/runtime/data
```

确保该目录中的 `.env` 已按项目的 `.env.example` 配好模型参数和 `SESSION_SECRET`，并且 `knowledge/` 目录包含仓库中的商品资料。首次部署前可在服务器上克隆此仓库到 `~/apps/xiaohan-commerce-agent`；保留现有 `.env` 和 `runtime/data/`，不要用代码包覆盖它们。

以 `ubuntu` 用户登录 ACR 一次，让 Docker 能拉取两个私有镜像：

```bash
read -r -p 'ACR 公网域名：' REGISTRY_HOST
read -r -p 'ACR 登录用户名：' REGISTRY_USERNAME
docker login "$REGISTRY_HOST" --username "$REGISTRY_USERNAME"
```

按提示输入 ACR 密码，看到 `Login Succeeded` 即可。使用普通 `docker login`，不要加 `sudo`，否则凭证会保存在 root 用户下。

确保 SSH 公钥已经追加到 Ubuntu 用户的 `~/.ssh/authorized_keys`。`SSH_KNOWN_HOSTS` 应使用服务器本机的 SSH host key，并先通过可信渠道核对指纹；不要直接把未经核实的 `ssh-keyscan` 结果当作可信记录。非 22 端口时，known_hosts 行首格式为 `[服务器地址]:端口`。

## 4. 首次运行与启用部署

先保持 `DEPLOY_ENABLED=false`，把本地变更提交并推送到 `main`。Actions 应依次通过后端测试、脚本测试、前端测试、Compose 检查，再构建并推送两个镜像。首次推送前，必须创建两个镜像仓库并配置好 ACR Variables/Secrets。

检查构建成功后，在 Ubuntu 确认目录、配置和端口：

```bash
cd ~/apps/xiaohan-commerce-agent
test -f .env && test -d knowledge && test -d runtime/data
docker compose version
```

在 GitHub 将 `DEPLOY_ENABLED` 改为 `true`。之后每次 `main` 有新提交，流水线都会先测试、构建并推送，再通过 SSH 更新 Ubuntu。也可以在 Actions 页面手动运行工作流。PR 只运行测试，不会登录仓库、推镜像或部署。

## 5. 查看结果与恢复

在 Ubuntu 查看容器和 API：

```bash
cd ~/apps/xiaohan-commerce-agent
docker compose ps
curl -f http://127.0.0.1:8891/api/health
cat .deploy/current-revision
```

浏览器使用 `http://服务器公网IP:8891/`。安全组/防火墙需开放 TCP `8891`；容器内部的 `8020` 不需要对公网开放。

服务器会保留每次发布的小型 Compose 配置和镜像摘要。镜像拉取失败时不会切换容器；新容器或 Nginx 到 API 的检查失败时，会恢复上一份成功发布的 Compose 配置。模型配置、`runtime/data/` 和 PostgreSQL 数据不随发布包覆盖。排查时可运行：

```bash
docker compose logs --tail=100 backend frontend
```

第一次自动部署前若目录中没有 `.deploy/current-revision`，流水线没有可恢复的上一份 CI 发布版本；先确认目录、`.env`、资料、ACR 登录和 8891 端口均可用，再启用自动部署。
