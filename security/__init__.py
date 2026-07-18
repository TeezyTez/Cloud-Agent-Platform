"""Security module for action validation and access control."""
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass
from enum import Enum
import re
import structlog
import asyncio

logger = structlog.get_logger(__name__)


class SecurityLevel(Enum):
    """Security levels for actions."""
    SAFE = "safe"
    CAUTION = "caution"
    DANGER = "danger"
    BLOCKED = "blocked"


@dataclass
class SecurityPolicy:
    """Security policy configuration."""
    allow_network: bool = False
    allow_file_deletion: bool = True
    allow_process_kill: bool = False
    max_file_size_mb: int = 100
    max_execution_time_seconds: int = 300
    blocked_commands: List[str] = None
    
    def __post_init__(self):
        if self.blocked_commands is None:
            self.blocked_commands = [
                r"rm\s+-rf\s+/",  # Delete root
                r"dd\s+if=",  # Direct disk write
                r":\(\)\{",  # Fork bomb pattern
                r"curl.*\|\s*sh",  # Pipe to shell
                r"wget.*\|\s*sh",  # Pipe to shell
                r"mkfs",  # Format filesystem
                r"fdisk",  # Partition disk
            ]


@dataclass
class SecurityResult:
    """Result of a security check."""
    allowed: bool
    level: SecurityLevel
    reason: Optional[str] = None
    requires_confirmation: bool = False


class SecurityAnalyzer:
    """
    Analyzes and validates agent actions for security.
    
    Implements defense-in-depth security checks:
    1. Command pattern matching
    2. Resource limits
    3. Network access
    4. File system operations
    """
    
    # Dangerous file patterns
    DANGEROUS_PATTERNS = [
        r"^/etc/passwd$",
        r"^/etc/shadow$",
        r"^/etc/sudoers$",
        r"\.ssh/authorized_keys$",
        r"\.aws/credentials$",
        r"/root/\.bashrc$",
        r"/home/.*/\.bashrc$",
    ]
    
    # Dangerous commands
    DANGEROUS_COMMANDS = [
        "chmod 777",
        "chmod +x",
        "sudo su",
        "passwd root",
        "useradd",
        "usermod",
        "deluser",
        "delgroup",
        "shutdown",
        "reboot",
        "init 0",
        "init 6",
        "killall",
        "pkill -9",
        "netstat",
        "tcpdump",
        "nmap",
        "hydra",
        "john",
        "hashcat",
    ]
    
    def __init__(self, policy: Optional[SecurityPolicy] = None):
        self.policy = policy or SecurityPolicy()
        self._pattern_cache: Dict[str, re.Pattern] = {}
    
    def _compile_pattern(self, pattern: str) -> re.Pattern:
        """Compile and cache regex pattern."""
        if pattern not in self._pattern_cache:
            self._pattern_cache[pattern] = re.compile(pattern, re.IGNORECASE)
        return self._pattern_cache[pattern]
    
    def analyze_command(
        self,
        command: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> SecurityResult:
        """
        Analyze a shell command for security risks.
        
        Args:
            command: The command to analyze
            context: Optional execution context
            
        Returns:
            SecurityResult indicating if command is allowed
        """
        # Check blocked patterns
        for pattern in self.policy.blocked_commands:
            compiled = self._compile_pattern(pattern)
            if compiled.search(command):
                return SecurityResult(
                    allowed=False,
                    level=SecurityLevel.BLOCKED,
                    reason=f"Command matches blocked pattern: {pattern}",
                )
        
        # Check dangerous commands
        command_lower = command.lower()
        for dangerous in self.DANGEROUS_COMMANDS:
            if dangerous.lower() in command_lower:
                return SecurityResult(
                    allowed=False,
                    level=SecurityLevel.DANGER,
                    reason=f"Command contains dangerous operation: {dangerous}",
                    requires_confirmation=True,
                )
        
        # Check network access
        network_indicators = [
            "curl", "wget", "nc ", "netcat", "ssh", "telnet",
            "ftp", "sftp", "scp", "rsync", "http", "https",
        ]
        
        for indicator in network_indicators:
            if indicator in command_lower:
                if not self.policy.allow_network:
                    return SecurityResult(
                        allowed=False,
                        level=SecurityLevel.CAUTION,
                        reason=f"Network operation not allowed: {indicator}",
                    )
        
        # Check for file operations that could be dangerous
        file_delete_patterns = [
            r"rm\s+-rf",
            r"rm\s+-r\s+/\*?",
            r"find.*-delete",
            r"rm\s+-rf\s+/\*",
        ]
        
        for pattern in file_delete_patterns:
            compiled = self._compile_pattern(pattern)
            if compiled.search(command):
                if not self.policy.allow_file_deletion:
                    return SecurityResult(
                        allowed=False,
                        level=SecurityLevel.DANGER,
                        reason="File deletion not allowed by policy",
                    )
                return SecurityResult(
                    allowed=True,
                    level=SecurityLevel.CAUTION,
                    reason="Caution: Aggressive file deletion command",
                    requires_confirmation=True,
                )
        
        # Check process termination
        if any(kw in command_lower for kw in ["kill ", "killall", "pkill", "kill -9"]):
            if not self.policy.allow_process_kill:
                return SecurityResult(
                    allowed=False,
                    level=SecurityLevel.CAUTION,
                    reason="Process termination not allowed by policy",
                )
        
        # Command appears safe
        return SecurityResult(
            allowed=True,
            level=SecurityLevel.SAFE,
        )
    
    def analyze_file_path(
        self,
        path: str,
        operation: str = "read",
    ) -> SecurityResult:
        """
        Analyze a file path for security risks.
        
        Args:
            path: File path to check
            operation: Type of operation (read, write, delete)
            
        Returns:
            SecurityResult indicating if operation is allowed
        """
        # Check dangerous patterns
        for pattern in self.DANGEROUS_PATTERNS:
            compiled = self._compile_pattern(pattern)
            if compiled.match(path):
                return SecurityResult(
                    allowed=False,
                    level=SecurityLevel.BLOCKED,
                    reason=f"Access to sensitive file not allowed: {path}",
                )
        
        # Check for path traversal attempts
        if ".." in path or path.startswith("/etc") or path.startswith("/root"):
            return SecurityResult(
                allowed=False,
                level=SecurityLevel.DANGER,
                reason="Path traversal or sensitive directory access",
            )
        
        # Write operations to system directories
        system_dirs = ["/bin", "/sbin", "/usr/bin", "/usr/sbin", "/lib", "/boot"]
        for sys_dir in system_dirs:
            if path.startswith(sys_dir) and operation in ["write", "delete"]:
                return SecurityResult(
                    allowed=False,
                    level=SecurityLevel.BLOCKED,
                    reason=f"Writing to system directory not allowed: {sys_dir}",
                )
        
        return SecurityResult(
            allowed=True,
            level=SecurityLevel.SAFE,
        )
    
    def analyze_url(
        self,
        url: str,
    ) -> SecurityResult:
        """
        Analyze a URL for security risks.
        
        Args:
            url: URL to check
            
        Returns:
            SecurityResult indicating if URL is allowed
        """
        # Block javascript: URLs
        if url.lower().startswith("javascript:"):
            return SecurityResult(
                allowed=False,
                level=SecurityLevel.BLOCKED,
                reason="JavaScript URLs are not allowed",
            )
        
        # Block data: URLs (could contain malicious content)
        if url.lower().startswith("data:"):
            return SecurityResult(
                allowed=False,
                level=SecurityLevel.BLOCKED,
                reason="Data URLs are not allowed",
            )
        
        # Check for IP addresses in URL (could be used for SSRF)
        ip_pattern = r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"
        if re.search(ip_pattern, url):
            if not self.policy.allow_network:
                return SecurityResult(
                    allowed=False,
                    level=SecurityLevel.CAUTION,
                    reason="Direct IP access not allowed (SSRF protection)",
                )
        
        # Check for localhost/internal addresses
        internal_patterns = [
            r"localhost",
            r"127\.0\.0\.1",
            r"0\.0\.0\.0",
            r"169\.254\.169\.254",  # AWS metadata
            r"metadata\.google",  # GCP metadata
        ]
        
        for pattern in internal_patterns:
            if re.search(pattern, url, re.IGNORECASE):
                return SecurityResult(
                    allowed=False,
                    level=SecurityLevel.BLOCKED,
                    reason=f"Access to internal endpoint not allowed: {pattern}",
                )
        
        return SecurityResult(
            allowed=True,
            level=SecurityLevel.SAFE,
        )


class RateLimiter:
    """
    Token bucket rate limiter for API endpoints.
    """
    
    def __init__(
        self,
        rate: int,
        period_seconds: int,
    ):
        self.rate = rate
        self.period_seconds = period_seconds
        self._buckets: Dict[str, tuple[int, float]] = {}  # key -> (tokens, last_refill)
        self._lock = asyncio.Lock()
    
    async def is_allowed(self, key: str) -> bool:
        """
        Check if a request is allowed under the rate limit.
        
        Args:
            key: Identifier for rate limiting (e.g., user_id, IP)
            
        Returns:
            True if request is allowed, False if rate limited
        """
        import time
        
        async with self._lock:
            now = time.time()
            tokens, last_refill = self._buckets.get(key, (self.rate, now))
            
            # Refill tokens based on elapsed time
            elapsed = now - last_refill
            refill_amount = (elapsed / self.period_seconds) * self.rate
            tokens = min(self.rate, tokens + refill_amount)
            
            if tokens >= 1:
                tokens -= 1
                self._buckets[key] = (tokens, now)
                return True
            else:
                self._buckets[key] = (tokens, now)
                return False
    
    async def reset(self, key: str) -> None:
        """Reset rate limit for a key."""
        async with self._lock:
            self._buckets.pop(key, None)


class AccessControl:
    """
    Simple access control for API endpoints.
    """
    
    def __init__(self):
        self._permissions: Dict[str, List[str]] = {}  # user_id -> permissions
        self._api_keys: Dict[str, Dict[str, Any]] = {}  # api_key -> {user_id, permissions}
    
    def add_user_permission(self, user_id: str, permission: str) -> None:
        """Add a permission for a user."""
        if user_id not in self._permissions:
            self._permissions[user_id] = []
        if permission not in self._permissions[user_id]:
            self._permissions[user_id].append(permission)
    
    def add_api_key(
        self,
        api_key: str,
        user_id: str,
        permissions: Optional[List[str]] = None,
    ) -> None:
        """Register an API key."""
        self._api_keys[api_key] = {
            "user_id": user_id,
            "permissions": permissions or [],
        }
    
    def validate_api_key(self, api_key: str) -> Optional[Dict[str, Any]]:
        """Validate an API key and return user info."""
        return self._api_keys.get(api_key)
    
    def has_permission(self, user_id: str, permission: str) -> bool:
        """Check if a user has a specific permission."""
        user_perms = self._permissions.get(user_id, [])
        return permission in user_perms or "*" in user_perms


# Default security analyzer instance
_default_analyzer: Optional[SecurityAnalyzer] = None


def get_security_analyzer() -> SecurityAnalyzer:
    """Get the default security analyzer."""
    global _default_analyzer
    if _default_analyzer is None:
        _default_analyzer = SecurityAnalyzer()
    return _default_analyzer
