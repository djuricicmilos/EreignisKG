from pydantic import BaseModel, ConfigDict, Field
from dataclasses import dataclass
from typing import Literal

EntityType = Literal[
    "PERSON",
    "WORK",
    "CONCEPT",
    "PLACE",
    "ORGANIZATION",
    "EVENT",
    "HISTORICAL_PERIOD",
    "OTHER",
]

RelationType = Literal[
    "AUTHORED",
    "EDITED",
    "TRANSLATED",
    "PART_OF",
    "DISCUSSES",
    "DEFINES",
    "INFLUENCED",
    "CRITIQUES",
    "CONTRASTS_WITH",
    "REFERENCES",
    "ASSOCIATED_WITH",
    "LOCATED_IN",
    "PARTICIPATED_IN",
    "OTHER",
]

@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    page_url: str
    page_title: str
    page_type: str
    headings: list[str]
    chunk_index: int
    text: str
    token_count: int

class PageRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    url: str
    page_type: str = "unknown"
    local_path: str | None = None
    format: str = "html"
    title: str = ""
    headings: list[str] = Field(default_factory=list)
    text: str = ""
    encoding: str = "utf-8"
    n_chars: int | None = None