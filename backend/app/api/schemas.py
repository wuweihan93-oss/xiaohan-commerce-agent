"""HTTP 请求体模型与输入边界。"""

import uuid

from pydantic import BaseModel, Field


class CustomerRequest(BaseModel):
    customer_id: str


class ConfirmationRequest(BaseModel):
    confirm: bool


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=3000)
    conversation_id: uuid.UUID = Field(default_factory=uuid.uuid4)
