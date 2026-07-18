# Cloud Agent Platform Architecture

## Overview

The Cloud Agent Platform is a production-ready system for running autonomous AI agents in isolated cloud environments. It provides secure, scalable execution of natural language tasks through a combination of LLM-powered reasoning and sandboxed tool execution.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLIENT LAYER                                    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐│
│  │  Web UI     │  │  REST API   │  │ WebSocket   │  │  SDK Client         ││
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘│
└─────────┼────────────────┼────────────────┼──────────────────┼───────────┘
          │                │                │                  │
          └────────────────┴────────┬───────┴──────────────────┘
                                    │
┌───────────────────────────────────┴─────────────────────────────────────────┐
│                            API GATEWAY LAYER                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │                        FastAPI Application                               │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐│ │
│  │  │ Task Router  │  │ Auth/AuthZ    │  │ Rate Limiter │  │ WebSocket   ││ │
│  │  │              │  │              │  │              │  │ Manager     ││ │
│  │  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────┘│ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────────┘
                                    │
┌───────────────────────────────────┴─────────────────────────────────────────┐
│                         ORCHESTRATION LAYER                                 │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐  │
│  │ Task Scheduler │  │ Agent Registry  │  │ Event Bus (Redis)           │  │
│  │ (Priority Q)   │  │                 │  │                             │  │
│  └────────┬────────┘  └────────┬────────┘  └──────────────┬──────────────┘  │
└───────────┼────────────────────┼─────────────────────────┼─────────────────┘
            │                    │                         │
┌───────────┼────────────────────┼─────────────────────────┼─────────────────┐
│           │    AGENT EXECUTION LAYER                     │                  │
│  ┌────────┴────────┐  ┌────────┴────────┐  ┌────────────┴────────────────┐  │
│  │ Agent Engine    │  │ Tool System     │  │ LLM Integration Layer       │  │
│  │ (Reasoning Loop)│  │                 │  │                             │  │
│  └────────┬────────┘  └────────┬────────┘  └────────────┬────────────────┘  │
└───────────┼────────────────────┼─────────────────────────┼──────────────────┘
            │                    │                         │
┌───────────┼────────────────────┼─────────────────────────┼──────────────────┐
│           │     SANDBOX EXECUTION LAYER                   │                  │
│  ┌────────┴────────┐  ┌────────┴────────┐  ┌────────────┴────────────────┐  │
│  │ Docker Manager  │  │ K8s Manager     │  │ Workspace Manager           │  │
│  │                 │  │                 │  │ (File/Process isolation)    │  │
│  └────────┬────────┘  └────────┬────────┘  └────────────┬────────────────┘  │
└───────────┼────────────────────┼─────────────────────────┼──────────────────┘
            │                    │                         │
┌───────────┼────────────────────┼─────────────────────────┼──────────────────┐
│           │   INFRASTRUCTURE LAYER                        │                  │
│  ┌────────┴────────┐  ┌────────┴────────┐  ┌────────────┴────────────────┐  │
│  │ Docker Daemon   │  │ Kubernetes API  │  │ Persistent Storage           │  │
│  │                 │  │                 │  │ (Redis + SQLite/Postgres)   │  │
│  └─────────────────┘  └─────────────────┘  └───────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. API Gateway Layer

**Responsibilities:**
- HTTP/REST endpoint handling
- WebSocket connection management for real-time events
- Authentication and authorization (JWT-based)
- Rate limiting and quota management
- Request validation and routing

**Key Endpoints:**
```
POST   /api/v1/tasks                    - Submit a new task
GET    /api/v1/tasks/{id}              - Get task status/result
DELETE /api/v1/tasks/{id}              - Cancel a running task
GET    /api/v1/tasks/{id}/events       - Stream task events (WS)
POST   /api/v1/tasks/{id}/interrupt    - Interrupt running task
GET    /api/v1/agents                  - List available agent types
WS     /ws/v1/conversations/{id}       - Real-time conversation stream
```

### 2. Orchestration Layer

**Task Scheduler:**
- Priority-based task queue using Redis
- Supports task dependencies and chaining
- Automatic retry with exponential backoff
- Task timeout and deadline management

**Agent Registry:**
- Manages available agent configurations
- Supports multiple agent types with different capabilities
- Version management for agent prompts and tools

**Event Bus:**
- Redis-based pub/sub for inter-component communication
- Real-time event streaming to connected clients
- Event history persistence for debugging

### 3. Agent Execution Layer

**Agent Engine (Reasoning Loop):**
```
┌─────────────────────────────────────────────────────────────────┐
│                      REASONING LOOP                              │
│                                                                  │
│  ┌─────────┐    ┌──────────────┐    ┌─────────────────────────┐ │
│  │ Observe │ -> │   Reason     │ -> │        Act              │ │
│  │ State   │    │ (LLM Call)   │    │ (Tool Execution)        │ │
│  └────┬────┘    └──────┬───────┘    └───────────┬─────────────┘ │
│       │               │                          │               │
│       │    ┌──────────┴──────────┐               │               │
│       │    │                     │               │               │
│       └────│   MAX ITERATIONS?   │───────────────┘               │
│            │   TIMEOUT?           │                               │
│            │   GOAL COMPLETE?     │                               │
│            └─────────────────────┘                               │
└─────────────────────────────────────────────────────────────────┘
```

**Tool System:**
- Base tools: Terminal, FileEditor, TaskTracker, WebBrowser
- Dynamic tool registration and discovery
- Tool result caching for idempotent operations
- Sandboxed execution with resource limits

**LLM Integration:**
- Multi-provider support (OpenAI, Anthropic, local models)
- Automatic fallback and retry logic
- Streaming response handling
- Token usage tracking and cost estimation

### 4. Sandbox Execution Layer

**Docker Sandbox:**
- Isolated container per agent execution
- Configurable resource limits (CPU, memory, disk)
- Network isolation (no external access by default)
- Ephemeral filesystem with optional persistence

**Kubernetes Sandbox (Production):**
- Pod-based isolation for production workloads
- Horizontal pod autoscaling
- Persistent workspaces for long-running tasks
- GPU support for ML workloads

**Workspace Manager:**
- Secure file upload/download
- Process execution with timeout
- Workspace snapshot and restore
- Collaborative workspace sharing

## Data Flow

### Task Submission Flow
```
1. Client submits task via REST API
2. API Gateway validates and authenticates request
3. Task is queued in Redis with priority
4. Scheduler picks up task based on priority
5. Sandbox is provisioned (Docker/K8s)
6. Agent Engine starts in sandbox
7. Agent reasoning loop executes
8. Events streamed to client via WebSocket
9. Task completes or is interrupted
10. Sandbox is cleaned up
```

### Event Types
```
TaskStarted      - Task execution has begun
Thought          - Agent reasoning/thinking
ToolCall         - Tool is being executed
ToolResult       - Tool execution completed
Error            - An error occurred
TaskCompleted    - Task finished successfully
TaskCancelled    - Task was cancelled
```

## Security

### Isolation
- Each agent runs in an isolated container/namespace
- No shared filesystem between agents
- Network policies prevent lateral movement
- Resource quotas prevent denial of service

### Action Validation
- Security analyzer reviews all actions before execution
- Blocklist of dangerous commands
- Confirmation mode for high-risk actions
- Audit logging of all operations

### Authentication
- JWT-based authentication
- API key support for programmatic access
- OAuth 2.0 integration for enterprise

## Scalability

### Horizontal Scaling
- API Gateway: Stateless, can scale behind load balancer
- Scheduler: Multiple instances with Redis coordination
- Agent Workers: Auto-scaling based on queue depth

### Performance Optimizations
- Warm pool of pre-initialized sandboxes
- Conversation history compression
- Parallel tool execution
- Result caching

## Observability

### Logging
- Structured logging with correlation IDs
- Log aggregation ready (ELK, Datadog)
- Per-task log isolation

### Metrics
- Task latency and success rates
- LLM token usage and costs
- Resource utilization
- Queue depth and wait times

### Tracing
- OpenTelemetry integration
- End-to-end request tracing
- Agent reasoning trace capture
