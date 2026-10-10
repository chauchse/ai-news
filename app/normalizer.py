import re
from bs4 import BeautifulSoup


def normalize_text(raw_text: str | None) -> str:
    """Convertit un contenu HTML en texte brut normalisé."""

    if not raw_text:
        return ""

    soup = BeautifulSoup(raw_text, "html.parser")

    for tag in soup(["script", "style", "iframe"]):
        tag.decompose()

    text = soup.get_text(separator=" ", strip=True)

    # Normaliser les espaces et retours à la ligne
    text = re.sub(r"\s+", " ", text)

    return text.strip()
