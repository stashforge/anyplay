import re


class MetadataRecognizer:
    BRACKET_REGEX = re.compile(r'\[([^\]]+)\]|\(([^\)]+)\)')
    TRAILING_LANG_REGEX = re.compile(r'(?i)\s+(english\s+sub(?:bed)?|eng\s+sub|sub(?:bed)?|english\s+dub(?:bed)?|eng\s+dub|dub(?:bed)?|dual\s+audio)\s*$')
    FORMAT_REGEX = re.compile(r'(?i)\b(ova|oad|movie|special|specials)\s*$')

    @classmethod
    def extract(cls, raw_title: str) -> tuple[str, list[str]]:
        tags = []
        for match in cls.BRACKET_REGEX.finditer(raw_title):
            tag = match.group(1) or match.group(2)
            if tag:
                tags.append(tag.strip())
        clean_title = cls.BRACKET_REGEX.sub('', raw_title)

        lang_match = cls.TRAILING_LANG_REGEX.search(clean_title)
        if lang_match:
            tag = lang_match.group(1)
            tags.append(tag.strip())
            clean_title = cls.TRAILING_LANG_REGEX.sub('', clean_title)

        format_match = cls.FORMAT_REGEX.search(clean_title)
        if format_match:
            tag = format_match.group(1)
            tags.append(tag.upper())
            clean_title = cls.FORMAT_REGEX.sub('', clean_title)

        clean_title = re.sub(r'\s+', ' ', clean_title).strip()

        return clean_title, tags
