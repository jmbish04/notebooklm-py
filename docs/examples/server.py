#!/usr/bin/env python3
"""FastAPI server wrapper for notebooklm-py.

This server exposes NotebookLM functionality as a REST API, designed for
integration with frontend applications like Cloudflare Workers (Astro).

Architecture:
    - This Python container runs FastAPI with notebooklm-py
    - Frontend calls this API to interact with NotebookLM
    - Authentication is handled via stored browser credentials

Endpoints:
    POST /chat - Send messages to a notebook conversation
    POST /generate - Generate artifacts (audio podcasts, resumes, etc.)
    GET /health - Health check endpoint

Prerequisites:
    pip install "notebooklm-py[browser]" fastapi uvicorn
    playwright install chromium
    notebooklm login  # Authenticate first

Usage:
    uvicorn server:app --host 0.0.0.0 --port 8000

    # Or run directly:
    python server.py

Example requests:
    # Chat with a notebook
    curl -X POST http://localhost:8000/chat \\
        -H "Content-Type: application/json" \\
        -d '{"message": "Why am I a good fit?", "notebook_id": "xxx", "conversation_id": null}'

    # Generate an audio podcast
    curl -X POST http://localhost:8000/generate \\
        -H "Content-Type: application/json" \\
        -d '{"type": "audio", "notebook_id": "xxx", "prompt": "Create a debate about my weaknesses"}'
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# FastAPI imports
try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
except ImportError as e:
    raise ImportError(
        "FastAPI is required for the server. Install with: pip install fastapi uvicorn"
    ) from e

# notebooklm-py imports
from notebooklm import (
    ArtifactNotFoundError,
    ArtifactNotReadyError,
    AuthError,
    ChatError,
    NetworkError,
    NotebookLMClient,
    NotebookNotFoundError,
    RPCError,
    SourceAddError,
    ValidationError,
)
from notebooklm.types import AudioFormat

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# Request/Response Models
# =============================================================================


class ChatRequest(BaseModel):
    """Request model for chat endpoint."""

    message: str = Field(..., description="The message/question to send")
    notebook_id: str = Field(..., description="The NotebookLM notebook ID")
    conversation_id: str | None = Field(None, description="Existing conversation ID for follow-ups")
    source_ids: list[str] | None = Field(
        None, description="Specific source IDs to query (optional)"
    )


class ChatResponse(BaseModel):
    """Response model for chat endpoint."""

    answer: str = Field(..., description="The AI response")
    conversation_id: str = Field(..., description="Conversation ID for follow-ups")
    turn_number: int = Field(..., description="Turn number in conversation")
    is_follow_up: bool = Field(..., description="Whether this was a follow-up")


class GenerationType(str, Enum):
    """Supported generation types."""

    AUDIO = "audio"
    RESUME = "resume"
    REPORT = "report"


class GenerateRequest(BaseModel):
    """Request model for generate endpoint."""

    type: GenerationType = Field(..., description="Type of content to generate")
    notebook_id: str = Field(..., description="The NotebookLM notebook ID")
    prompt: str | None = Field(None, description="Custom instructions/prompt")
    source_ids: list[str] | None = Field(None, description="Specific source IDs to use (optional)")
    wait: bool = Field(True, description="Wait for generation to complete (default: True)")
    timeout: int = Field(300, description="Timeout in seconds when waiting")
    poll_interval: int = Field(10, gt=0, description="Polling interval in seconds when waiting")
    audio_format: str | None = Field(
        None, description="Audio format: deep_dive, brief, critique, debate"
    )


class GenerateResponse(BaseModel):
    """Response model for generate endpoint."""

    type: str = Field(..., description="Type of content generated")
    status: str = Field(..., description="Generation status")
    task_id: str | None = Field(None, description="Task ID for polling")
    content: str | None = Field(None, description="Generated content (for text types like resume)")
    url: str | None = Field(None, description="URL for media content (audio/video)")
    is_complete: bool = Field(..., description="Whether generation is complete")


class HealthResponse(BaseModel):
    """Response model for health check."""

    status: str = Field(..., description="Service status")
    authenticated: bool = Field(..., description="Whether client is authenticated")
    version: str = Field(..., description="notebooklm-py version")


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str = Field(..., description="Error type")
    message: str = Field(..., description="Error message")
    details: dict[str, Any] | None = Field(None, description="Additional details")


# =============================================================================
# Global State
# =============================================================================

# Client instance (managed by lifespan)
_client: NotebookLMClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage client lifecycle."""
    global _client
    logger.info("Starting server, initializing NotebookLM client...")

    try:
        _client = await NotebookLMClient.from_storage()
        await _client.__aenter__()
        logger.info("NotebookLM client initialized successfully")
    except FileNotFoundError:
        logger.error("Authentication not found. Run 'notebooklm login' first to authenticate.")
        _client = None
    except Exception as e:
        logger.error(f"Failed to initialize client: {e}")
        _client = None

    yield

    # Cleanup
    if _client:
        logger.info("Shutting down, closing NotebookLM client...")
        await _client.__aexit__(None, None, None)
        _client = None


# =============================================================================
# FastAPI App
# =============================================================================

app = FastAPI(
    title="NotebookLM API Server",
    description="REST API wrapper for Google NotebookLM using notebooklm-py",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration for frontend integration
# Configure via CORS_ORIGINS environment variable for production.
# Set to comma-separated list of allowed origins (e.g., "https://example.com,https://api.example.com")
# Defaults to "*" for development only.
_cors_origins = [origin.strip() for origin in os.environ.get("CORS_ORIGINS", "*").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_client() -> NotebookLMClient:
    """Get the NotebookLM client, raising error if not available."""
    if _client is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "ServiceUnavailable",
                "message": "NotebookLM client not initialized. Run 'notebooklm login' to authenticate.",
            },
        )
    return _client


# =============================================================================
# Exception Handlers
# =============================================================================


@app.exception_handler(NotebookNotFoundError)
async def notebook_not_found_handler(request: Request, exc: NotebookNotFoundError):
    """Handle NotebookNotFoundError exceptions."""
    return JSONResponse(
        status_code=404,
        content={"error": "NotebookNotFound", "message": str(exc)},
    )


@app.exception_handler(ChatError)
async def chat_error_handler(request: Request, exc: ChatError):
    """Handle ChatError exceptions."""
    return JSONResponse(
        status_code=400,
        content={"error": "ChatError", "message": str(exc)},
    )


@app.exception_handler(NetworkError)
async def network_error_handler(request: Request, exc: NetworkError):
    """Handle NetworkError exceptions."""
    return JSONResponse(
        status_code=502,
        content={"error": "NetworkError", "message": str(exc)},
    )


@app.exception_handler(AuthError)
async def auth_error_handler(request: Request, exc: AuthError):
    """Handle AuthError exceptions."""
    return JSONResponse(
        status_code=401,
        content={
            "error": "AuthError",
            "message": str(exc),
            "details": {"action": "Run 'notebooklm login' to re-authenticate"},
        },
    )


@app.exception_handler(RPCError)
async def rpc_error_handler(request: Request, exc: RPCError):
    """Handle RPCError exceptions."""
    return JSONResponse(
        status_code=500,
        content={"error": "RPCError", "message": str(exc)},
    )


@app.exception_handler(SourceAddError)
async def source_add_error_handler(request: Request, exc: SourceAddError):
    """Handle SourceAddError exceptions."""
    return JSONResponse(
        status_code=400,
        content={"error": "SourceError", "message": str(exc)},
    )


@app.exception_handler(ValidationError)
async def validation_error_handler(request: Request, exc: ValidationError):
    """Handle ValidationError exceptions."""
    return JSONResponse(
        status_code=400,
        content={"error": "ValidationError", "message": str(exc)},
    )


@app.exception_handler(ArtifactNotReadyError)
async def artifact_not_ready_handler(request: Request, exc: ArtifactNotReadyError):
    """Handle ArtifactNotReadyError exceptions."""
    return JSONResponse(
        status_code=202,
        content={
            "error": "ArtifactNotReady",
            "message": str(exc),
            "details": {"status": "Generation still in progress"},
        },
    )


@app.exception_handler(ArtifactNotFoundError)
async def artifact_not_found_handler(request: Request, exc: ArtifactNotFoundError):
    """Handle ArtifactNotFoundError exceptions."""
    return JSONResponse(
        status_code=404,
        content={"error": "ArtifactNotFound", "message": str(exc)},
    )


# =============================================================================
# Endpoints
# =============================================================================


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    from notebooklm import __version__

    return HealthResponse(
        status="healthy" if _client else "degraded",
        authenticated=_client is not None,
        version=__version__,
    )


@app.post("/chat", response_model=ChatResponse, responses={400: {"model": ErrorResponse}})
async def chat(request: ChatRequest) -> ChatResponse:
    """Send a message to a NotebookLM notebook.

    This endpoint allows chatting with notebook content. For follow-up
    questions, include the conversation_id from a previous response.

    Example:
        POST /chat
        {
            "message": "What are the key themes?",
            "notebook_id": "abc123",
            "conversation_id": null
        }

    For follow-ups:
        POST /chat
        {
            "message": "Can you elaborate on the first point?",
            "notebook_id": "abc123",
            "conversation_id": "conv-xyz"
        }
    """
    client = get_client()

    result = await client.chat.ask(
        notebook_id=request.notebook_id,
        question=request.message,
        source_ids=request.source_ids,
        conversation_id=request.conversation_id,
    )

    return ChatResponse(
        answer=result.answer,
        conversation_id=result.conversation_id,
        turn_number=result.turn_number,
        is_follow_up=result.is_follow_up,
    )


@app.post("/generate", response_model=GenerateResponse, responses={400: {"model": ErrorResponse}})
async def generate(request: GenerateRequest) -> GenerateResponse:
    """Generate content from a NotebookLM notebook.

    Supported types:
    - audio: Generate an Audio Overview (podcast)
    - resume: Generate a resume/briefing document
    - report: Generate a detailed report

    Example (audio):
        POST /generate
        {
            "type": "audio",
            "notebook_id": "abc123",
            "prompt": "Create an engaging debate about my weaknesses",
            "audio_format": "debate"
        }

    Example (resume/report):
        POST /generate
        {
            "type": "resume",
            "notebook_id": "abc123",
            "prompt": "Focus on leadership experience"
        }
    """
    client = get_client()

    if request.type == GenerationType.AUDIO:
        return await _generate_audio(client, request)
    elif request.type in (GenerationType.RESUME, GenerationType.REPORT):
        return await _generate_report(client, request)
    else:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "InvalidType",
                "message": f"Unsupported generation type: {request.type}",
            },
        )


async def _generate_audio(client: NotebookLMClient, request: GenerateRequest) -> GenerateResponse:
    """Generate an audio podcast."""
    # Parse audio format if provided
    audio_format = None
    if request.audio_format:
        format_map = {
            "deep_dive": AudioFormat.DEEP_DIVE,
            "brief": AudioFormat.BRIEF,
            "critique": AudioFormat.CRITIQUE,
            "debate": AudioFormat.DEBATE,
        }
        audio_format = format_map.get(request.audio_format.lower())
        if audio_format is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "InvalidAudioFormat",
                    "message": f"Invalid audio format: {request.audio_format}. "
                    f"Valid options: {', '.join(format_map.keys())}",
                },
            )

    # Start generation
    status = await client.artifacts.generate_audio(
        notebook_id=request.notebook_id,
        source_ids=request.source_ids,
        instructions=request.prompt,
        audio_format=audio_format,
    )

    logger.info(f"Started audio generation, task_id: {status.task_id}")

    # Wait for completion if requested
    if request.wait:
        final_status = await client.artifacts.wait_for_completion(
            notebook_id=request.notebook_id,
            task_id=status.task_id,
            timeout=request.timeout,
            poll_interval=request.poll_interval,
        )
        return GenerateResponse(
            type="audio",
            status=final_status.status,
            task_id=status.task_id,
            content=None,
            url=final_status.url,
            is_complete=final_status.is_complete,
        )

    return GenerateResponse(
        type="audio",
        status=status.status,
        task_id=status.task_id,
        content=None,
        url=None,
        is_complete=False,
    )


async def _generate_report(client: NotebookLMClient, request: GenerateRequest) -> GenerateResponse:
    """Generate a text report (resume, briefing, etc.)."""
    from notebooklm.types import ReportFormat

    # Determine report format based on type
    report_format = (
        ReportFormat.BRIEFING_DOC
        if request.type == GenerationType.RESUME
        else ReportFormat.BLOG_POST
    )

    # Start generation
    status = await client.artifacts.generate_report(
        notebook_id=request.notebook_id,
        source_ids=request.source_ids,
        report_format=report_format,
        custom_prompt=request.prompt,
    )

    logger.info(f"Started report generation, task_id: {status.task_id}")

    # Wait for completion if requested
    if request.wait:
        final_status = await client.artifacts.wait_for_completion(
            notebook_id=request.notebook_id,
            task_id=status.task_id,
            timeout=request.timeout,
            poll_interval=request.poll_interval,
        )

        # Try to get the report content
        content = None
        if final_status.is_complete:
            try:
                artifacts = await client.artifacts.list(request.notebook_id)
                # Find the most recent report artifact
                report_artifacts = [a for a in artifacts if a.kind == "report"]
                if report_artifacts:
                    # Get content from the most recent one
                    latest = max(
                        report_artifacts,
                        key=lambda a: a.created_at or datetime.min,
                    )
                    content = latest.content
            except Exception as e:
                logger.warning(f"Could not fetch report content: {e}")

        return GenerateResponse(
            type=request.type.value,
            status=final_status.status,
            task_id=status.task_id,
            content=content,
            url=None,
            is_complete=final_status.is_complete,
        )

    return GenerateResponse(
        type=request.type.value,
        status=status.status,
        task_id=status.task_id,
        content=None,
        url=None,
        is_complete=False,
    )


# =============================================================================
# Server Entry Point
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
