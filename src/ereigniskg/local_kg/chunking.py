import hashlib
import re
from typing import Callable
import tiktoken

from .models import PageRecord, Chunk

# da ne bismo svaki put pravili novi encoder
def count(encoder: tiktoken.Encoding, text: str) -> int:
    return len(encoder.encode(text, disallowed_special=()))

def make_token_counter(encoding_name: str = "cl100k_base"):
    try:
        encoder = tiktoken.get_encoding(encoding_name)
    except (ValueError, KeyError) as exc:
        raise ValueError(f"Unknown tiktoken encoding: {encoding_name}") from exc

    return lambda text: count(encoder, text)

def _clean_page_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    lines = text.splitlines()
    if lines and lines[-1].strip() == "Ereignis":
        lines.pop()
        text = "\n".join(lines).rstrip()
    return text

def _split_oversized_block(block: str, max_tokens: int, token_count: Callable[[str], int]) -> list[str]:
    if token_count(block) <= max_tokens:
        return [block]

    sentences = [part.strip() for part in re.split(r"(?<=[.!?;·])\s+", block) if part.strip()]
    if len(sentences) > 1:
        result: list[str] = []
        for sentence in sentences:
            result.extend(_split_oversized_block(sentence, max_tokens, token_count))
        return result

    words = re.findall(r"\S+\s*", block)
    if len(words) > 1:
        result: list[str] = []
        current = ""
        for word in words:
            candidate = current + word
            if current and token_count(candidate) > max_tokens:
                result.append(current.strip())
                current = word
            else:
                current = candidate
            if token_count(current) > max_tokens:
                result.extend(_split_by_characters(current, max_tokens, token_count))
                current = ""
        if current.strip():
            result.append(current.strip())
        return result

    return _split_by_characters(block, max_tokens, token_count)

def _split_by_characters(text: str, max_tokens: int, token_count: Callable[[str], int]) -> list[str]:
    pieces: list[str] = []
    start = 0
    while start < len(text):
        low, high = start + 1, len(text)
        best = low
        while low <= high:
            mid = (low + high) // 2
            if token_count(text[start:mid]) <= max_tokens:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        if best <= start:
            best = min(start + 1, len(text))
        if best < len(text):
            boundary = text.rfind(" ", start, best)
            if boundary > start + 1 and token_count(text[start:boundary]) <= max_tokens:
                best = boundary
        piece = text[start:best].strip()
        if piece:
            pieces.append(piece)
        start = best
        while start < len(text) and text[start].isspace():
            start += 1
    return pieces


def _suffix_within_budget(text: str, budget: int, token_count: Callable[[str], int]) -> str:
    if budget <= 0 or not text:
        return ""
    if token_count(text) <= budget:
        return text
    words = text.split()
    suffix: list[str] = []
    for word in reversed(words):
        candidate = " ".join([word] + suffix)
        if token_count(candidate) > budget:
            break
        suffix.insert(0, word)
    return " ".join(suffix)

def _overlap_suffix(units: list[str], overlap_tokens: int, token_count: Callable[[str], int]) -> list[str]:
    if overlap_tokens <= 0:
        return []
    suffix: list[str] = []
    used = 0
    for unit in reversed(units):
        remaining = overlap_tokens - used
        if remaining <= 0:
            break
        unit_tokens = token_count(unit)
        if unit_tokens <= remaining:
            suffix.insert(0, unit)
            used += unit_tokens
        else:
            truncated_unit = _suffix_within_budget(unit, remaining, token_count)
            if truncated_unit:
                suffix.insert(0, truncated_unit)
            break
    return suffix

# funkcija koja ce praviti chunkove po stranicama dokumenta
def chunk_pages(
    pages: list[dict],
    max_tokens: int = 700,
    overlap_tokens: int = 100,
    encoding_name: str = "cl100k_base",
    min_chars: int = 1,
) -> list[Chunk]:
    all_chunks: list[Chunk] = []
    for page in pages:
        if len(page["text"].strip()) < min_chars:
            continue
        page_chunks = chunk_page(
            page,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
            encoding_name=encoding_name,
        )
        all_chunks.extend(page_chunks)
    return all_chunks

def chunk_page(
    page: PageRecord,
    max_tokens: int = 700,
    overlap_tokens: int = 100,
    encoding_name: str = "cl100k_base",
) -> list[Chunk]:
    if max_tokens <= 50:
        raise ValueError("max_tokens must be greater than 50")
    if overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be non-negative and less than max_tokens")

    count_tokens = make_token_counter(encoding_name)
    cleaned = _clean_page_text(page.text)
    if not cleaned:
        return []

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", cleaned) if part.strip()]
    units: list[str] = []
    for paragraph in paragraphs:
        units.extend(_split_oversized_block(paragraph, max_tokens, count_tokens))

    chunks_text: list[str] = []
    current: list[str] = []
    for unit in units:
        candidate = "\n\n".join(current + [unit])
        if current and count_tokens(candidate) > max_tokens:
            chunks_text.append("\n\n".join(current).strip())
            current = _overlap_suffix(current, overlap_tokens, count_tokens)
            while current and count_tokens("\n\n".join(current + [unit])) > max_tokens:
                current.pop(0)
        current.append(unit)

    if current:
        final_text = "\n\n".join(current).strip()
        if final_text:
            chunks_text.append(final_text)

    result: list[Chunk] = []
    for index, text in enumerate(text):
        id = hashlib.sha256(f"{page.url}\x1f{index}\x1f{text}".encode("utf-8")).hexdigest()[:24]
        result.append(
            Chunk(
                chunk_id=id,
                page_url=page.url,
                page_title=page.title,
                page_type=page.type,
                headings=page.headings,
                chunk_index=index,
                text=text,
                token_count=count_tokens(text),
            )
        )

    return result