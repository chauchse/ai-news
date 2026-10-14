import re
from project_config import load_section


class CyberGlossary:
    def __init__(self, config_path=None):
        config = load_section('glossary', config_path)

        self.terms = sorted(
            set(config.get("protected_terms", [])),
            key=len,
            reverse=True,
        )

        self.translations = config.get("translations", {}).get("en-fr", {})
        self.case_sensitive_terms = set(config.get("case_sensitive_terms", []))

        self.patterns = [
            r"\bCVE-\d{4}-\d{4,}\b",
            r"\bCWE-\d+\b",
            r"\bGHSA-[a-zA-Z0-9-]+\b",
            r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
            r"\b(?:v?\d+\.)+\d+(?:[-+][\w.-]+)?\b",
        ]

    def protect(self, text):
        matches = []

        for term in self.terms:
            pattern = re.compile(
                rf"(?<!\w){re.escape(term)}(?!\w)",
                0 if term in self.case_sensitive_terms else re.IGNORECASE,
            )

            for match in pattern.finditer(text):
                matches.append((match.start(), match.end()))

        for pattern in self.patterns:
            for match in re.finditer(pattern, text):
                matches.append((match.start(), match.end()))

        # Retirer les chevauchements, en privilégiant
        # les correspondances les plus longues.
        matches.sort(key=lambda x: (x[0], -(x[1] - x[0])))

        selected = []
        last_end = -1

        for start, end in matches:
            if start >= last_end:
                selected.append((start, end))
                last_end = end

        protected = {}
        result = text

        for index, (start, end) in reversed(list(enumerate(selected))):
            token = f"ZXQTERM{index}QXZ"
            protected[token] = text[start:end]
            result = result[:start] + token + result[end:]

        return result, protected

    def restore(self, text, protected):
        for token, original in protected.items():
            text = text.replace(token, original)

        return text
