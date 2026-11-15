***

## Harness智能体架构通用服务

Time: 2026/11/5\
Author: GuiQiHueas
------------------

## 如何使用

### 一、关于Harness FastAPI后端服务的启动方式以及相关配置

均在项目根目录下执行如下命令

1. 创建Python虚拟环境

```bash
python -m venv .venv
```

2. 激活虚拟环境

```bash
.venv\Source\activate
```

3. 安装依赖

```bash
pip install -r requirements.txt
```

4. 启动服务

```bash
python main.py 
```

启动后，控制台应能看到：

### 二、关于内置前端UI的启动方式

本项目拥有两种前端UI模式，可以根据自己喜欢的模式启动。

1. 基于Web Server的前端项目。(VUE)
2. 基于Desktop App的前端项目.(VUE + Electron)

他们的启动方式完全一致

```bash
npm install
npm run dev
```

如缺少相关依赖，使用npm install等命令安装依赖



值得一提的

:

- 基于Web Server的前端项目，需要在当前项目目录  /frontend/Desktop/HarnessDesktop  下执行
- 基于Desktop App的前端项目，需要在当前项目目录  /frontend/HarnessControlPlatform  下执行

### 三、关于 PostgreSQL 数据库的启动方式



在docker中启动PostgreSQL数据库

\
Image:

```docker
 docker pull postgres:16
```

Container:

```docker
 docker run -d --name harness-postgres -e POSTGRES_USER=your_user_name -e POSTGRES_PASSWORD=123456 -e POSTGRES_DB=your_db_name -p 5432:5432 -v harness_pgdata:/var/lib/postgresql/data postgres:16
```

该数据库将会存储绝大多数的数据：

### 四、关于 本地及其的沙箱服务的启动方式

应该有很多种方法，这里介绍在Linux下的部署方式

首先确保你的linux也支持docker。

你需要在   /opt   目录下创建一个子目录（不是强制，你可以在任何地方新建）opensandbox作为你的沙箱服务目录。

整体目录结构如下

```text
/opt/opensandbox/
├── sandbox/
    |——Dockerfile
    |——setup-runtime-runtime.sh
|——sandbox.toml
```

[Dockerfile](./sandbox/Dockerfile)文件与[setup-runtime.sh](./sandbox/setup-runtime.sh)文件在本项目目录已提供。

将他们全部拷贝到Linux中

关于toml文件，不需要你手动编写配置。你可以直接拷贝本项目提供的[sandbox.toml](./sandbox/sandbox.toml)文件。

- 也可以通过命令自动生成

1. 先创建镜像

```bash
docker build -t myagent-sandbox:1 sandbox
```

> 该命令将读取sandbox子目录下的Dockerfile，该过程可能需要很久，耐心等待或者使用国内镜像源

测试镜像创建成功

```bash
docker run --rm myagent-sandbox:1 python -c "import yaml; print('sandbox ok')"
```

2. 安装uv，如果你的宿主机有uv，可以略过这一步

```bash
# 示例：安装到 /usr/local/bin 目录
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="/usr/local/bin" sh
# 然后安装opensandbox-server
# 1. 装服务端，必须锁 0.1.14 —— 客户端是 opensandbox 0.1.16
uv tool install 'opensandbox-server==0.1.14'
# 已装过的话先看一眼： opensandbox-server --version
```

3. 生成toml配置文件（可选）\
   回到 /opt/opensandbox/ 目录下执行

```bash
# 可选，也可以直接把该项目的./sandbox/sandbox.toml文件复制到Linux中
uvx opensandbox-server init-config ~/.sandbox.toml --example docker
```

然后就拥有了一个默认的toml配置文件。

4. 配置你的toml文件。一般来说，只需要配置如下参数即可

```text
[server]
host = "0.0.0.0"
port = 18083   <---  这里要和你的.env中一致
max_sandbox_timeout_seconds = 86400

# Optional: Uncomment to enable API key authentication
api_key = "sk-harness-local-7c1a9e4b2d8f6a35"  <---  默认是注释掉的。这里要和你的.env中一致。这样才可以连接到linux的沙箱服务。
# If api_key stays empty, startup requires explicit acknowledgment:
# - Interactive TTY: type YES when prompted
# - Non-interactive: set OPENSANDBOX_INSECURE_SERVER=YES
```

5. 启动opensandbox服务\
   回到 /opt/opensandbox/ 目录下执行启动

```bash
# 清除BOM头
sed -i '1s/^\xEF\xBB\xBF//' /opt/opensandbox/sandbox.toml
# 启动
opensandbox-server --config /opt/opensandbox/sandbox.toml
```

6. 启动后，控制台应能看到：

```text

INFO:     2026-11-05 17:10:21+0800 [-] opensandbox_server.config: Loaded configuration from /opt/opensandbox/sandbox.toml
INFO:     2026-11-05 17:10:21+0800 [-] opensandbox_server.services.factory: Creating sandbox service with type: docker
INFO:     2026-11-05 17:10:21+0800 [-] opensandbox_server.services.docker.docker_service: Docker service initialized from environment
INFO:     2026-11-05 17:10:21+0800 [-] opensandbox_server.services.docker.docker_service: Sandbox 29273975-8781-40f6-a561-3ac9698e7ffc already expired; terminating now.
INFO:     2026-11-05 17:10:21+0800 [-] opensandbox_server.services.docker.docker_service: Sandbox 0430cce0-b8cc-430b-a698-b243f652c408 already expired; terminating now.
INFO:     2026-11-05 17:10:21+0800 [-] opensandbox_server.services.docker.docker_service: Sandbox 9eda4332-aa1c-4691-beda-566c87d2b914 already expired; terminating now.
INFO:     2026-11-05 17:10:21+0800 [-] uvicorn.error: Started server process [2074]
INFO:     2026-11-05 17:10:21+0800 [-] uvicorn.error: Waiting for application startup.
INFO:     2026-11-05 17:10:22+0800 [-] opensandbox_server.main: Validating secure runtime for Docker backend
INFO:     2026-11-05 17:10:22+0800 [-] opensandbox_server.services.runtime_resolver: Secure runtime is not configured.
INFO:     2026-11-05 17:10:22+0800 [-] uvicorn.error: Application startup complete.
INFO:     2026-11-05 17:10:22+0800 [-] uvicorn.error: Uvicorn running on http://0.0.0.0:18083 (Press CTRL+C to quit)


```

如果遇到构建失败的情况，建议同步时间

```bash
timedatectl set-timezone Asia/Shanghai

yum install -y chrony

systemctl restart chronyd

chronyc -a makestep

date
```

***

## 项目介绍

本项目涵盖多种基础通用的Agent客户端的功能

### 一、基础对话与项目

### 二、SKILL模块

### 三、MCP模块

### 四、轨迹

是AI与你对话和执行任务期间产生的日志与记录。你可以查看AI的思考过程，以及执行任务的详细步骤。
也可以看到AI的执行结果，每一步的参数等信息。

本系统的Agent自我优化与迭代能力大部分也基于轨迹的充分发掘来实现。\


详见  [文档-Agent自我迭代优化机制]()



### 五、我的任务流

定义任务执行流程，Agent 命中后按统一方法执行；支持内置流只读复制、可视化编辑、发布与执行回放。

### 六、模型配置

该模块提供一些模型配置卡片信息，便于你快速LLM模型API KYE 与 厂商服务URL等参数。

> 是静态前端页面提供，后端没有提供这些模型配置信息。

### 七、环境变量介绍

***

## 关于演示用户

系统提供了4个用户id，你可以在
[用户id预设代码配置](./frontend/HarnessControlPlatform/api/identity.ts#L10-L17)
新增你的用户id，主要是为了验证多用户隔离的功能。
