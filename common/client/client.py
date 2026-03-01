import json

from collections.abc import AsyncIterable
from typing import Any

import httpx

from httpx._types import TimeoutTypes
from httpx_sse import connect_sse

from common.types import (
    A2AClientHTTPError,
    A2AClientJSONError,
    AgentCard,
    CancelTaskRequest,
    CancelTaskResponse,
    GetTaskPushNotificationRequest,
    GetTaskPushNotificationResponse,
    GetTaskRequest,
    GetTaskResponse,
    JSONRPCRequest,
    MessageSendRequest,
    MessageSendResponse,
    MessageStreamRequest,
    MessageStreamResponse,
    SendTaskRequest,
    SendTaskResponse,
    SendTaskStreamingRequest,
    SendTaskStreamingResponse,
    SetTaskPushNotificationRequest,
    SetTaskPushNotificationResponse,
)


class A2AClient:
    def __init__(
        self,
        agent_card: AgentCard = None,
        url: str = None,
        timeout: TimeoutTypes = 60.0,
    ):
        if agent_card:
            self.url = agent_card.url
        elif url:
            self.url = url
        else:
            raise ValueError('Must provide either agent_card or url')
        self.timeout = timeout

    async def send_task(self, payload: dict[str, Any]) -> SendTaskResponse:
        request = SendTaskRequest(params=payload)
        return SendTaskResponse(**await self._send_request(request))

    async def send_task_streaming(
        self, payload: dict[str, Any]
    ) -> AsyncIterable[SendTaskStreamingResponse]:
        request = SendTaskStreamingRequest(params=payload)
        with httpx.Client(timeout=None) as client:
            with connect_sse(
                client, 'POST', self.url, json=request.model_dump()
            ) as event_source:
                try:
                    for sse in event_source.iter_sse():
                        yield SendTaskStreamingResponse(**json.loads(sse.data))
                except json.JSONDecodeError as e:
                    raise A2AClientJSONError(str(e)) from e
                except httpx.RequestError as e:
                    raise A2AClientHTTPError(400, str(e)) from e

    async def send_message(self, payload: dict[str, Any]) -> MessageSendResponse:
        """Send a message using A2A v0.3+ message/send method."""
        request = MessageSendRequest(params=payload)
        return MessageSendResponse(
            **await self._send_request(request, exclude_none=True)
        )

    async def send_message_streaming(
        self, payload: dict[str, Any]
    ) -> AsyncIterable[MessageStreamResponse]:
        """Stream responses using A2A v0.3+ message/stream method."""
        request = MessageStreamRequest(params=payload)
        with httpx.Client(timeout=None) as client:
            with connect_sse(
                client,
                'POST',
                self.url,
                json=request.model_dump(exclude_none=True),
            ) as event_source:
                try:
                    for sse in event_source.iter_sse():
                        yield MessageStreamResponse(**json.loads(sse.data))
                except json.JSONDecodeError as e:
                    raise A2AClientJSONError(str(e)) from e
                except httpx.RequestError as e:
                    raise A2AClientHTTPError(400, str(e)) from e

    async def _send_request(
        self, request: JSONRPCRequest, exclude_none: bool = False
    ) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            try:
                # Image generation could take time, adding timeout
                response = await client.post(
                    self.url,
                    json=request.model_dump(exclude_none=exclude_none),
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                raise A2AClientHTTPError(e.response.status_code, str(e)) from e
            except json.JSONDecodeError as e:
                raise A2AClientJSONError(str(e)) from e

    async def get_task(self, payload: dict[str, Any]) -> GetTaskResponse:
        request = GetTaskRequest(params=payload)
        return GetTaskResponse(**await self._send_request(request))

    async def cancel_task(self, payload: dict[str, Any]) -> CancelTaskResponse:
        request = CancelTaskRequest(params=payload)
        return CancelTaskResponse(**await self._send_request(request))

    async def set_task_callback(
        self, payload: dict[str, Any]
    ) -> SetTaskPushNotificationResponse:
        request = SetTaskPushNotificationRequest(params=payload)
        return SetTaskPushNotificationResponse(
            **await self._send_request(request)
        )

    async def get_task_callback(
        self, payload: dict[str, Any]
    ) -> GetTaskPushNotificationResponse:
        request = GetTaskPushNotificationRequest(params=payload)
        return GetTaskPushNotificationResponse(
            **await self._send_request(request)
        )
