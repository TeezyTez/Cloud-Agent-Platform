"""Agent Engine - Core reasoning and execution loop."""
from typing import List, Dict, Any, Optional, AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime
import asyncio
import uuid
import structlog

from llm import LLMProvider, LLMResponse, Message
from agent.tools import Tool, ToolRegistry, ToolResult, ToolContext, ToolDefinition
from src.models import Task, TaskEvent, EventType, AgentType

logger = structlog.get_logger(__name__)


@dataclass
class AgentState:
    """Current state of the agent."""
    task_id: str
    iteration: int = 0
    messages: List[Dict[str, Any]] = field(default_factory=list)
    tool_results: List[ToolResult] = field(default_factory=list)
    is_complete: bool = False
    is_error: bool = False
    error_message: Optional[str] = None
    final_result: Optional[Dict[str, Any]] = None


@dataclass 
class AgentConfig:
    """Configuration for agent execution."""
    agent_type: AgentType = AgentType.DEFAULT
    system_prompt: str = ""
    max_iterations: int = 100
    timeout_seconds: int = 3600
    tools: List[str] = field(default_factory=list)  # Tool names to enable
    enable_thinking: bool = True
    confirmation_mode: bool = False


class AgentCallbacks:
    """Callbacks for agent events."""
    
    def __init__(self):
        self._callbacks: List[Callable[[Dict[str, Any]], None]] = []
    
    def add(self, callback: Callable[[Dict[str, Any]], None]):
        """Add a callback function."""
        self._callbacks.append(callback)
    
    async def emit(self, event: Dict[str, Any]):
        """Emit an event to all callbacks."""
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception as e:
                logger.error("Callback error", error=str(e))
    
    async def emit_thought(self, thought: str):
        """Emit a thought event."""
        await self.emit({
            "type": EventType.THOUGHT.value,
            "data": {"thought": thought},
        })
    
    async def emit_tool_call(self, tool_name: str, arguments: Dict[str, Any], tool_call_id: str):
        """Emit a tool call event."""
        await self.emit({
            "type": EventType.TOOL_CALL.value,
            "data": {
                "tool_name": tool_name,
                "arguments": arguments,
                "tool_call_id": tool_call_id,
            },
        })
    
    async def emit_tool_result(self, result: ToolResult):
        """Emit a tool result event."""
        await self.emit({
            "type": EventType.TOOL_RESULT.value,
            "data": {
                "tool_call_id": result.tool_call_id,
                "tool_name": result.tool_name,
                "success": result.success,
                "result": result.result,
                "error": result.error,
                "duration_ms": result.duration_ms,
            },
        })
    
    async def emit_message(self, content: str, role: str = "assistant"):
        """Emit an agent message event."""
        await self.emit({
            "type": EventType.AGENT_MESSAGE.value,
            "data": {"content": content, "role": role},
        })
    
    async def emit_error(self, error: str):
        """Emit an error event."""
        await self.emit({
            "type": EventType.ERROR.value,
            "data": {"error": error},
        })


class AgentEngine:
    """
    Core agent engine implementing the reasoning-action loop.
    
    The agent:
    1. Receives a task description
    2. Plans approach using LLM
    3. Executes tools based on LLM decisions
    4. Observes tool results
    5. Repeats until task is complete or max iterations reached
    """
    
    def __init__(
        self,
        llm: LLMProvider,
        tools: List[Tool],
        config: Optional[AgentConfig] = None,
    ):
        self.llm = llm
        self.tools = {t.name: t for t in tools}
        self.config = config or AgentConfig()
        self.callbacks = AgentCallbacks()
        self._state: Optional[AgentState] = None
        self._running = False
        self._cancelled = False
    
    def add_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """Add an event callback."""
        self.callbacks.add(callback)
    
    async def run(
        self,
        task: Task,
        initial_context: ToolContext,
    ) -> AgentState:
        """
        Run the agent on a task.
        
        Args:
            task: The task to execute
            initial_context: Context including sandbox and workspace info
            
        Returns:
            Final agent state with results
        """
        self._running = True
        self._cancelled = False
        
        self._state = AgentState(task_id=task.id)
        
        logger.info(
            "Starting agent execution",
            task_id=task.id,
            agent_type=self.config.agent_type,
        )
        
        # Build initial messages
        messages = self._build_messages(task.description)
        
        # Main reasoning loop
        while self._running and not self._state.is_complete:
            self._state.iteration += 1
            
            if self._state.iteration > self.config.max_iterations:
                logger.warning(
                    "Max iterations reached",
                    task_id=task.id,
                    iterations=self._state.iteration,
                )
                self._state.is_error = True
                self._state.error_message = f"Max iterations ({self.config.max_iterations}) reached"
                break
            
            # Check timeout
            # (In production, track elapsed time)
            
            try:
                # Get tool definitions
                tool_defs = self._get_enabled_tool_definitions()
                
                # Call LLM
                response = await self.llm.complete(
                    messages=messages,
                    tools=tool_defs if tool_defs else None,
                    temperature=0.7,
                )
                
                # Extract response content
                response_content = response.content
                
                # Log thought
                if response_content:
                    await self.callbacks.emit_thought(response_content)
                
                # Check if task is complete (no tool calls)
                # Parse tool calls from response if present
                tool_calls = self._parse_tool_calls(response.raw_response)
                
                if not tool_calls:
                    # No tool calls - agent has completed the task
                    self._state.is_complete = True
                    self._state.final_result = {
                        "message": response_content or "Task completed.",
                        "iterations": self._state.iteration,
                    }
                    await self.callbacks.emit_message(response_content or "Task completed.")
                    break
                
                # Execute tool calls
                for tool_call in tool_calls:
                    if self._cancelled:
                        break
                    
                    tool_name = tool_call.get("name")
                    tool_args = tool_call.get("arguments", {})
                    tool_call_id = tool_call.get("id", str(uuid.uuid4()))
                    
                    # Emit tool call event
                    await self.callbacks.emit_tool_call(tool_name, tool_args, tool_call_id)
                    
                    # Execute tool
                    result = await self._execute_tool(tool_call_id, tool_name, tool_args, initial_context)
                    
                    # Store result
                    self._state.tool_results.append(result)
                    
                    # Emit result event
                    await self.callbacks.emit_tool_result(result)
                    
                    # Add tool result to messages
                    messages.append(Message(
                        role="tool",
                        content=result.content,
                        tool_call_id=tool_call_id,
                    ))
                
                # Add assistant response to messages
                messages.append(Message(
                    role="assistant",
                    content=response_content,
                ))
                
            except asyncio.CancelledError:
                logger.info("Agent cancelled", task_id=task.id)
                self._cancelled = True
                self._state.is_error = True
                self._state.error_message = "Task cancelled"
                break
                
            except Exception as e:
                logger.error(
                    "Agent error",
                    task_id=task.id,
                    error=str(e),
                    iteration=self._state.iteration,
                )
                await self.callbacks.emit_error(str(e))
                self._state.is_error = True
                self._state.error_message = str(e)
                break
        
        self._running = False
        return self._state
    
    def cancel(self):
        """Cancel the running agent."""
        self._cancelled = True
        self._running = False
        logger.info("Agent cancellation requested")
    
    def _build_messages(self, task_description: str) -> List[Message]:
        """Build initial message list with system prompt and task."""
        messages = []
        
        # System prompt
        system_prompt = self.config.system_prompt or self._get_default_system_prompt()
        messages.append(Message(role="system", content=system_prompt))
        
        # User task
        messages.append(Message(
            role="user",
            content=f"Task: {task_description}\n\nPlease complete this task. Use the available tools as needed."
        ))
        
        return messages
    
    def _get_default_system_prompt(self) -> str:
        """Get default system prompt based on agent type."""
        prompts = {
            AgentType.DEFAULT: """You are a helpful AI assistant that can complete tasks using available tools.

Available tools:
- terminal: Execute shell commands
- file_editor: Read, write, and edit files
- task_tracker: Track and manage subtasks
- web_search: Search the web for information

Be thorough and precise. Always verify your work when possible.""",
            
            AgentType.CODER: """You are an expert programmer assistant.

Available tools:
- terminal: Execute shell commands (use for git, npm, python, etc.)
- file_editor: Read, write, and edit code files
- task_tracker: Track coding tasks

Follow best practices. Write clean, well-documented code. Always run tests when available.""",
            
            AgentType.REVIEWER: """You are a code reviewer assistant.

Available tools:
- terminal: Execute commands for code analysis
- file_editor: Read code files
- web_search: Look up documentation

Provide constructive, specific feedback. Reference specific lines and patterns.""",
            
            AgentType.RESEARCHER: """You are a research assistant.

Available tools:
- web_search: Search the web for information
- terminal: Execute commands for data processing
- file_editor: Save research findings

Be thorough and cite sources. Organize findings clearly.""",
        }
        return prompts.get(self.config.agent_type, prompts[AgentType.DEFAULT])
    
    def _get_enabled_tool_definitions(self) -> List[Dict[str, Any]]:
        """Get tool definitions for enabled tools."""
        definitions = []
        for tool_name in self.config.tools:
            if tool_name in self.tools:
                tool_def = self.tools[tool_name].definition
                definitions.append({
                    "type": "function",
                    "function": {
                        "name": tool_def.name,
                        "description": tool_def.description,
                        "parameters": tool_def.parameters,
                    },
                })
        return definitions
    
    def _parse_tool_calls(self, raw_response: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Parse tool calls from LLM response."""
        if not raw_response:
            return []
        
        # OpenAI format
        choices = raw_response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            tool_calls = message.get("tool_calls", [])
            return [
                {
                    "id": tc.get("id"),
                    "name": tc.get("function", {}).get("name"),
                    "arguments": tc.get("function", {}).get("arguments"),
                }
                for tc in tool_calls
            ]
        
        return []
    
    async def _execute_tool(
        self,
        tool_call_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        """Execute a tool and return the result."""
        if tool_name not in self.tools:
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                success=False,
                error=f"Unknown tool: {tool_name}",
            )
        
        tool = self.tools[tool_name]
        
        # Parse arguments if they're a string (JSON)
        if isinstance(arguments, str):
            import json
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return ToolResult(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    success=False,
                    error=f"Invalid arguments JSON: {arguments}",
                )
        
        try:
            result = await tool.execute(tool_call_id, arguments, context)
            return result
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                success=False,
                error=str(e),
            )


class SubAgentRunner:
    """
    Runner for delegating tasks to specialized sub-agents.
    
    This enables hierarchical task decomposition where a parent agent
    can spawn sub-agents for complex subtasks.
    """
    
    def __init__(
        self,
        llm_factory: Callable[[], LLMProvider],
        tool_registry: ToolRegistry,
    ):
        self.llm_factory = llm_factory
        self.tool_registry = tool_registry
    
    async def run_subagent(
        self,
        task_description: str,
        agent_type: AgentType = AgentType.DEFAULT,
        context: Optional[ToolContext] = None,
        max_iterations: int = 50,
    ) -> Dict[str, Any]:
        """Run a sub-agent and return results."""
        llm = self.llm_factory()
        tools = [
            self.tool_registry.get(name)
            for name in self.tool_registry.get_all_names()
        ]
        tools = [t for t in tools if t is not None]
        
        config = AgentConfig(
            agent_type=agent_type,
            max_iterations=max_iterations,
        )
        
        engine = AgentEngine(llm, tools, config)
        
        # Create a minimal task
        task = Task(description=task_description)
        
        # Run with placeholder context if not provided
        if context is None:
            context = ToolContext(
                workspace_id="subagent",
                task_id=task.id,
                working_directory="/workspace",
            )
        
        state = await engine.run(task, context)
        
        return {
            "success": state.is_complete and not state.is_error,
            "result": state.final_result,
            "error": state.error_message,
            "iterations": state.iteration,
        }
