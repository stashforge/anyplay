import re

# We use a list of specific rules so we can easily scale to 100+ patterns
# without creating a massive, unreadable monolithic regex.

EPISODE_PATTERNS = [
    # Standard prefixes: - 12, Ep 12, Episode 03
    re.compile(r'(?i)(?:-\s*|\b(?:ep|episode|e)\s*|\[)(\d+(?:\.\d+)?)(?:\])?\b'),
    # Specials and OVAs: SP1, OVA 2
    re.compile(r'(?i)\b(?:sp|ova|oad|ncop|nced)\s*(\d+(?:\.\d+)?)\b'),
    # Batch ranges: 01-12 (Extracts the first number or flags it as batch)
    re.compile(r'(?i)\b(?:batch|complete)\b.*?(?:-|\b)(\d+)(?:\.\d+)?\b'),
    # Version tags (we want to strip these but they aren't episodes): v2, v3
    re.compile(r'(?i)\bv\d+\b'),
]

SEASON_PATTERNS = [
    # Standard: Season 2, S02, 2nd Season
    re.compile(r'(?i)\b(?:s|season)\s*0*(\d+)\b'),
    re.compile(r'(?i)\b0*(\d+)(?:st|nd|rd|th)\s+season\b'),
    # Roman Numerals at the end of the string
    re.compile(r'\s+(II|III|IV|V|VI|VII|VIII|IX|X|Ⅱ|Ⅲ|Ⅳ|Ⅴ|Ⅵ|Ⅶ|Ⅷ|Ⅸ|Ⅹ)\b'),
    # Bare numbers < 20 at the end of the string (Donghua style)
    re.compile(r'(?<!-)\s+0*([1-9]|1[0-9])\s*$')
]

PART_PATTERNS = [
    # Parts and Cours: Part 2, Cour 2, Pt. 2
    re.compile(r'(?i)\b(?:part|pt\.?|cour)\s*0*(\d+)\b')
]
