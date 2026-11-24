# -AI

#How to run 砸扣AI？
## 1. Clone the responsitory:
  
    git clone https://github.com/tq78885-boop/-AI.git
 

## 2. Navigate to the project directory:
   
    cd -AI
  

## 3. create a virtual environment:
  
    conda create -n zakou-AI python=3.11 -y
   

## 4. Activate the virtual environment:
  
    conda activate zakou-AI


## 5. Install the required packages:
  
    pip install -r requirements.txt


## 6. Run the program:

    python app.py

---

## 部署到阿里云 ECS（GitHub Actions CI/CD）

这一节把项目从「本地 `python app.py`」升级为「推送到 `main` 分支即自动部署到阿里云服务器」。

### 0. 流水线总览

`.github/workflows/cicd.yaml` 定义了 3 个 Job：

| Job | 触发时机 | 作用 |
| --- | --- | --- |
| `ci` | push / PR / 手动 | 安装依赖、`compileall` 语法检查、`import app` 冒烟测试 |
| `build` | push / PR / 手动 | 构建 Docker 镜像；非 PR 时推送到阿里云 ACR（`latest` + `<commit sha>` 两个标签） |
| `deploy` | 仅 push 到 `main` / 手动 | SSH 到 ECS：备份旧镜像 → 拉新镜像 → 重建容器 → 健康检查 → 失败自动回滚 |

```
git push (main)
      │
      ▼
GitHub Actions ── ci ──► build ──► push 阿里云 ACR
                                        │
                              appleboy/ssh-action（SSH）
                                        ▼
阿里云 ECS ── docker pull ──► 重建 zaikou-ai 容器 ──► 健康检查（失败回滚）
                                        │
        /opt/zaikou-ai/{.env, data/, chroma_db/, uploads/}（宿主机持久化）
```

容器内 `/app` 对应镜像内容；`data/`（SQLite + LangGraph checkpoint）、`chroma_db/`（向量库）、`uploads/`（上传文件）都挂载到宿主机，**容器重建不会丢数据**；`.env` 以只读方式挂载到 `/app/.env`，不会被打进镜像。

---

### 1. 准备阿里云 ECS

1. 购买一台 ECS（建议 **2 核 4G 起**，Ubuntu 22.04 / 24.04 或 Alibaba Cloud Linux 3，磁盘 40G+），记录**公网 IP**。
2. 在控制台「安全组」添加入方向规则：
   - `22`：来源填 `0.0.0.0/0`（或用 GitHub Actions 出口 IP 段，见 https://api.github.com/meta 的 `actions` 字段）——部署服务器需要 SSH。
   - `8080`：来源 `0.0.0.0/0`——先用 `http://公网IP:8080` 直接访问（后面接 Nginx 时可关闭）。
3. SSH 登录服务器，安装 Docker（阿里云镜像安装脚本，支持 Ubuntu / CentOS / Alibaba Cloud Linux）：

```bash
curl -fsSL https://get.docker.com | sudo sh -s -- --mirror Aliyun
sudo systemctl enable --now docker
sudo docker version
```

4. 让部署用户能直接调用 docker（用 root 部署可跳过；用 `ubuntu` 等普通用户时执行）：

```bash
sudo usermod -aG docker $USER
```

退出 SSH 重新登录，验证：

```bash
docker run --rm hello-world
```

> 拉取 Docker Hub 镜像慢时，可在「容器镜像服务 ACR 控制台 → 镜像工具 → 镜像加速器」取专属地址，写入 `/etc/docker/daemon.json` 的 `registry-mirrors` 后 `sudo systemctl restart docker`。

---

### 2. 在服务器上创建部署目录和 `.env`

```bash
sudo mkdir -p /opt/zaikou-ai/{data,chroma_db,uploads}
sudo chown -R $USER:$USER /opt/zaikou-ai
sudo nano /opt/zaikou-ai/.env
```

`.env` 内容（把示例值换成你自己的 Key；该文件**只存在服务器**，不要提交到 Git）：

```dotenv
OPENAI_API_KEY=sk-你的Key
OPENAI_BASE_URL=https://api.openai-proxy.org/v1
OPENAI_MODEL=gpt-4o-mini
TAVILY_API_KEY=tvly-你的Key

LANGSMITH_TRACING=true
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_API_KEY=lsv2_pt_你的Key
LANGSMITH_PROJECT=zaikouAI
```

```bash
sudo chmod 600 /opt/zaikou-ai/.env
```

说明：`cicd.yaml` 里 `docker run` 会把该文件只读挂载到容器 `/app/.env`，由 `app.py` 中的 `python-dotenv` 读取。**修改 `.env` 后执行 `docker restart zaikou-ai` 即可生效**，无需重新构建镜像。

---

### 3. 开通阿里云容器镜像服务 ACR

1. 控制台搜索「容器镜像服务」，开通**个人版**（免费）。
2. 「命名空间」→ 创建命名空间，例如 `zaikou-ai`（全小写）。
3. 「镜像仓库」→ 创建镜像仓库：命名空间 `zaikou-ai`，仓库名 `zaikou-ai`，可见性选**私有**。
4. 「访问凭证」→ 设置**固定密码**，页面同时会显示登录用户名——这两项就是后面的 `ALIYUN_ACR_USERNAME` / `ALIYUN_ACR_PASSWORD`（**不是**阿里云账号登录密码）。
5. 在仓库详情页确认「公网地址」，形如：

```
registry.cn-hangzhou.aliyuncs.com/zaikou-ai/zaikou-ai
└──────── ALIYUN_REGISTRY ────────┘ └─ 命名空间 ─┘ └ 仓库名 ┘
```

> 建议 ACR 地域与 ECS 保持一致（杭州 / 北京 / 深圳 / 上海等），拉取更快。

---

### 4. 配置 GitHub Secrets

仓库 → `Settings` → `Secrets and variables` → `Actions` → `New repository secret`，逐个添加：

| Secret | 必填 | 示例 | 说明 |
| --- | --- | --- | --- |
| `ALIYUN_REGISTRY` | 是 | `registry.cn-hangzhou.aliyuncs.com` | ACR 公网地址的域名部分 |
| `ALIYUN_ACR_NAMESPACE` | 是 | `zaikou-ai` | ACR 命名空间 |
| `ALIYUN_ACR_USERNAME` | 是 | `yourname@example.com` | ACR「访问凭证」页显示的用户名 |
| `ALIYUN_ACR_PASSWORD` | 是 | `********` | ACR 固定密码 |
| `ALIYUN_HOST` | 是 | `47.100.xx.xx` | ECS 公网 IP |
| `ALIYUN_USER` | 是 | `root` 或 `ubuntu` | SSH 登录用户（需能执行 docker） |
| `ALIYUN_SSH_PORT` | 否 | `22` | 不填默认 22 |
| `ALIYUN_SSH_KEY` | 是 | `-----BEGIN OPENSSH PRIVATE KEY----- ...` | 部署私钥全文，含首尾 BEGIN/END 行 |

生成部署密钥（在**本地电脑**执行，Windows PowerShell）：

```powershell
ssh-keygen -t ed25519 -C "github-actions-deploy" -f "$env:USERPROFILE\.ssh\aliyun_deploy"
Get-Content "$env:USERPROFILE\.ssh\aliyun_deploy.pub"
```

macOS / Linux：

```bash
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/aliyun_deploy -N ""
cat ~/.ssh/aliyun_deploy.pub
```

把上一步输出的**公钥**（`ssh-ed25519 ...` 一整行）加到服务器：

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "ssh-ed25519 AAAA...你的公钥" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

（也可以在阿里云控制台「远程连接 / Workbench」里操作。）随后把**私钥**全文填进 `ALIYUN_SSH_KEY`：

```powershell
Get-Content "$env:USERPROFILE\.ssh\aliyun_deploy" -Raw   # Windows
```

```bash
cat ~/.ssh/aliyun_deploy                                   # macOS / Linux
```

本地验证登录：

```bash
ssh -i ~/.ssh/aliyun_deploy root@<ECS公网IP>
```

---

### 5. 触发部署

提交并推送到 `main`（或在 Actions 页面点 `Run workflow` 手动触发）：

```bash
git add .github/workflows/cicd.yaml README.md
git commit -m "ci: add GitHub Actions CI/CD for Aliyun ECS"
git push origin main
```

执行顺序：`CI / lint & import smoke test` → `Build / push image to Aliyun ACR` → `Deploy / Aliyun ECS`。
首次构建需要拉取基础镜像并安装依赖，大约 5–10 分钟；之后有 `type=gha` 缓存会快很多。

---

### 6. 验证部署

```bash
# 浏览器访问
http://<ECS公网IP>:8080

# 服务器上确认容器状态
sudo docker ps
sudo docker logs -f zaikou-ai
```

也可以 `curl -I http://<ECS公网IP>:8080` 确认返回 `200`。部署成功时，Actions 运行详情页的 `Deploy / Aliyun ECS` 步骤会打印容器状态表格；失败时会自动回滚到上一个镜像并打印容器日志。

---

### 7. 日常运维命令

| 操作 | 命令 |
| --- | --- |
| 查看日志 | `sudo docker logs -f --tail 100 zaikou-ai` |
| 重启（改完 `.env` 后） | `sudo docker restart zaikou-ai` |
| 停止 / 启动 | `sudo docker stop zaikou-ai` / `sudo docker start zaikou-ai` |
| 进入容器排查 | `sudo docker exec -it zaikou-ai bash` |
| 查看磁盘占用 | `sudo docker system df` |
| 清理悬空镜像 | `sudo docker image prune -f` |

**手动回滚**（流水线每次部署前都会把当前镜像打上 `zaikou-ai:rollback` 标签）：

```bash
sudo docker rm -f zaikou-ai
sudo docker run -d --name zaikou-ai --restart unless-stopped \
  -p 8080:8080 \
  -v /opt/zaikou-ai/.env:/app/.env:ro \
  -v /opt/zaikou-ai/data:/app/data \
  -v /opt/zaikou-ai/chroma_db:/app/chroma_db \
  -v /opt/zaikou-ai/uploads:/app/uploads \
  --log-opt max-size=10m --log-opt max-file=3 \
  zaikou-ai:rollback
```

**手动发布指定 commit**（镜像标签就是 commit sha）：

```bash
sudo docker pull registry.cn-hangzhou.aliyuncs.com/zaikou-ai/zaikou-ai:<commit-sha>
sudo docker rm -f zaikou-ai
# 再用上面的 docker run 命令，把最后的镜像名换成该 sha 标签
```

---

### 8. 可选：Nginx 反向代理 + HTTPS（推荐）

直接用 `IP:8080` 暴露服务不适合公网长期使用。接入 Nginx 后可用域名 + HTTPS，且 80/443 只暴露反向代理。

```bash
sudo apt-get install -y nginx        # Alibaba Cloud Linux 用 sudo dnf install -y nginx
```

`/etc/nginx/sites-available/zaikou-ai.conf`（RHEL 系放到 `/etc/nginx/conf.d/zaikou-ai.conf`）：

```nginx
server {
    listen 80;
    server_name ai.example.com;

    client_max_body_size 25m;   # 应用单文件上传上限 20MB

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # /chat/stream 是 SSE 流式输出，必须关闭缓冲，否则回复会“卡住一次性吐出”
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/zaikou-ai.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 申请免费证书（域名需已解析到 ECS 公网 IP）
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d ai.example.com
```

启用 Nginx 后，把安全组里 `8080` 的入方向规则删除（只保留 80/443），容器端口仅本机可访问。

> 中国大陆地域的 ECS 绑定域名对外提供 Web 服务，需要域名已完成 **ICP 备案**。

---

### 9. 常见问题

| 现象 | 原因 / 解决 |
| --- | --- |
| `ssh: connect to host ... port 22: Connection timed out` | 安全组未放行 22，或 `ALIYUN_HOST` / `ALIYUN_SSH_PORT` 配错 |
| `unauthorized: authentication required` | ACR 凭证错：必须用「访问凭证」的固定密码，不是阿里云账号密码 |
| `denied: requested access to the resource is denied` | ACR 仓库/命名空间名与 `ALIYUN_ACR_NAMESPACE`、镜像仓库名 `zaikou-ai` 不一致，或仓库未创建 |
| 部署步骤报 `ERROR: /opt/zaikou-ai/.env 不存在或为空` | 没做第 2 步，或部署用户无权读取该文件 |
| `permission denied ... docker.sock` | 部署用户不在 docker 组：`sudo usermod -aG docker $USER` 后重新登录 |
| 健康检查失败，日志出现 `模型初始化失败` | `.env` 缺失 / Key 写错 / `OPENAI_BASE_URL` 不通；修正后 `docker restart zaikou-ai` |
| 容器反复重启 | `sudo docker logs --tail 200 zaikou-ai` 查看异常；常见是内存不足（建议 4G 起） |
| 浏览器打不开 `IP:8080` | 安全组 8080 未放行，或容器未运行（`docker ps`） |
| 上传大文件返回 413 | 走 Nginx 时调大 `client_max_body_size` |
| 修改 `.env` 不生效 | 没有重启容器：`sudo docker restart zaikou-ai` |
| 回复一段一段“卡住后才出现” | Nginx 未设置 `proxy_buffering off` |
| 容器秒退，日志 `exec format error` | 镜像架构与 ECS 不符：ARM 机型（如 g8y）需把 workflow 里 `platforms` 改为 `linux/arm64` |

---

### 10. 安全注意事项

- `.env` 已被 `.gitignore` / `.dockerignore` 忽略：不进 Git、不进镜像，只留在服务器 `/opt/zaikou-ai/.env`（权限 600）。
- 所有云端凭证只放 **GitHub Secrets**，不要写进 workflow 文件；私有 ACR 仓库可避免镜像被公开拉取。
- 建议在服务器 `/etc/ssh/sshd_config` 设置 `PasswordAuthentication no`，只允许密钥登录；离职或换机时删除 `authorized_keys` 中对应公钥并轮换 GitHub Secret。
- 如果 API Key 曾经被提交到公开仓库，请到对应平台**重置**后再更新服务器 `.env`。
