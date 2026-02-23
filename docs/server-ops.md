# 服务器运维文档 — 101.35.177.232

> 最后更新：2026-02-23
> 面向下一位运维 AI agent 的完整参考

---

## 1. 基础信息

| 项目    | 值                                |
| ------- | --------------------------------- |
| 公网 IP | `101.35.177.232`                  |
| 内网 IP | `10.0.0.5`                        |
| OS      | TencentOS Server 3.1 (RHEL 系)    |
| CPU     | 4 核                              |
| 内存    | 7.5 GB                            |
| 磁盘    | 99 GB (已用 26 GB / 28%)          |
| SSH     | `ssh root@101.35.177.232`         |
| 面板    | 1Panel (https://panel.exsaas.com) |

---

## 2. 服务全景

### 2.1 Docker 容器

| 容器名                | 镜像                                | 端口映射             | 用途             |
| --------------------- | ----------------------------------- | -------------------- | ---------------- |
| 1Panel-openresty-4mqV | 1panel/openresty:1.21.4.3-3-3-focal | host 网络模式        | Nginx 反向代理   |
| 1Panel-n8n-nIkK       | n8nio/n8n:latest                    | 0.0.0.0:5678→5678    | n8n 自动化       |
| 1Panel-openclaw-vS7v  | 1panel/openclaw:2026.2.19           | 0.0.0.0:18789-18790  | OpenClaw 平台    |
| 1Panel-mariadb-Mzae   | mariadb:latest                      | 0.0.0.0:25245→3306   | MariaDB 数据库   |
| 1Panel-redis-k8Kc     | redis:latest                        | 0.0.0.0:6379→6379    | Redis 缓存       |
| qdrant                | qdrant/qdrant:latest                | 0.0.0.0:6333-6334    | Qdrant 向量库    |
| emqx                  | emqx/emqx:latest                    | 1883/8083/8883/18083 | EMQX MQTT Broker |

### 2.2 Systemd 服务

| 服务名              | 描述                 | 端口           | 配置文件                                |
| ------------------- | -------------------- | -------------- | --------------------------------------- |
| `pdf2skill.service` | pdf2skill API Server | 127.0.0.1:8900 | `/etc/systemd/system/pdf2skill.service` |
| `v2ray.service`     | V2Ray 出境代理       | 见下文         | `/usr/local/etc/v2ray/config.json`      |
| `xray.service`      | Xray Trojan 入境     | 见下文         | `/usr/local/etc/xray/config.json`       |

---

## 3. pdf2skill 服务

### 3.1 部署位置

```
/opt/pdf2skill/
├── .env              # 环境变量（DeepSeek API Key 等）
├── src/              # Python 后端
├── frontend/         # React 前端
├── static/dist/      # 前端构建产物
├── workflows/        # 工作流数据
└── prompts/          # Prompt 模板
```

### 3.2 Systemd 配置

```ini
# /etc/systemd/system/pdf2skill.service
[Unit]
Description=pdf2skill API Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/pdf2skill
ExecStart=/usr/bin/python3 -m uvicorn src.web_ui:app --host 127.0.0.1 --port 8900
Restart=always
RestartSec=5
EnvironmentFile=/opt/pdf2skill/.env

[Install]
WantedBy=multi-user.target
```

### 3.3 常用命令

```bash
# 查看状态
systemctl status pdf2skill

# 重启
systemctl restart pdf2skill

# 查看日志
journalctl -u pdf2skill -f

# 更新代码（GitHub 需走代理）
cd /opt/pdf2skill
export http_proxy=http://127.0.0.1:10809 https_proxy=http://127.0.0.1:10809
git pull
cd frontend && npm install && npx vite build && cd ..
systemctl restart pdf2skill
```

---

## 4. Nginx 反向代理（1Panel OpenResty）

### 4.1 架构

- OpenResty 运行在 Docker 容器 `1Panel-openresty-4mqV`，**`--network host`** 模式
- 配置文件位于宿主机 `/opt/1panel/apps/openresty/openresty/conf/conf.d/`，挂载到容器 `/usr/local/openresty/nginx/conf/conf.d/`
- 静态文件根目录：宿主机 `/opt/1panel/apps/openresty/openresty/root` → 容器 `/usr/share/nginx/html`
- 站点日志目录：宿主机 `/opt/1panel/apps/openresty/openresty/www` → 容器 `/www`

### 4.2 站点列表

| 域名              | 配置文件               | 代理目标       |
| ----------------- | ---------------------- | -------------- |
| rag.exsaas.com    | rag.exsaas.com.conf    | 127.0.0.1:8900 |
| panel.exsaas.com  | panel.exsaas.com.conf  | 1Panel 面板    |
| n8n.yaodriver.com | n8n.yaodriver.com.conf | 127.0.0.1:5678 |
| www.yaodriver.com | www.yaodriver.com.conf | 对应服务       |

### 4.3 rag.exsaas.com 完整配置

```nginx
# /opt/1panel/apps/openresty/openresty/conf/conf.d/rag.exsaas.com.conf

# HTTP → HTTPS 重定向
server {
    listen 80;
    server_name rag.exsaas.com;

    location ^~ /.well-known/acme-challenge {
        allow all;
        root /usr/share/nginx/html;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

# HTTPS 主站
server {
    listen 443 ssl http2;
    server_name rag.exsaas.com;

    ssl_certificate     /usr/local/openresty/nginx/conf/conf.d/ssl/rag.exsaas.com/fullchain.pem;
    ssl_certificate_key /usr/local/openresty/nginx/conf/conf.d/ssl/rag.exsaas.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    access_log /www/sites/rag.exsaas.com/log/access.log;
    error_log /www/sites/rag.exsaas.com/log/error.log;

    client_max_body_size 100m;

    location / {
        proxy_pass http://127.0.0.1:8900;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $http_connection;
        proxy_read_timeout 300s;
        proxy_buffering off;
    }
}
```

### 4.4 Nginx 操作命令

```bash
# 测试配置
docker exec 1Panel-openresty-4mqV nginx -t

# 重载配置（不停服务）
docker exec 1Panel-openresty-4mqV nginx -s reload

# 查看 access log
tail -f /opt/1panel/apps/openresty/openresty/www/sites/rag.exsaas.com/log/access.log
```

> **⚠ 注意**：不要用系统的 `nginx` 命令（PID 文件不匹配），必须用 `docker exec` 操作 OpenResty 容器。

---

## 5. SSL 证书

### 5.1 证书清单

| 域名             | 颁发机构      | 到期时间   | 证书路径                                  |
| ---------------- | ------------- | ---------- | ----------------------------------------- |
| rag.exsaas.com   | Let's Encrypt | 2026-05-24 | `/etc/letsencrypt/live/rag.exsaas.com/`   |
| panel.exsaas.com | Let's Encrypt | 2026-05-23 | `/etc/letsencrypt/live/panel.exsaas.com/` |

### 5.2 自动续期

```
# crontab
0 3 * * * /usr/local/bin/certbot-renew.sh
```

Certbot 续期后自动执行 deploy hook，将证书拷贝到 Docker 挂载路径并 reload OpenResty：

```bash
# /etc/letsencrypt/renewal-hooks/deploy/copy-to-openresty.sh
#!/bin/bash
DOMAIN=rag.exsaas.com
DEST=/opt/1panel/apps/openresty/openresty/conf/conf.d/ssl/$DOMAIN
mkdir -p $DEST
cp /etc/letsencrypt/live/$DOMAIN/fullchain.pem $DEST/fullchain.pem
cp /etc/letsencrypt/live/$DOMAIN/privkey.pem $DEST/privkey.pem
docker exec 1Panel-openresty-4mqV nginx -s reload
```

### 5.3 手动续期

```bash
certbot renew --dry-run    # 测试
certbot renew              # 正式续期
```

### 5.4 新域名申请证书

```bash
# 1. 写 Nginx HTTP 配置（包含 .well-known/acme-challenge）
# 2. docker exec 1Panel-openresty-4mqV nginx -s reload
# 3. 申请证书
certbot certonly --webroot -w /opt/1panel/apps/openresty/openresty/root \
  -d NEW_DOMAIN.com --non-interactive --agree-tos --email dayuer@gmail.com
# 4. 拷贝证书到 Docker 挂载路径
mkdir -p /opt/1panel/apps/openresty/openresty/conf/conf.d/ssl/NEW_DOMAIN.com
cp /etc/letsencrypt/live/NEW_DOMAIN.com/fullchain.pem /opt/1panel/apps/openresty/openresty/conf/conf.d/ssl/NEW_DOMAIN.com/
cp /etc/letsencrypt/live/NEW_DOMAIN.com/privkey.pem /opt/1panel/apps/openresty/openresty/conf/conf.d/ssl/NEW_DOMAIN.com/
# 5. 更新 renewal hook（新增域名）
# 6. 切换 Nginx 配置为 HTTPS，reload
```

---

## 6. 代理配置（出境翻墙）

### 6.1 V2Ray（出境客户端）

服务器无法直接访问 GitHub 等境外资源，需通过 V2Ray 代理：

```json
// /usr/local/etc/v2ray/config.json（简化）
{
  "inbounds": [
    {"port": 10808, "protocol": "socks", "listen": "127.0.0.1"},
    {"port": 10809, "protocol": "http",  "listen": "127.0.0.1"}
  ],
  "outbounds": [
    {"protocol": "trojan", ...},  // 主通道（翻墙）
    {"protocol": "freedom"},       // 直连
    {"protocol": "blackhole"}      // 拦截
  ]
}
```

**使用方式：**

```bash
# 临时使用
export http_proxy=http://127.0.0.1:10809
export https_proxy=http://127.0.0.1:10809
git clone https://github.com/xxx/yyy.git

# Git 全局配置（永久）
git config --global http.proxy http://127.0.0.1:10809
```

### 6.2 Xray（入境 Trojan 服务端）

```
# /usr/local/etc/xray/config.json
# 提供 Trojan 协议入站，供外部客户端连接
```

```bash
systemctl status xray     # 查看状态
systemctl restart xray    # 重启
```

---

## 7. 关键路径速查

| 用途               | 路径                                                           |
| ------------------ | -------------------------------------------------------------- |
| pdf2skill 代码     | `/opt/pdf2skill/`                                              |
| pdf2skill 环境变量 | `/opt/pdf2skill/.env`                                          |
| pdf2skill systemd  | `/etc/systemd/system/pdf2skill.service`                        |
| Nginx 站点配置     | `/opt/1panel/apps/openresty/openresty/conf/conf.d/`            |
| Nginx 主配置       | `/opt/1panel/apps/openresty/openresty/conf/nginx.conf`         |
| SSL 证书 (源)      | `/etc/letsencrypt/live/{域名}/`                                |
| SSL 证书 (Docker)  | `/opt/1panel/apps/openresty/openresty/conf/conf.d/ssl/{域名}/` |
| 续期 Hook          | `/etc/letsencrypt/renewal-hooks/deploy/copy-to-openresty.sh`   |
| V2Ray 配置         | `/usr/local/etc/v2ray/config.json`                             |
| Xray 配置          | `/usr/local/etc/xray/config.json`                              |
| 站点日志           | `/opt/1panel/apps/openresty/openresty/www/sites/{域名}/log/`   |
| 1Panel 数据        | `/opt/1panel/`                                                 |

---

## 8. 端口占用表

| 端口  | 服务            | 绑定地址  |
| ----- | --------------- | --------- |
| 80    | OpenResty HTTP  | 0.0.0.0   |
| 443   | OpenResty HTTPS | 0.0.0.0   |
| 1883  | EMQX MQTT       | 0.0.0.0   |
| 5678  | n8n             | 0.0.0.0   |
| 6333  | Qdrant HTTP     | 0.0.0.0   |
| 6379  | Redis           | 0.0.0.0   |
| 8083  | EMQX WebSocket  | 0.0.0.0   |
| 8883  | EMQX MQTTS      | 0.0.0.0   |
| 8900  | pdf2skill       | 127.0.0.1 |
| 10808 | V2Ray SOCKS     | 127.0.0.1 |
| 10809 | V2Ray HTTP      | 127.0.0.1 |
| 18083 | EMQX Dashboard  | 0.0.0.0   |
| 18789 | OpenClaw        | 0.0.0.0   |
| 25245 | MariaDB         | 0.0.0.0   |

---

## 9. 故障排查 SOP

### 9.1 pdf2skill 502/无响应

```bash
systemctl status pdf2skill                      # 1. 检查进程
journalctl -u pdf2skill -n 50 --no-pager        # 2. 查看日志
curl -s http://127.0.0.1:8900/api/workflows     # 3. 内部直连测试
systemctl restart pdf2skill                      # 4. 重启
```

### 9.2 HTTPS 证书过期

```bash
certbot certificates                             # 1. 查看到期日
certbot renew                                    # 2. 手动续期
# 3. 检查 hook 是否拷贝了新证书
ls -la /opt/1panel/apps/openresty/openresty/conf/conf.d/ssl/rag.exsaas.com/
docker exec 1Panel-openresty-4mqV nginx -s reload  # 4. 重载
```

### 9.3 GitHub 访问不了

```bash
export http_proxy=http://127.0.0.1:10809 https_proxy=http://127.0.0.1:10809
curl -I https://github.com   # 测试代理是否正常
systemctl status v2ray       # 检查 V2Ray 状态
```
