"""API Gateway - REST endpoints and WebSocket support."""
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Dict, Any, Optional, List
from datetime import datetime
import asyncio
import structlog
import json

from src.models import (
    Task, TaskStatus, TaskCreate, TaskUpdate,
    TaskEvent, EventType, AgentType, AgentConfig, LLMMetrics
)
from scheduler import TaskScheduler, EventBus, get_scheduler, TaskQueue
from sandbox import SandboxManager, SandboxInterface, LocalSandbox, DockerSandbox
from agent.engine import AgentEngine, AgentConfig as AgentConfigModel, SubAgentRunner
from agent.tools import ToolRegistry, get_default_tools, ToolContext
from llm import LLMFactory, LLMProvider

logger = structlog.get_logger(__name__)


# Application state
class AppState:
    """Global application state."""
    
    def __init__(self):
        self.scheduler: Optional[TaskScheduler] = None
        self.sandbox_manager: Optional[SandboxManager] = None
        self.tool_registry: Optional[ToolRegistry] = None
        self.llm_provider: Optional[LLMProvider] = None
        self._websocket_connections: Dict[str, List[WebSocket]] = {}
    
    async def initialize(self):
        """Initialize application state."""
        self.scheduler = get_scheduler()
        await self.scheduler.start()
        
        self.sandbox_manager = SandboxManager()
        self.tool_registry = ToolRegistry()
        
        # Register default tools
        for tool in get_default_tools():
            self.tool_registry.register(tool)
        
        # Initialize LLM (will be configured from settings)
        # self.llm_provider = LLMFactory.create(...)
        
        logger.info("Application state initialized")


app_state = AppState()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    
    app = FastAPI(
        title="Cloud Agent Platform",
        description="Autonomous AI agents running in isolated cloud environments",
        version="0.1.0",
    )
    
    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    @app.on_event("startup")
    async def startup():
        await app_state.initialize()
    
    @app.on_event("shutdown")
    async def shutdown():
        if app_state.scheduler:
            await app_state.scheduler.stop()
        if app_state.sandbox_manager:
            await app_state.sandbox_manager.destroy_all()
    
    # Health check
    @app.get("/health")
    async def health_check():
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "version": "0.1.0",
        }
    
    # Task endpoints
    @app.post("/api/v1/tasks", response_model=Task)
    async def create_task(request: TaskCreate) -> Task:
        """
        Submit a new task for execution.
        
        The task will be queued and processed by an available agent.
        """
        # Create task
        task = Task(
            description=request.description,
            agent_type=request.agent_type,
            priority=request.priority,
            metadata=request.metadata or {},
        )
        
        # Define task handler
        async def handle_task(t: Task, event_bus: EventBus):
            await execute_agent_task(t, event_bus, app_state)
        
        # Submit to scheduler
        await app_state.scheduler.submit_task(task, handle_task)
        
        return task
    
    @app.get("/api/v1/tasks/{task_id}")
    async def get_task(task_id: str) -> Task:
        """Get task status and results."""
        task = await app_state.scheduler.get_task_status(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return task
    
    @app.delete("/api/v1/tasks/{task_id}")
    async def cancel_task(task_id: str) -> Dict[str, Any]:
        """Cancel a running or queued task."""
        success = await app_state.scheduler.cancel_task(task_id)
        if not success:
            raise HTTPException(
                status_code=400,
                detail="Task cannot be cancelled (may already be completed or not found)",
            )
        return {"message": "Task cancelled", "task_id": task_id}
    
    @app.get("/api/v1/tasks")
    async def list_tasks(
        status: Optional[TaskStatus] = None,
        limit: int = Query(default=100, le=1000),
    ) -> List[Task]:
        """List all tasks, optionally filtered by status."""
        tasks = await app_state.scheduler.queue.list_tasks(status)
        return tasks[:limit]
    
    # WebSocket for real-time events
    @app.websocket("/ws/v1/tasks/{task_id}")
    async def task_websocket(websocket: WebSocket, task_id: str):
        """
        WebSocket endpoint for real-time task event streaming.
        
        Connect to receive live updates about task execution.
        """
        await websocket.accept()
        
        try:
            # Subscribe to task events
            async for event in app_state.scheduler.event_bus.subscribe_task(task_id):
                await websocket.send_json(event.model_dump())
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected", task_id=task_id)
        except Exception as e:
            logger.error("WebSocket error", task_id=task_id, error=str(e))
            await websocket.close(code=1011, reason=str(e))
    
    # Agent endpoints
    @app.get("/api/v1/agents")
    async def list_agents() -> List[AgentConfig]:
        """List available agent types and their configurations."""
        return [
            AgentConfig(
                agent_type=AgentType.DEFAULT,
                name="Default Assistant",
                description="General purpose assistant for various tasks",
                system_prompt="You are a helpful AI assistant.",
                tools=["terminal", "file_editor", "task_tracker", "web_search"],
            ),
            AgentConfig(
                agent_type=AgentType.CODER,
                name="Code Assistant",
                description="Specialized for programming and code tasks",
                system_prompt="You are an expert programmer assistant.",
                tools=["terminal", "file_editor", "task_tracker"],
            ),
            AgentConfig(
                agent_type=AgentType.REVIEWER,
                name="Code Reviewer",
                description="Specialized for code review tasks",
                system_prompt="You are a code reviewer assistant.",
                tools=["terminal", "file_editor"],
            ),
            AgentConfig(
                agent_type=AgentType.RESEARCHER,
                name="Research Assistant",
                description="Specialized for research and information gathering",
                system_prompt="You are a research assistant.",
                tools=["web_search", "file_editor", "task_tracker"],
            ),
        ]
    
    # Sandbox endpoints
    @app.post("/api/v1/sandboxes")
    async def create_sandbox(
        sandbox_type: str = "docker",
        image: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new sandbox environment."""
        try:
            sandbox_id, sandbox, info = await app_state.sandbox_manager.create_sandbox(
                sandbox_type=sandbox_type,
                image=image or "ghcr.io/openhands/agent-server:latest-python",
            )
            return {
                "sandbox_id": sandbox_id,
                "sandbox_type": sandbox_type,
                "status": info.status,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.delete("/api/v1/sandboxes/{sandbox_id}")
    async def destroy_sandbox(sandbox_id: str) -> Dict[str, Any]:
        """Destroy a sandbox environment."""
        success = await app_state.sandbox_manager.destroy_sandbox(sandbox_id)
        if not success:
            raise HTTPException(status_code=404, detail="Sandbox not found")
        return {"message": "Sandbox destroyed", "sandbox_id": sandbox_id}
    
    @app.get("/api/v1/sandboxes")
    async def list_sandboxes() -> List[Dict[str, Any]]:
        """List all active sandboxes."""
        sandboxes = await app_state.sandbox_manager.list_sandboxes()
        return [
            {
                "sandbox_id": s.sandbox_id,
                "sandbox_type": s.sandbox_type,
                "status": s.status,
                "host_port": s.host_port,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in sandboxes
        ]
    
    # Metrics endpoints
    @app.get("/api/v1/metrics")
    async def get_metrics() -> Dict[str, Any]:
        """Get platform metrics."""
        tasks = await app_state.scheduler.queue.list_tasks()
        
        status_counts = {}
        for task in tasks:
            status = task.status.value
            status_counts[status] = status_counts.get(status, 0) + 1
        
        return {
            "total_tasks": len(tasks),
            "status_counts": status_counts,
            "queue_size": app_state.scheduler.queue.size(),
            "active_workers": len([w for w in app_state.scheduler._workers if not w.done()]),
            "timestamp": datetime.utcnow().isoformat(),
        }
    
    return app


async def execute_agent_task(
    task: Task,
    event_bus: EventBus,
    state: AppState,
) -> None:
    """
    Execute an agent task.
    
    This is the main handler for processing agent tasks.
    """
    logger.info("Executing agent task", task_id=task.id)
    
    try:
        # Publish started event
        await event_bus.publish(TaskEvent(
            task_id=task.id,
            event_type=EventType.TASK_STARTED.value,
            data={"task_id": task.id},
        ))
        
        # Create sandbox
        sandbox_id, sandbox, _ = await state.sandbox_manager.create_sandbox(
            sandbox_type="local",  # Use local for simplicity
        )
        
        # Get LLM provider
        llm = state.llm_provider
        if not llm:
            # Create default provider if not configured
            import os
            llm = LLMFactory.create(
                provider="openai",
                api_key=os.getenv("OPENAI_API_KEY", ""),
                default_model="gpt-4o",
            )
        
        # Create agent
        agent_config = AgentConfigModel(
            agent_type=task.agent_type,
            max_iterations=100,
            tools=state.tool_registry.get_all_names(),
        )
        
        engine = AgentEngine(
            llm=llm,
            tools=[state.tool_registry.get(name) for name in agent_config.tools],
            config=agent_config,
        )
        
        # Add event callbacks
        async def emit_event(event: Dict[str, Any]):
            await event_bus.publish(TaskEvent(
                task_id=task.id,
                event_type=event.get("type", "unknown"),
                data=event.get("data", {}),
            ))
        
        engine.add_callback(emit_event)
        
        # Create tool context
        context = ToolContext(
            workspace_id=sandbox_id,
            task_id=task.id,
            working_directory="/tmp/agent-workspace",
            sandbox=sandbox,
        )
        
        # Run agent
        result_state = await engine.run(task, context)
        
        # Update task with results
        task.result = result_state.final_result
        task.completed_at = datetime.utcnow()
        
        if result_state.is_error:
            task.status = TaskStatus.FAILED
            task.error = result_state.error_message
        else:
            task.status = TaskStatus.COMPLETED
        
        # Cleanup sandbox
        await state.sandbox_manager.destroy_sandbox(sandbox_id)
        
        # Publish completion
        event_type = (
            EventType.TASK_COMPLETED.value
            if task.status == TaskStatus.COMPLETED
            else EventType.TASK_FAILED.value
        )
        
        await event_bus.publish(TaskEvent(
            task_id=task.id,
            event_type=event_type,
            data={
                "task_id": task.id,
                "status": task.status,
                "result": task.result,
                "error": task.error,
            },
        ))
        
        logger.info(
            "Agent task completed",
            task_id=task.id,
            status=task.status,
            iterations=result_state.iteration,
        )
        
    except Exception as e:
        logger.error("Agent task failed", task_id=task.id, error=str(e))
        
        task.status = TaskStatus.FAILED
        task.error = str(e)
        task.completed_at = datetime.utcnow()
        
        await event_bus.publish(TaskEvent(
            task_id=task.id,
            event_type=EventType.TASK_FAILED.value,
            data={
                "task_id": task.id,
                "error": str(e),
            },
        ))


# Run with uvicorn
def run_server():
    """Run the API server."""
    import uvicorn
    from src.config import get_settings
    
    settings = get_settings()
    
    uvicorn.run(
        create_app,
        host=settings.api_host,
        port=settings.api_port,
        workers=1,  # Use 1 for debug, increase for production
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run_server()
