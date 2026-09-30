"""FZF-style fuzzy matching, scoring, and text highlighting for TUI pickers."""

from __future__ import annotations

# Separator characters that define word boundaries
_BOUNDARY_CHARS = frozenset(" \t\n\r-_:./\\()[]{}'\",+*~`!?<>|#@$%^&=")


def _is_word_boundary(text: str, idx: int) -> bool:
    if idx == 0:
        return True
    prev = text[idx - 1]
    curr = text[idx]
    if prev in _BOUNDARY_CHARS:
        return True
    # CamelCase boundary (lowercase followed by uppercase)
    if prev.islower() and curr.isupper():
        return True
    return False


def _fuzzy_match_single(term: str, text: str) -> tuple[int, list[int]] | None:
    """Match a single search term against target text with FZF-style scoring.

    Returns (score, matched_indices) or None if no match.
    """
    if not term:
        return 0, []

    t_len = len(term)
    text_len = len(text)
    if t_len > text_len:
        return None

    term_lower = term.lower()
    text_lower = text.lower()

    # Fast path: exact substring match
    sub_idx = text_lower.find(term_lower)
    if sub_idx != -1:
        indices = list(range(sub_idx, sub_idx + t_len))
        # Base score for exact match
        score = 1000 + (t_len * 50)
        # Bonus for matching at start of text
        if sub_idx == 0:
            score += 300
        # Bonus for word boundary
        elif _is_word_boundary(text, sub_idx):
            score += 200
        # Full exact match bonus
        if t_len == text_len:
            score += 500
        return score, indices

    # Subsequence search with best-path scoring
    matches = []
    text_idx = 0
    for ch in term_lower:
        found = text_lower.find(ch, text_idx)
        if found == -1:
            return None
        matches.append(found)
        text_idx = found + 1

    # Backtrack to find tightest match span
    last_term_idx = t_len - 1
    rev_matches = [0] * t_len
    text_idx = text_len - 1
    possible = True
    for i in range(last_term_idx, -1, -1):
        ch = term_lower[i]
        found = text_lower.rfind(ch, 0, text_idx + 1)
        if found == -1:
            possible = False
            break
        rev_matches[i] = found
        text_idx = found - 1

    if possible:
        span_fwd = matches[-1] - matches[0]
        span_rev = rev_matches[-1] - rev_matches[0]
        chosen_matches = rev_matches if span_rev < span_fwd else matches
    else:
        chosen_matches = matches

    score = 100
    prev_idx = -1
    consecutive = 0

    for i, idx in enumerate(chosen_matches):
        char_score = 10

        # Word boundary bonus
        if _is_word_boundary(text, idx):
            char_score += 80

        # Consecutive character bonus
        if prev_idx != -1 and idx == prev_idx + 1:
            consecutive += 1
            char_score += 40 + (consecutive * 20)
        else:
            consecutive = 0
            if prev_idx != -1:
                gap = idx - prev_idx - 1
                char_score -= min(30, gap * 3)

        score += char_score
        prev_idx = idx

    total_span = chosen_matches[-1] - chosen_matches[0] + 1
    if total_span == t_len:
        score += 150
    else:
        score += max(0, 100 - (total_span - t_len) * 5)

    score -= min(50, chosen_matches[0] * 2)
    return score, chosen_matches


def fuzzy_match(query: str, text: str) -> tuple[int, set[int]] | None:
    """Match multi-term query against text.

    All terms in query must match. Returns (combined_score, matched_indices_set)
    or None if any term fails to match.
    """
    if not query:
        return 0, set()

    terms = query.strip().split()
    if not terms:
        return 0, set()

    all_indices: set[int] = set()
    total_score = 0

    for term in terms:
        res = _fuzzy_match_single(term, text)
        if res is None:
            return None
        term_score, term_indices = res
        total_score += term_score
        all_indices.update(term_indices)

    return total_score, all_indices


def fuzzy_highlight(
    text: str,
    matched_indices: set[int],
    base_style: str = "",
    match_style: str = "\033[38;2;137;180;250m\033[1m",  # Blue bold highlight
    reset_style: str = "\033[0m",
) -> str:
    """Render text with matched character indices highlighted."""
    if not matched_indices:
        return f"{base_style}{text}{reset_style}"

    chunks = []
    in_highlight = False

    for idx, ch in enumerate(text):
        is_match = idx in matched_indices
        if is_match and not in_highlight:
            chunks.append(f"{reset_style}{match_style}")
            in_highlight = True
        elif not is_match and in_highlight:
            chunks.append(f"{reset_style}{base_style}")
            in_highlight = False
        chunks.append(ch)

    chunks.append(reset_style)
    return f"{base_style}{''.join(chunks)}"
