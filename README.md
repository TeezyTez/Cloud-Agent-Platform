# Cloud Agent Platform

A production-ready cloud platform for running autonomous AI agents in isolated sandbox environments. The platform enables users to submit natural language tasks that are executed by intelligent agents powered by LLMs.

## Features

- **🤖 Autonomous Agents**: AI agents that can understand tasks, plan approaches, and execute actions
- **🔒 Isolated Execution**: Secure sandbox environments (Docker/Kubernetes) with resource limits
- **🛠️ Tool System**: Rich toolset including terminal, file editor, web search, and task tracking
- **📊 Real-time Streaming**: WebSocket-based event streaming for live task progress
- **📈 Observability**: Comprehensive metrics, logging, and distributed tracing
- **🔐 Security**: Defense-in-depth security with action validation and rate limiting
- **⚡ Scalable**: Horizontal scaling with Redis-based task queue

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                           CLIENT LAYER                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐  │
│  │   Web UI    │  │   REST API  │  │  WebSocket  │  │    SDK    │  │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └─────┬──────┘  │
└─────────┼────────────────┼────────────────┼──────────────┼────────┘
          │                │                │              │
┌─────────┴────────────────┴────────┬───────┴──────────────┴────────┐
│                           API GATEWAY                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────┐ │
│  │Task Router   │  │Auth/AuthZ    │  │Rate Limiter │  │WebSocket │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────┘│
└─────────────────────────────────┬───────────────────────────────┬─┘
                                  │                               │
┌─────────────────────────────────┴───────────────────────────────┴─┐
│                        ORCHESTRATION LAYER                          │
│  ┌────────────────┐  ┌────────────────┐  ┌───────────────────────┐  │
│  │Task Scheduler  │  │Agent Registry  │  │  Event Bus (Redis)    │  │
│  │(Priority Q)     │  │               │  │                       │  │
│  └────────┬───────┘  └───────┬────────┘  └──────────┬────────────┘  │
└───────────┼───────────────────┼──────────────────────┼───────────────┘
            │                   │                      │
┌───────────┼───────────────────┼──────────────────────┼───────────────┐
│           │   AGENT EXECUTION LAYER                 │               │
│  ┌────────┴────────┐  ┌───────┴────────┐  ┌────────┴──────────────┐ │
│  │Agent Engine     │  │Tool System     │  │LLM Integration Layer  │ │
│  │(Reasoning Loop) │  │               │  │                       │ │
│  └────────┬────────┘  └───────┬────────┘  └────────┬─────────────┘ │
└───────────┼────────────────────┼────────────────────┼──────────────┘
            │                    │                    │
┌───────────┼────────────────────┼────────────────────┼──────────────┐
│           │   SANDBOX EXECUTION LAYER                │              │
│  ┌────────┴────────┐  ┌───────┴────────┐  ┌────────┴─────────────┐│
│  │Docker Manager   │  │K8s Manager     │  │Workspace Manager     ││
│  │                 │  │               │  │(File/Process isol)   ││
│  └─────────────────┘  └────────────────┘  └──────────────────────┘│
└───────────────────────────────────────────────────────────────────┘
```

## Quick Start

### Prerequisites

- Python 3.10+
- Docker (for sandbox isolation)
- Redis (for task queue, optional)

### Installation

```bash
# Clone the repository
cd cloud-agent-platform

# Install dependencies
pip install -e .

# Set environment variables
export OPENAI_API_KEY="your-api-key"
export LLM_MODEL="gpt-4o"

# Run the server
python -m api
```

### Using Docker

```bash
# Build the image
docker build -t cloud-agent-platform .

# Run with Docker
docker run -p 8000:8000 \
  -e OPENAI_API_KEY="your-api-key" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  cloud-agent-platform
```

## API Usage

### Submit a Task

```bash
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Read the current directory and list all files",
    "agent_type": "default",
    "priority": 5
  }'
```

### Get Task Status

```bash
curl http://localhost:8000/api/v1/tasks/{task_id}
```

### Stream Task Events (WebSocket)

```bash
# Connect to WebSocket for real-time events
wscat -c ws://localhost:8000/ws/v1/tasks/{task_id}
```

### Python SDK Example

```python
import httpx
import asyncio
from websockets import connect

async def run_task():
    # Submit task
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/api/v1/tasks",
            json={
                "description": "Create a Python hello world script",
                "agent_type": "coder",
            }
        )
        task = response.json()
        task_id = task["id"]
    
    # Stream events
    async with connect(f"ws://localhost:8000/ws/v1/tasks/{task_id}") as ws:
        async for message in ws:
            event = json.loads(message)
            print(f"[{event['event_type']}] {event}")
            
            if event['event_type'] in ['task_completed', 'task_failed']:
                break

asyncio.run(run_task())
```

## Agent Types

| Type | Description | Best For |
|------|-------------|----------|
| `default` | General purpose assistant | Various tasks |
| `coder` | Programming specialist | Code generation, debugging |
| `reviewer` | Code review expert | PR reviews, code analysis |
| `researcher` | Information gathering | Research, fact-finding |

## Available Tools

- **Terminal**: Execute shell commands
- **FileEditor**: Read, write, and edit files
- **TaskTracker**: Manage subtasks
- **WebSearch**: Search the web

## Security

The platform implements multiple security layers:

1. **Sandbox Isolation**: Agents run in isolated Docker containers
2. **Action Validation**: Commands are analyzed before execution
3. **Resource Limits**: CPU, memory, and execution time limits
4. **Network Policies**: No external network access by default
5. **Rate Limiting**: API request throttling

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | - | API key for LLM provider |
| `LLM_MODEL` | `gpt-4o` | LLM model to use |
| `SANDBOX_TYPE` | `docker` | Sandbox backend |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `LOG_LEVEL` | `INFO` | Logging level |

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Format code
ruff format .

# Type check
mypy src/
```

## License

MIT License
