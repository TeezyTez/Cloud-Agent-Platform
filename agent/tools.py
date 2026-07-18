"""Tool system for agent execution."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
import json
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ToolDefinition:
    """Definition of a tool for LLM consumption."""
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema for parameters
    required: List[str] = field(default_factory=list)
    returns: Optional[Dict[str, Any]] = None


@dataclass
class ToolResult:
    """Result from executing a tool."""
    tool_call_id: str
    tool_name: str
    success: bool
    result: Any = None
    error: Optional[str] = None
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    
    @property
    def content(self) -> str:
        """Get result as string content."""
        if self.error:
            return f"Error: {self.error}"
        if self.result is None:
            return "No result"
        if isinstance(self.result, str):
            return self.result
        return json.dumps(self.result, indent=2, default=str)


class Tool(ABC):
    """Abstract base class for tools."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name for invocation."""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description."""
        pass
    
    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        """Tool definition for LLM."""
        pass
    
    @abstractmethod
    async def execute(
        self,
        tool_call_id: str,
        arguments: Dict[str, Any],
        context: "ToolContext",
    ) -> ToolResult:
        """Execute the tool with given arguments."""
        pass


@dataclass
class ToolContext:
    """Context passed to tool execution."""
    workspace_id: str
    task_id: str
    working_directory: str
    environment: Dict[str, str] = field(default_factory=dict)
    sandbox: Optional["SandboxInterface"] = None


class ToolRegistry:
    """Registry of available tools."""
    
    def __init__(self):
        self._tools: Dict[str, Tool] = {}
        self._definitions: Dict[str, ToolDefinition] = {}
    
    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
        self._definitions[tool.name] = tool.definition
        logger.info("Registered tool", name=tool.name)
    
    def get(self, name: str) -> Optional[Tool]:
        """Get a tool by name."""
        return self._tools.get(name)
    
    def get_definition(self, name: str) -> Optional[ToolDefinition]:
        """Get tool definition by name."""
        return self._definitions.get(name)
    
    def get_all_definitions(self) -> List[ToolDefinition]:
        """Get all tool definitions."""
        return list(self._definitions.values())
    
    def get_all_names(self) -> List[str]:
        """Get all registered tool names."""
        return list(self._tools.keys())


class TerminalTool(Tool):
    """Execute shell commands in sandbox."""
    
    def __init__(self, allowed_commands: Optional[List[str]] = None):
        self._allowed_commands = allowed_commands or ["*"]  # Allow all by default
    
    @property
    def name(self) -> str:
        return "terminal"
    
    @property
    def description(self) -> str:
        return "Execute shell commands in the sandbox environment. Returns command output."
    
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Maximum execution time in seconds",
                        "default": 60,
                    },
                    "working_directory": {
                        "type": "string",
                        "description": "Working directory for command execution",
                    },
                },
                "required": ["command"],
            },
            required=["command"],
        )
    
    async def execute(
        self,
        tool_call_id: str,
        arguments: Dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        started_at = datetime.utcnow()
        
        try:
            command = arguments.get("command", "")
            timeout = arguments.get("timeout", 60)
            working_dir = arguments.get("working_directory") or context.working_directory
            
            if not context.sandbox:
                return ToolResult(
                    tool_call_id=tool_call_id,
                    tool_name=self.name,
                    success=False,
                    error="Sandbox not available",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                )
            
            result = await context.sandbox.execute_command(
                command=command,
                timeout=timeout,
                working_directory=working_dir,
            )
            
            completed_at = datetime.utcnow()
            duration_ms = (completed_at - started_at).total_seconds() * 1000
            
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name=self.name,
                success=result.exit_code == 0,
                result={
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "exit_code": result.exit_code,
                },
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
            )
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name=self.name,
                success=False,
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )


class FileEditorTool(Tool):
    """Read, write, and edit files in sandbox."""
    
    def __init__(self, allowed_paths: Optional[List[str]] = None):
        self._allowed_paths = allowed_paths or ["*"]
    
    @property
    def name(self) -> str:
        return "file_editor"
    
    @property
    def description(self) -> str:
        return "Read, write, and edit files in the sandbox environment."
    
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["read", "write", "edit", "delete", "list"],
                        "description": "Action to perform",
                    },
                    "path": {
                        "type": "string",
                        "description": "File path (relative to workspace)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content for write/edit operations",
                    },
                    "old_str": {
                        "type": "string",
                        "description": "String to replace (for edit action)",
                    },
                    "new_str": {
                        "type": "string",
                        "description": "Replacement string (for edit action)",
                    },
                },
                "required": ["action", "path"],
            },
            required=["action", "path"],
        )
    
    async def execute(
        self,
        tool_call_id: str,
        arguments: Dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        started_at = datetime.utcnow()
        
        try:
            action = arguments.get("action")
            path = arguments.get("path")
            
            if not context.sandbox:
                return ToolResult(
                    tool_call_id=tool_call_id,
                    tool_name=self.name,
                    success=False,
                    error="Sandbox not available",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                )
            
            full_path = f"{context.working_directory}/{path}" if not path.startswith("/") else path
            
            if action == "read":
                content = await context.sandbox.read_file(full_path)
                result = {"content": content}
                
            elif action == "write":
                content = arguments.get("content", "")
                await context.sandbox.write_file(full_path, content)
                result = {"message": f"File written: {path}"}
                
            elif action == "edit":
                old_str = arguments.get("old_str", "")
                new_str = arguments.get("new_str", "")
                await context.sandbox.edit_file(full_path, old_str, new_str)
                result = {"message": f"File edited: {path}"}
                
            elif action == "delete":
                await context.sandbox.delete_file(full_path)
                result = {"message": f"File deleted: {path}"}
                
            elif action == "list":
                files = await context.sandbox.list_directory(full_path)
                result = {"files": files}
                
            else:
                return ToolResult(
                    tool_call_id=tool_call_id,
                    tool_name=self.name,
                    success=False,
                    error=f"Unknown action: {action}",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                )
            
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name=self.name,
                success=True,
                result=result,
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
            
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name=self.name,
                success=False,
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )


class WebSearchTool(Tool):
    """Search the web for information."""
    
    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key
    
    @property
    def name(self) -> str:
        return "web_search"
    
    @property
    def description(self) -> str:
        return "Search the web for information. Returns search results with snippets."
    
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "max_results": {
                        "type": "number",
                        "description": "Maximum number of results",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
            required=["query"],
        )
    
    async def execute(
        self,
        tool_call_id: str,
        arguments: Dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        started_at = datetime.utcnow()
        
        try:
            query = arguments.get("query", "")
            max_results = arguments.get("max_results", 5)
            
            # Use Tavily or DuckDuckGo for search
            # This is a placeholder - implement actual search
            results = [
                {"title": f"Result {i+1} for {query}", "url": f"https://example.com/{i}", "snippet": f"Snippet {i+1}"}
                for i in range(max_results)
            ]
            
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name=self.name,
                success=True,
                result={"results": results},
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
            
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name=self.name,
                success=False,
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )


class TaskTrackerTool(Tool):
    """Track and manage subtasks."""
    
    def __init__(self):
        self._tasks: Dict[str, List[Dict[str, Any]]] = {}
    
    @property
    def name(self) -> str:
        return "task_tracker"
    
    @property
    def description(self) -> str:
        return "Track and manage subtasks for complex tasks."
    
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "update", "list", "complete", "delete"],
                        "description": "Action to perform",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Task ID for update/complete/delete",
                    },
                    "title": {
                        "type": "string",
                        "description": "Task title",
                    },
                    "description": {
                        "type": "string",
                        "description": "Task description",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed", "blocked"],
                        "description": "Task status",
                    },
                },
                "required": ["action"],
            },
            required=["action"],
        )
    
    async def execute(
        self,
        tool_call_id: str,
        arguments: Dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        started_at = datetime.utcnow()
        
        try:
            action = arguments.get("action")
            
            if context.workspace_id not in self._tasks:
                self._tasks[context.workspace_id] = []
            
            tasks = self._tasks[context.workspace_id]
            
            if action == "create":
                import uuid
                task = {
                    "id": str(uuid.uuid4())[:8],
                    "title": arguments.get("title", "Untitled"),
                    "description": arguments.get("description", ""),
                    "status": "pending",
                }
                tasks.append(task)
                result = {"task": task, "message": "Task created"}
                
            elif action == "update":
                task_id = arguments.get("task_id")
                for task in tasks:
                    if task["id"] == task_id:
                        if "title" in arguments:
                            task["title"] = arguments["title"]
                        if "description" in arguments:
                            task["description"] = arguments["description"]
                        if "status" in arguments:
                            task["status"] = arguments["status"]
                        break
                result = {"message": "Task updated"}
                
            elif action == "list":
                result = {"tasks": tasks}
                
            elif action == "complete":
                task_id = arguments.get("task_id")
                for task in tasks:
                    if task["id"] == task_id:
                        task["status"] = "completed"
                        break
                result = {"message": "Task completed"}
                
            elif action == "delete":
                task_id = arguments.get("task_id")
                self._tasks[context.workspace_id] = [t for t in tasks if t["id"] != task_id]
                result = {"message": "Task deleted"}
                
            else:
                return ToolResult(
                    tool_call_id=tool_call_id,
                    tool_name=self.name,
                    success=False,
                    error=f"Unknown action: {action}",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                )
            
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name=self.name,
                success=True,
                result=result,
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
            
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name=self.name,
                success=False,
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )


def get_default_tools() -> List[Tool]:
    """Get the default set of tools."""
    return [
        TerminalTool(),
        FileEditorTool(),
        TaskTrackerTool(),
        WebSearchTool(),
    ]
