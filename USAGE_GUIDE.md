# Cloud Agent Platform 使用指南

## 方式一：直接运行示例代码（无需 API Key）

如果你只是想快速体验平台功能，可以使用 `LocalSandbox` 模式，不需要 OpenAI API Key：

```bash
cd /workspace/project/cloud-agent-platform
pip install structlog

# 运行内置示例
python examples/usage.py
```

这将演示：
- Sandbox 隔离执行
- 任务调度器
- 可观测性（指标追踪）
- LLM 提供商配置

## 方式二：运行完整 API 服务

### 1. 安装依赖

```bash
cd /workspace/project/cloud-agent-platform

# 创建虚拟环境（推荐）
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或 venv\Scripts\activate  # Windows

# 安装核心依赖
pip install fastapi uvicorn pydantic pydantic-settings httpx structlog

# 可选：Docker 支持
pip install docker

# 安装全部依赖（含开发工具）
pip install -e ".[dev]"
```

### 2. 配置环境变量

创建 `.env` 文件：

```bash
# 必须设置
export OPENAI_API_KEY="sk-your-api-key"

# 可选配置
export LLM_MODEL="gpt-4o"
export LLM_PROVIDER="openai"
export SANDBOX_TYPE="local"  # 或 "docker"
export LOG_LEVEL="INFO"
```

### 3. 启动服务器

```bash
# 开发模式（自动重载）
uvicorn api:app --reload --host 0.0.0.0 --port 8000

# 生产模式
uvicorn api:app --host 0.0.0.0 --port 8000 --workers 4
```

### 4. 访问 Web UI

打开浏览器访问：`http://localhost:8000/ui/index.html`

功能：
- 提交自然语言任务
- 查看实时执行日志
- 监控平台指标

## 方式三：Python API 调用

### 提交任务

```python
import httpx
import asyncio
import json

async def main():
    async with httpx.AsyncClient() as client:
        # 1. 提交任务
        response = await client.post(
            "http://localhost:8000/api/v1/tasks",
            json={
                "description": "Read the current directory and list all Python files",
                "agent_type": "default",
                "priority": 5
            }
        )
        task = response.json()
        task_id = task["id"]
        print(f"Task submitted: {task_id}")

asyncio.run(main())
```

### 实时监控任务

```python
import asyncio
import websockets
import json

async def monitor_task(task_id):
    uri = f"ws://localhost:8000/ws/v1/tasks/{task_id}"
    
    async with websockets.connect(uri) as ws:
        while True:
            message = await ws.recv()
            event = json.loads(message)
            
            event_type = event.get("event_type")
            data = event.get("data", {})
            
            if event_type == "thought":
                print(f"🤔 {data.get('thought', '')[:80]}...")
            elif event_type == "tool_call":
                print(f"🔧 {data.get('tool_name')}()")
            elif event_type == "tool_result":
                status = "✅" if data.get("success") else "❌"
                print(f"{status} Result received")
            elif event_type in ["task_completed", "task_failed"]:
                print(f"{'✅' if event_type == 'task_completed' else '❌'} Task {event_type}")
                break

# 使用
asyncio.run(monitor_task("your-task-id"))
```

### 完整示例：提交任务 + 监控

```python
import httpx
import asyncio
import websockets
import json

async def run_agent_task(description: str):
    # 1. 提交任务
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/api/v1/tasks",
            json={
                "description": description,
                "agent_type": "coder",
                "priority": 5
            }
        )
        task = response.json()
        task_id = task["id"]
    
    print(f"📋 Task ID: {task_id}")
    
    # 2. 实时监控
    uri = f"ws://localhost:8000/ws/v1/tasks/{task_id}"
    async with websockets.connect(uri) as ws:
        while True:
            message = await ws.recv()
            event = json.loads(message)
            
            event_type = event.get("event_type")
            data = event.get("data", {})
            
            if event_type == "thought":
                print(f"\n🤔 Thinking: {data.get('thought', '')[:100]}...")
            elif event_type == "tool_call":
                print(f"\n🔧 Calling: {data.get('tool_name')}")
            elif event_type == "tool_result":
                result = data.get('result', {})
                if isinstance(result, dict):
                    print(f"📄 Output: {json.dumps(result)[:200]}...")
            elif event_type == "task_completed":
                print(f"\n✅ Task completed!")
                print(f"Result: {data.get('result')}")
                return task_id
            elif event_type == "task_failed":
                print(f"\n❌ Task failed: {data.get('error')}")
                return None

# 使用
asyncio.run(run_agent_task(
    "Create a Python file called greeting.py that contains a function greet() "
    "that returns 'Hello, World!'. Then run the file to verify it works."
))
```

## 方式四：直接使用 Agent SDK

如果你只想在代码中使用 Agent 而不需要 HTTP 服务：

```python
import asyncio
import os
from agent import AgentEngine, AgentConfig, AgentType
from agent.tools import get_default_tools, ToolContext
from llm import LLMFactory, Message
from sandbox import LocalSandbox

async def main():
    # 1. 创建 LLM
    llm = LLMFactory.create(
        provider="openai",
        api_key=os.getenv("OPENAI_API_KEY"),
        default_model="gpt-4o"
    )
    
    # 2. 创建沙箱
    sandbox = LocalSandbox()
    await sandbox.start()
    
    # 3. 创建 Agent
    config = AgentConfig(
        agent_type=AgentType.CODER,
        max_iterations=20,
        tools=["terminal", "file_editor"]
    )
    
    engine = AgentEngine(
        llm=llm,
        tools=get_default_tools(),
        config=config
    )
    
    # 4. 添加事件回调
    async def on_event(event):
        print(f"[{event['type']}]")
    
    engine.add_callback(on_event)
    
    # 5. 运行任务
    from models import Task
    task = Task(description="Create hello.py and run it")
    
    context = ToolContext(
        workspace_id="my-workspace",
        task_id=task.id,
        working_directory=sandbox.working_directory,
        sandbox=sandbox
    )
    
    state = await engine.run(task, context)
    
    print(f"✅ Completed in {state.iteration} iterations")
    print(f"Result: {state.final_result}")
    
    await sandbox.stop()

asyncio.run(main())
```

## API 端点参考

| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/v1/tasks` | POST | 提交新任务 |
| `/api/v1/tasks/{id}` | GET | 获取任务状态 |
| `/api/v1/tasks/{id}` | DELETE | 取消任务 |
| `/api/v1/tasks` | GET | 列出所有任务 |
| `/ws/v1/tasks/{id}` | WS | 实时事件流 |
| `/api/v1/agents` | GET | 列出 Agent 类型 |
| `/api/v1/metrics` | GET | 平台指标 |
| `/health` | GET | 健康检查 |

## 常见问题

### Q: Docker 沙箱无法启动？

确保：
1. Docker 已安装并运行
2. 有权限访问 Docker socket
3. 拉取镜像：`docker pull ghcr.io/openhands/agent-server:latest-python`

### Q: LLM 调用失败？

检查：
1. API Key 是否正确设置
2. 网络是否可达
3. 账户是否有足够配额

### Q: 任务卡住不动？

可能是：
1. LLM 响应超时
2. 工具执行卡死
3. 达到最大迭代次数

可以用 WebSocket 监控详细日志进行调试。

## 下一步

- 查看 `ARCHITECTURE.md` 了解系统架构
- 参考 `examples/usage.py` 了解各种使用场景
- 根据需要修改各个模块
