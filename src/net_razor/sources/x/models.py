from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class Engagement(BaseModel):
    likes: int = 0
    reposts: int = 0
    replies: int = 0
    quotes: int = 0
    views: int = 0


class SearchItem(BaseModel):
    id: str
    url: str
    text: str
    created_at: datetime
    author_handle: str
    author_name: str
    engagement: Engagement
