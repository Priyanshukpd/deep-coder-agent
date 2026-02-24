"""
Security Advisor Tool — LLM-guided security validation.

This tool acts as a "second opinion" for security alerts. It uses the LLM 
to determine if a specific dependency is a legitimate risk or a false positive 
(e.g., a standard library or a well-known internal package).
"""

import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class SecurityAdvice(BaseModel):
    decision: str = Field(description="One of: SAFE, SUSPICIOUS, DANGEROUS")
    reasoning: str = Field(description="Explanation for the decision")
    reconciliation_steps: Optional[str] = Field(description="How to fix if suspicious/dangerous")

ADVISOR_PROMPT = """You are a Security Operations Center (SOC) Analyst.
Your goal is to analyze a security alert for a software dependency.

CONTEXT:
- Task: {task}
- Stack: {stack}
- Flagged Package: {package}
- Heuristic reasoning: {reasoning}

DECISION CRITERIA:
1. SAFE: The package is clearly a standard library (e.g., 'os', 'sys', 'json' in Python) or a highly-trusted, well-known package that the heuristic simply missed.
2. SUSPICIOUS: The name is very close to a major package (e.g., 'requesst' vs 'requests') or uses a known typosquatting pattern (e.g., 'python-package' when the package is just 'package').
3. DANGEROUS: High confidence this is a malicious attempt to steal secrets or execute code.

Analyze the package name carefully. Respond using the provide tool."""

ADVISOR_TOOL = {
    "type": "function",
    "function": {
        "name": "analyze_security_risk",
        "description": "Provide a security assessment of a flagged dependency.",
        "parameters": {
            "type": "object",
            "properties": {
                "decision": {
                    "type": "string",
                    "enum": ["SAFE", "SUSPICIOUS", "DANGEROUS"],
                    "description": "The security classification."
                },
                "reasoning": {
                    "type": "string",
                    "description": "Detailed reasoning for the classification."
                },
                "reconciliation_steps": {
                    "type": "string",
                    "description": "Suggested fix if not SAFE."
                }
            },
            "required": ["decision", "reasoning"]
        }
    }
}

class SecurityAdvisor:
    def __init__(self, provider: Any):
        self.provider = provider

    def advise(self, package: str, heuristic_reason: str, task: str, stack: str) -> SecurityAdvice:
        """
        Consult the LLM for a security opinion on a flagged package.
        """
        logger.info(f"🛡️ Consulting Security Advisor for: {package}")
        
        prompt = ADVISOR_PROMPT.format(
            task=task,
            stack=stack,
            package=package,
            reasoning=heuristic_reason
        )
        
        messages = [
            {"role": "system", "content": "You are an expert Security Advisor."},
            {"role": "user", "content": prompt}
        ]
        
        try:
            # Use function calling for structured output
            res = self.provider.complete_with_tools(
                messages=messages,
                tools=[ADVISOR_TOOL],
                tool_choice="required"
            )
            
            args = res.arguments
            return SecurityAdvice(
                decision=args.get("decision", "SUSPICIOUS").upper(),
                reasoning=args.get("reasoning", "No reasoning provided"),
                reconciliation_steps=args.get("reconciliation_steps")
            )
        except Exception as e:
            logger.error(f"Security Advisor failed: {e}")
            # Fallback to suspicious on failure for safety
            return SecurityAdvice(
                decision="SUSPICIOUS",
                reasoning=f"Advisor failed to respond: {e}",
            )
