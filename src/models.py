"""Core data models for the Cloud Agent Platform."""
from pydantic import BaseModel, Field
from typing import Optional, Any, List, Dict
from datetime import datetime
from enum import Enum
import uuid


class TaskStatus(str, Enum):
    """Task execution status."""
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class AgentType(str, Enum):
    """Available agent types."""
    DEFAULT = "default"
    CODER = "coder"
    REVIEWER = "reviewer"
    RESEARCHER = "researcher"


class TaskPriority(int, Enum):
    """Task priority levels (higher = more urgent)."""
    LOW = 1
    NORMAL = 5
    HIGH = 10
    CRITICAL = 20


class Task(BaseModel):
    """Represents an agent task."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    description: str = Field(..., description="Natural language task description")
    agent_type: AgentType = AgentType.DEFAULT
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    user_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
        }


class TaskCreate(BaseModel):
    """Request model for creating a task."""
    description: str = Field(..., description="Natural language task description")
    agent_type: AgentType = AgentType.DEFAULT
    priority: TaskPriority = TaskPriority.NORMAL
    metadata: Optional[Dict[str, Any]] = None
    

class TaskUpdate(BaseModel):
    """Request model for updating a task."""
    status: Optional[TaskStatus] = None
    metadata: Optional[Dict[str, Any]] = None


class TaskEvent(BaseModel):
    """Event emitted during task execution."""
    task_id: str
    event_type: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    data: Dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
        }


class EventType(str, Enum):
    """Task event types."""
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"
    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    AGENT_MESSAGE = "agent_message"
    ERROR = "error"
    HEARTBEAT = "heartbeat"


class ToolCall(BaseModel):
    """Represents a tool invocation."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    result: Optional[Any] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[float] = None


class ConversationMessage(BaseModel):
    """A message in the agent conversation."""
    role: str  # "user", "assistant", "system", "tool"
    content: str
    tool_calls: Optional[List[ToolCall]] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AgentConfig(BaseModel):
    """Configuration for an agent instance."""
    agent_type: AgentType
    name: str
    description: str
    system_prompt: str
    tools: List[str] = Field(default_factory=list)
    max_iterations: int = 100
    timeout_seconds: int = 3600
    llm_model: Optional[str] = None


class SandboxConfig(BaseModel):
    """Configuration for sandbox environment."""
    sandbox_type: str = "docker"
    image: Optional[str] = None
    cpu_limit: str = "2"
    memory_limit: str = "4Gi"
    disk_limit: str = "10Gi"
    timeout_seconds: int = 3600
    environment: Dict[str, str] = Field(default_factory=dict)
    working_directory: Optional[str] = None


class WorkspaceInfo(BaseModel):
    """Information about an agent's workspace."""
    workspace_id: str
    sandbox_id: str
    sandbox_type: str
    host_port: Optional[int] = None
    working_directory: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None


class LLMMetrics(BaseModel):
    """LLM usage and cost metrics."""
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_cost: float = 0.0
    requests_count: int = 0
    errors_count: int = 0


class TaskMetrics(BaseModel):
    """Task execution metrics."""
    task_id: str
    llm_metrics: LLMMetrics = Field(default_factory=LLMMetrics)
    tool_calls_count: int = 0
    iterations_count: int = 0
    wall_time_seconds: float = 0.0
    cpu_time_seconds: float = 0.0
