"""
Enhanced MCP A2A Bridge with improved task management and result retrieval.

This script implements a bridge between the MCP protocol and A2A protocol,
allowing MCP clients to interact with A2A agents.

Supports multiple transport types:
- stdio: Standard input/output transport
- streamable-http: Recommended HTTP transport
- sse: Server-Sent Events transport

Configure the transport type using the MCP_TRANSPORT environment variable.
"""

import asyncio
import os
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional, Union
import json

import httpx
from fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from common.types import (
    AgentCard,
    AgentCapabilities,
    AgentSkill,
    Artifact,
    DataPart,
    Message,
    Part,
    TextPart,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TaskArtifactUpdateEvent,
    JSONRPCResponse,
    SendTaskRequest,
    SendTaskResponse,
    SendTaskStreamingRequest,
    SendTaskStreamingResponse,
    GetTaskRequest,
    GetTaskResponse,
    TaskQueryParams,
    CancelTaskRequest, 
    CancelTaskResponse
)
from common.client.client import A2AClient
from common.server.task_manager import InMemoryTaskManager

# Create a FastMCP server
mcp = FastMCP("A2A Bridge Server")

# Store for A2A agents that have been registered
registered_agents = {}
# Store task_id to agent_url mappings for later retrieval
task_agent_mapping = {}

# Load transport configuration from environment variables
DEFAULT_TRANSPORT = "stdio"
TRANSPORT_TYPES = ["stdio", "streamable-http", "sse"]

# MCP server configuration
MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", DEFAULT_TRANSPORT).lower()
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))
MCP_PATH = os.environ.get("MCP_PATH", "/mcp")  # For streamable-http
MCP_SSE_PATH = os.environ.get("MCP_SSE_PATH", "/sse")  # For sse

# A2A server configuration
A2A_HOST = os.environ.get("A2A_HOST", "0.0.0.0")
A2A_PORT = int(os.environ.get("A2A_PORT", "41241"))

# Validate transport type
if MCP_TRANSPORT not in TRANSPORT_TYPES:
    print(f"Warning: Invalid transport type '{MCP_TRANSPORT}'. Using default: {DEFAULT_TRANSPORT}")
    MCP_TRANSPORT = DEFAULT_TRANSPORT

class AgentInfo(BaseModel):
    """Information about an A2A agent."""
    url: str = Field(description="URL of the A2A agent")
    name: str = Field(description="Name of the A2A agent")
    description: str = Field(description="Description of the A2A agent")

class A2ABridgeTaskManager(InMemoryTaskManager):
    """Task manager that forwards tasks to A2A agents."""
    
    def __init__(self):
        super().__init__()
        self.agent_clients = {}  # Maps agent URLs to A2AClient instances
        
    def get_or_create_client(self, agent_url: str) -> A2AClient:
        """Get an existing client or create a new one."""
        if agent_url not in self.agent_clients:
            self.agent_clients[agent_url] = A2AClient(url=agent_url)  # Use named parameter
        return self.agent_clients[agent_url]
    
    async def on_send_task(self, request: SendTaskRequest) -> SendTaskResponse:
        """Handle a task send request by forwarding to the appropriate A2A agent."""
        task_id = request.params.id
        # Extract the agent URL from metadata
        agent_url = request.params.metadata.get("agent_url") if request.params.metadata else None
        
        if not agent_url:
            # No agent URL provided, return error
            return SendTaskResponse(
                id=request.id,
                error={
                    "code": -32602,
                    "message": "Agent URL not provided in task metadata",
                }
            )
        
        client = self.get_or_create_client(agent_url)
        
        # Forward the message to the A2A agent
        try:
            # Create payload as a single dictionary
            payload = {
                "id": task_id,
                "message": request.params.message,
            }
            if request.params.sessionId:
                payload["sessionId"] = request.params.sessionId
            if request.params.metadata:
                payload["metadata"] = request.params.metadata
                
            result = await client.send_task(payload)
            
            # Store the task result
            self.tasks[task_id] = result
            
            # Return the response
            return SendTaskResponse(id=request.id, result=result)
        except Exception as e:
            return SendTaskResponse(
                id=request.id,
                error={
                    "code": -32603,
                    "message": f"Error forwarding task to A2A agent: {str(e)}",
                }
            )
    
    async def on_send_task_subscribe(
        self, request: SendTaskStreamingRequest
    ) -> AsyncGenerator[SendTaskStreamingResponse, None] | JSONRPCResponse:
        """Handle a task subscription request by forwarding to the appropriate A2A agent."""
        task_id = request.params.id
        # Extract the agent URL from metadata
        agent_url = request.params.metadata.get("agent_url") if request.params.metadata else None
        
        if not agent_url:
            # No agent URL provided, return error
            return JSONRPCResponse(
                id=request.id,
                error={
                    "code": -32602,
                    "message": "Agent URL not provided in task metadata",
                }
            )
        
        client = self.get_or_create_client(agent_url)
        
        # Set up SSE consumer
        sse_event_queue = await self.setup_sse_consumer(task_id=task_id)
        
        # Start forwarding the task in a background task
        asyncio.create_task(self._forward_task_stream(
            client=client,
            request=request,
            task_id=task_id,
        ))
        
        # Return the SSE consumer
        return self.dequeue_events_for_sse(
            request_id=request.id,
            task_id=task_id,
            sse_event_queue=sse_event_queue,
        )
    
    async def _forward_task_stream(
        self, client: A2AClient, request: SendTaskStreamingRequest, task_id: str
    ):
        """Forward a task stream to an A2A agent and relay the responses."""
        try:
            # Create payload as a single dictionary
            payload = {
                "id": task_id,
                "message": request.params.message,
            }
            if request.params.sessionId:
                payload["sessionId"] = request.params.sessionId
            if request.params.metadata:
                payload["metadata"] = request.params.metadata
                
            # Send the task and subscribe to updates
            stream = client.send_task_subscribe(payload)
            
            # Process the stream events
            async for event in stream:
                # Forward the event to our SSE queue
                await self.enqueue_events_for_sse(task_id, event)
                
                # If this is the final event, break
                if hasattr(event, "final") and event.final:
                    break
                    
        except Exception as e:
            # Create an error event and enqueue it
            error_event = TaskStatusUpdateEvent(
                id=task_id,
                status=TaskStatus(
                    state=TaskState.FAILED,
                    message=Message(
                        role="agent",
                        parts=[TextPart(text=f"Error forwarding task: {str(e)}")],
                    ),
                ),
                final=True,
            )
            await self.enqueue_events_for_sse(task_id, error_event)

async def fetch_agent_card(url: str) -> AgentCard:
    """
    Fetch the agent card from the agent's URL.
    First try the main URL, then the well-known location.
    """
    async with httpx.AsyncClient() as client:
        # First try the main endpoint
        try:
            response = await client.get(url)
            if response.status_code == 200:
                try:
                    data = response.json()
                    if isinstance(data, dict) and "name" in data and "url" in data:
                        return AgentCard(**data)
                except json.JSONDecodeError:
                    pass  # Not a valid JSON response, try the well-known URL
        except Exception:
            pass  # Connection error, try the well-known URL
        
        # Try the well-known location
        well_known_url = f"{url.rstrip('/')}/.well-known/agent.json"
        try:
            response = await client.get(well_known_url)
            if response.status_code == 200:
                try:
                    data = response.json()
                    return AgentCard(**data)
                except json.JSONDecodeError:
                    raise ValueError(f"Invalid JSON in agent card from {well_known_url}")
        except httpx.RequestError as e:
            raise ValueError(f"Failed to fetch agent card from {well_known_url}: {str(e)}")
    
    # If we can't get the agent card, create a minimal one with default values
    return AgentCard(
        name="Unknown Agent",
        url=url,
        version="0.1.0",
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="unknown",
                name="Unknown Skill",
                description="Unknown agent capabilities",
            )
        ],
    )


@mcp.tool()
async def register_agent(url: str, ctx: Context) -> Dict[str, Any]:
    """
    Register an A2A agent with the bridge server.
    
    Args:
        url: URL of the A2A agent
        
    Returns:
        Dictionary with registration status
    """
    try:
        # Fetch the agent card directly
        agent_card = await fetch_agent_card(url)
        
        # Store the agent information
        agent_info = AgentInfo(
            url=url,
            name=agent_card.name,
            description=agent_card.description or "No description provided",
        )
        
        registered_agents[url] = agent_info
        
        await ctx.info(f"Successfully registered agent: {agent_card.name}")
        return {
            "status": "success",
            "agent": agent_info.model_dump(),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to register agent: {str(e)}",
        }


@mcp.tool()
async def list_agents() -> List[Dict[str, Any]]:
    """
    List all registered A2A agents.
    
    Returns:
        List of registered agents
    """
    return [agent.model_dump() for agent in registered_agents.values()]


@mcp.tool()
async def unregister_agent(url: str, ctx: Context = None) -> Dict[str, Any]:
    """
    Unregister an A2A agent from the bridge server.
    
    Args:
        url: URL of the A2A agent to unregister
        
    Returns:
        Dictionary with unregistration status
    """
    if url not in registered_agents:
        return {
            "status": "error",
            "message": f"Agent not registered: {url}",
        }
    
    try:
        # Get agent name before removing it
        agent_name = registered_agents[url].name
        
        # Remove from registered agents
        del registered_agents[url]
        
        # Clean up any task mappings related to this agent
        # Create a list of task_ids to remove to avoid modifying the dictionary during iteration
        tasks_to_remove = []
        for task_id, agent_url in task_agent_mapping.items():
            if agent_url == url:
                tasks_to_remove.append(task_id)
        
        # Now remove the task mappings
        for task_id in tasks_to_remove:
            del task_agent_mapping[task_id]
        
        if ctx:
            await ctx.info(f"Successfully unregistered agent: {agent_name}")
        
        return {
            "status": "success",
            "message": f"Successfully unregistered agent: {agent_name}",
            "removed_tasks": len(tasks_to_remove),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error unregistering agent: {str(e)}",
        }


@mcp.tool()
async def send_message(
    agent_url: str,
    message: str,
    session_id: Optional[str] = None,
    ctx: Context = None,
) -> Dict[str, Any]:
    """
    Send a message to an A2A agent.
    
    Args:
        agent_url: URL of the A2A agent
        message: Message to send
        session_id: Optional session ID for multi-turn conversations
        
    Returns:
        Agent's response with task_id for future reference
    """
    if agent_url not in registered_agents:
        return {
            "status": "error",
            "message": f"Agent not registered: {agent_url}",
        }
    
    # Create a client for the agent
    client = A2AClient(url=agent_url)
    
    try:
        # Generate a task ID
        task_id = str(uuid.uuid4())
        
        # Store the mapping of task_id to agent_url for later reference
        task_agent_mapping[task_id] = agent_url
        
        # Create the message
        a2a_message = Message(
            role="user",
            parts=[TextPart(text=message)],
        )
        
        if ctx:
            await ctx.info(f"Sending message to agent: {message}")
        
        # Create payload as a single dictionary
        payload = {
            "id": task_id,
            "message": a2a_message,
        }
        if session_id:
            payload["sessionId"] = session_id
        
        # Send the task with the payload
        result = await client.send_task(payload)
        
        # Debug: Print the raw response for analysis
        if ctx:
            await ctx.info(f"Raw response: {result}")
            
        # Create a response dictionary with as much info as we can extract
        response = {
            "status": "success",
            "task_id": task_id,
        }
        
        # Add any available fields from the result
        if hasattr(result, "sessionId"):
            response["session_id"] = result.sessionId
        else:
            response["session_id"] = None
            
        # Try to get the state
        try:
            if hasattr(result, "status") and hasattr(result.status, "state"):
                response["state"] = result.status.state
            else:
                response["state"] = "unknown"
        except Exception as e:
            response["state"] = f"error_getting_state: {str(e)}"
            
        # Try to extract response message
        try:
            if hasattr(result, "status") and hasattr(result.status, "message") and result.status.message:
                response_text = ""
                for part in result.status.message.parts:
                    if part.type == "text":
                        response_text += part.text
                if response_text:
                    response["message"] = response_text
        except Exception as e:
            response["message_error"] = f"Error extracting message: {str(e)}"
        
        # Try to get artifacts
        try:
            if hasattr(result, "artifacts") and result.artifacts:
                artifacts_data = []
                for artifact in result.artifacts:
                    artifact_data = {
                        "name": artifact.name if hasattr(artifact, "name") else "unnamed_artifact",
                        "contents": [],
                    }
                    
                    for part in artifact.parts:
                        if part.type == "text":
                            artifact_data["contents"].append({
                                "type": "text",
                                "text": part.text,
                            })
                        elif part.type == "data":
                            artifact_data["contents"].append({
                                "type": "data",
                                "data": part.data,
                            })
                    
                    artifacts_data.append(artifact_data)
                
                response["artifacts"] = artifacts_data
        except Exception as e:
            response["artifacts_error"] = f"Error extracting artifacts: {str(e)}"
            
        return response
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error sending message: {str(e)}",
        }


@mcp.tool()
async def get_task_result(
    task_id: str,
    history_length: Optional[int] = None,
    ctx: Context = None,
) -> Dict[str, Any]:
    """
    Retrieve the result of a task from an A2A agent.
    
    Args:
        task_id: ID of the task to retrieve
        history_length: Optional number of history items to include (null for all)
        
    Returns:
        Task result including status, message, and artifacts if available
    """
    if task_id not in task_agent_mapping:
        return {
            "status": "error",
            "message": f"Task ID not found: {task_id}",
        }
    
    agent_url = task_agent_mapping[task_id]
    
    # Create a client for the agent
    client = A2AClient(url=agent_url)
    
    try:
        # Create the request payload
        payload = {
            "id": task_id,
            "historyLength": history_length
        }
        
        if ctx:
            await ctx.info(f"Retrieving task result for task_id: {task_id}")
        
        # Send the get task request
        result = await client.get_task(payload)
        
        # Debug: Print the raw response for analysis
        if ctx:
            await ctx.info(f"Raw task result: {result}")
            
        # Create a response dictionary with as much info as we can extract
        response = {
            "status": "success",
            "task_id": task_id,
        }
        
        # Try to extract task data
        try:
            if hasattr(result, "result"):
                task = result.result
                
                # Add basic task info
                if hasattr(task, "sessionId"):
                    response["session_id"] = task.sessionId
                else:
                    response["session_id"] = None
                
                # Add task status
                if hasattr(task, "status"):
                    status = task.status
                    if hasattr(status, "state"):
                        response["state"] = status.state
                    
                    # Extract message from status
                    if hasattr(status, "message") and status.message:
                        response_text = ""
                        for part in status.message.parts:
                            if part.type == "text":
                                response_text += part.text
                        if response_text:
                            response["message"] = response_text
                
                # Extract artifacts
                if hasattr(task, "artifacts") and task.artifacts:
                    artifacts_data = []
                    for artifact in task.artifacts:
                        artifact_data = {
                            "name": artifact.name if hasattr(artifact, "name") else "unnamed_artifact",
                            "contents": [],
                        }
                        
                        for part in artifact.parts:
                            if part.type == "text":
                                artifact_data["contents"].append({
                                    "type": "text",
                                    "text": part.text,
                                })
                            elif part.type == "data":
                                artifact_data["contents"].append({
                                    "type": "data",
                                    "data": part.data,
                                })
                        
                        artifacts_data.append(artifact_data)
                    
                    response["artifacts"] = artifacts_data
                
                # Extract message history if available
                if hasattr(task, "history") and task.history:
                    history_data = []
                    for message in task.history:
                        message_data = {
                            "role": message.role,
                            "parts": [],
                        }
                        
                        for part in message.parts:
                            if part.type == "text":
                                message_data["parts"].append({
                                    "type": "text",
                                    "text": part.text,
                                })
                            elif hasattr(part, "data"):
                                message_data["parts"].append({
                                    "type": "data",
                                    "data": part.data,
                                })
                        
                        history_data.append(message_data)
                    
                    response["history"] = history_data
            else:
                response["error"] = "No result in response"
                
        except Exception as e:
            response["parsing_error"] = f"Error parsing task result: {str(e)}"
            
        return response
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error retrieving task result: {str(e)}",
        }


@mcp.tool()
async def cancel_task(
    task_id: str,
    ctx: Context = None,
) -> Dict[str, Any]:
    """
    Cancel a running task on an A2A agent.
    
    Args:
        task_id: ID of the task to cancel
        
    Returns:
        Cancellation result
    """
    if task_id not in task_agent_mapping:
        return {
            "status": "error",
            "message": f"Task ID not found: {task_id}",
        }
    
    agent_url = task_agent_mapping[task_id]
    
    # Create a client for the agent
    client = A2AClient(url=agent_url)
    
    try:
        # Create the request payload
        payload = {
            "id": task_id
        }
        
        if ctx:
            await ctx.info(f"Cancelling task: {task_id}")
        
        # Send the cancel task request
        result = await client.cancel_task(payload)
        
        # Debug: Print the raw response for analysis
        if ctx:
            await ctx.info(f"Raw cancellation result: {result}")
            
        # Create a response dictionary
        if hasattr(result, "error"):
            return {
                "status": "error",
                "task_id": task_id,
                "message": result.error.message,
                "code": result.error.code
            }
        elif hasattr(result, "result"):
            return {
                "status": "success",
                "task_id": task_id,
                "message": "Task cancelled successfully"
            }
        else:
            return {
                "status": "unknown",
                "task_id": task_id,
                "message": "Unexpected response format"
            }
            
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error cancelling task: {str(e)}",
        }


@mcp.tool()
async def send_message_stream(
    agent_url: str,
    message: str,
    session_id: Optional[str] = None,
    ctx: Context = None,
) -> Dict[str, Any]:
    """
    Send a message to an A2A agent and stream the response.
    
    Args:
        agent_url: URL of the A2A agent
        message: Message to send
        session_id: Optional session ID for multi-turn conversations
        
    Returns:
        Stream of agent's responses
    """
    if agent_url not in registered_agents:
        return {
            "status": "error",
            "message": f"Agent not registered: {agent_url}",
        }
    
    # Create a client for the agent
    client = A2AClient(url=agent_url)
    
    try:
        # Generate a task ID
        task_id = str(uuid.uuid4())
        
        # Store the mapping of task_id to agent_url for later reference
        task_agent_mapping[task_id] = agent_url
        
        # Create the message
        a2a_message = Message(
            role="user",
            parts=[TextPart(text=message)],
        )
        
        if ctx:
            await ctx.info(f"Sending message to agent (streaming): {message}")
            
        # Start progress indication
        if ctx:
            await ctx.info("Processing...")
        
        # Dictionary to accumulate streaming responses
        complete_response = {
            "status": "success",
            "task_id": task_id,
            "session_id": session_id,
            "state": "working",
            "messages": [],
            "artifacts": [],
        }
        
        # Create payload as a single dictionary
        payload = {
            "id": task_id,
            "message": a2a_message,
        }
        if session_id:
            payload["sessionId"] = session_id
        
        # Send the task and subscribe to updates
        stream = client.send_task_streaming(payload)
        
        # Process and report stream events
        try:
            all_events = []
            
            async for event in stream:
                # Save all events for debugging
                all_events.append({
                    "type": str(type(event)),
                    "dir": str(dir(event)),
                })
                
                if hasattr(event, "result"):
                    if hasattr(event.result, "status"):
                        # It's a TaskStatusUpdateEvent
                        status_event = event.result
                        
                        # Update the state
                        if hasattr(status_event, "status") and hasattr(status_event.status, "state"):
                            complete_response["state"] = status_event.status.state
                        
                        # Extract any message
                        if hasattr(status_event, "status") and hasattr(status_event.status, "message") and status_event.status.message:
                            message_text = ""
                            for part in status_event.status.message.parts:
                                if part.type == "text":
                                    message_text += part.text
                            
                            if message_text:
                                complete_response["messages"].append(message_text)
                                if ctx:
                                    await ctx.info(f"Agent: {message_text}")
                        
                        # If this is the final event, set session ID
                        if hasattr(status_event, "final") and status_event.final:
                            complete_response["session_id"] = getattr(status_event, "sessionId", session_id)
                        
                    elif hasattr(event.result, "artifact"):
                        # It's a TaskArtifactUpdateEvent
                        artifact_event = event.result
                        
                        # Extract artifact content
                        artifact_data = {
                            "name": artifact_event.artifact.name if hasattr(artifact_event.artifact, "name") else "unnamed",
                            "contents": [],
                        }
                        
                        for part in artifact_event.artifact.parts:
                            if part.type == "text":
                                artifact_data["contents"].append({
                                    "type": "text",
                                    "text": part.text,
                                })
                            elif part.type == "data":
                                artifact_data["contents"].append({
                                    "type": "data",
                                    "data": part.data,
                                })
                        
                        complete_response["artifacts"].append(artifact_data)
                        
                        if ctx:
                            await ctx.info(f"Received artifact: {artifact_data['name']}")
                else:
                    # Unknown event type, try to extract what we can
                    complete_response["unknown_events"] = complete_response.get("unknown_events", []) + [
                        {
                            "type": str(type(event)),
                            "dir": str(dir(event))
                        }
                    ]
            
            # Include debug info            
            complete_response["_debug_info"] = {
                "all_events": all_events
            }
            
            return complete_response
        except Exception as e:
            return {
                "status": "error",
                "message": f"Error processing stream events: {str(e)}",
                "_debug_info": {
                    "all_events": all_events
                }
            }
            
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error sending message (stream): {str(e)}",
        }


class CustomA2AServer:
    """
    A minimal A2A server implementation that uses the task manager.
    """
    
    def __init__(
        self,
        agent_card: AgentCard,
        task_manager: A2ABridgeTaskManager,
        host: str = "0.0.0.0",
        port: int = 41241,
    ):
        self.agent_card = agent_card
        self.task_manager = task_manager
        self.host = host
        self.port = port
        
    async def start_async(self):
        """Start the A2A server asynchronously."""
        # In a real implementation, this would start a FastAPI server
        # For now, just log that it's "started"
        print(f"A2A server 'started' at {self.host}:{self.port}")
        # Keep the server "running"
        while True:
            await asyncio.sleep(3600)  # Sleep for an hour
    
    def start(self):
        """Start the A2A server."""
        asyncio.create_task(self.start_async())


def setup_a2a_server():
    """Set up the A2A server with our task manager."""
    # Create a sample agent card
    agent_card = AgentCard(
        name="MCP Bridge Agent",
        description="A bridge between MCP and A2A protocols",
        url=f"http://{A2A_HOST}:{A2A_PORT}",
        version="0.1.0",
        # Add the required capabilities field
        capabilities=AgentCapabilities(
            streaming=True,
            pushNotifications=False,
            stateTransitionHistory=False,
        ),
        # Add the required skills field with at least one skill
        skills=[
            AgentSkill(
                id="mcp-bridge",
                name="MCP Bridge",
                description="Allows MCP clients to communicate with A2A agents",
                tags=["bridge", "proxy", "mcp", "a2a"],
                examples=[
                    "Send a message to an A2A agent",
                    "Register an A2A agent with the bridge",
                    "List all registered A2A agents",
                ],
                inputModes=["text/plain"],
                outputModes=["text/plain", "application/json"],
            )
        ],
    )
    
    # Create our custom task manager
    task_manager = A2ABridgeTaskManager()
    
    # Create and return the A2A server
    return CustomA2AServer(
        agent_card=agent_card,
        task_manager=task_manager,
        host=A2A_HOST,
        port=A2A_PORT,
    )


async def main_async():
    """
    Main async function to start both the MCP and A2A servers.
    """
    # Set up and start the A2A server
    a2a_server = setup_a2a_server()
    a2a_task = asyncio.create_task(a2a_server.start_async())
    
    # Start the MCP server with the configured transport
    print(f"Starting MCP server with {MCP_TRANSPORT} transport...")
    
    if MCP_TRANSPORT == "stdio":
        # Use stdio transport (default)
        mcp_task = asyncio.create_task(
            mcp.run_async(transport="stdio")
        )
    elif MCP_TRANSPORT == "streamable-http":
        # Use streamable-http transport
        mcp_task = asyncio.create_task(
            mcp.run_async(
                transport="streamable-http",
                host=MCP_HOST,
                port=MCP_PORT,
                path=MCP_PATH,
            )
        )
    elif MCP_TRANSPORT == "sse":
        # Use sse transport (deprecated but still supported)
        mcp_task = asyncio.create_task(
            mcp.run_async(
                transport="sse",
                host=MCP_HOST,
                port=MCP_PORT,
                path=MCP_SSE_PATH,
            )
        )
    
    # Run both servers
    await asyncio.gather(a2a_task, mcp_task)


def main():
    """
    Main entry point.
    """
    # Print the configuration for debugging
    print(f"MCP Bridge Configuration:")
    print(f"- Transport: {MCP_TRANSPORT}")
    print(f"- Host: {MCP_HOST}")
    print(f"- Port: {MCP_PORT}")
    if MCP_TRANSPORT == "streamable-http":
        print(f"- Path: {MCP_PATH}")
    elif MCP_TRANSPORT == "sse":
        print(f"- SSE Path: {MCP_SSE_PATH}")
    print(f"A2A Server Configuration:")
    print(f"- Host: {A2A_HOST}")
    print(f"- Port: {A2A_PORT}")
    
    asyncio.run(main_async())


if __name__ == "__main__":
    main()