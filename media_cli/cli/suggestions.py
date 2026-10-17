"""Conservative CLI corrections ranked with the picker's fuzzy matcher."""

from ..ui.fuzzy import fuzzy_match


def _typo_distance(left, right):
    rows = [list(range(len(right) + 1))]
    for i, char in enumerate(left, 1):
        row = [i]
        for j, other in enumerate(right, 1):
            value = min(row[j - 1] + 1, rows[-1][j] + 1,
                        rows[-1][j - 1] + (char != other))
            if i > 1 and j > 1 and char == right[j - 2] and left[i - 2] == other:
                value = min(value, rows[-2][j - 2] + 1)
            row.append(value)
        rows.append(row)
    return rows[-1][-1]


def suggest(value, choices):
    query = value.casefold()
    if len(query) < 2:
        return []
    ranked = []
    for choice in sorted(set(choices)):
        candidate = choice.casefold()
        distance = _typo_distance(query, candidate)
        length = max(len(query), len(candidate))
        if distance > (1 if len(query) <= 4 else 2) or distance / length > 0.34:
            continue
        match = fuzzy_match(query, candidate) or fuzzy_match(candidate, query)
        score = match[0] if match else 0
        ranked.append((distance, -score, choice))
    ranked.sort()
    if not ranked:
        return []
    best_distance = ranked[0][0]
    return [choice for distance, _, choice in ranked if distance == best_distance][:3]
