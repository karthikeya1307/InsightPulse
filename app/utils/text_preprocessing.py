"""Text normalisation, language detection, PII detection and multilingual lexicon.

Design notes
------------
* Language detection is script-based for native Telugu (U+0C00-U+0C7F) and
  Devanagari/Hindi (U+0900-U+097F), plus a marker-word lexicon for romanised
  (code-mixed) Hindi and Telugu. It does not try to be a general-purpose
  language identifier.
* The English pipeline uses NLTK's regexp tokeniser and Snowball stemmer (both
  ship with NLTK, so no corpus download is needed) plus negation marking.
* Non-English text is NOT stemmed with English rules; it is only normalised
  (NFC, lower-case for Latin script). Character n-grams in the models carry the
  multilingual signal, and the lexicon below is the documented fallback.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from nltk.stem.snowball import SnowballStemmer
from nltk.tokenize import RegexpTokenizer

_TOKENIZER = RegexpTokenizer(r"\w+|[^\w\s]")
_STEMMER = SnowballStemmer("english")

TELUGU_RE = re.compile(r"[ఀ-౿]")
DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")

ROMAN_HI_MARKERS = frozenset(
    "hai hain tha thi raha rahi bahut bilkul kharab accha acha achha nahi nahin kya aur lekin par "
    "mein main hoon ka ki ke se bekaar pareshani khush mast theek thaak dekhte sakte kiya maine baar".split()
)
ROMAN_TE_MARKERS = frozenset(
    "chala bagundi baledu assalu undi unnanu ayindi parledu gurinchi emi ledu kani inka tho ga "
    "vaadakam avtundi avvatledu vastundi chesanu ippudu lekunda malli sare chuddam cheppalenu nenu".split()
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+91[\s-]?)?(?<!\d)[6-9]\d{9}(?!\d)")

NEGATIONS = frozenset({"not", "no", "never", "cannot", "nobody", "nothing", "n't", "dont", "didnt",
                       "doesnt", "isnt", "wasnt", "wont", "nahi", "nahin", "ledu", "baledu"})
_CLAUSE_END = frozenset(".,;:!?।")


def normalize_text(text: str) -> str:
    """Unicode NFC, collapse whitespace, strip."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "")).strip()


def text_hash(text: str) -> str:
    """Stable SHA-256 of the case-folded normalised text (duplicate detection)."""
    return hashlib.sha256(normalize_text(text).casefold().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LanguageInfo:
    language: str      # en | hi | te
    script: str        # latin | devanagari | telugu
    code_mixed: bool   # romanised Hindi/Telugu (Latin script)


def detect_language(text: str) -> LanguageInfo:
    """Detect en / hi / te using script ranges and romanised marker words."""
    if TELUGU_RE.search(text):
        return LanguageInfo("te", "telugu", False)
    if DEVANAGARI_RE.search(text):
        return LanguageInfo("hi", "devanagari", False)
    tokens = set(re.findall(r"[a-z]+", text.lower()))
    hi, te = len(tokens & ROMAN_HI_MARKERS), len(tokens & ROMAN_TE_MARKERS)
    if max(hi, te) >= 2:
        return LanguageInfo("te" if te > hi else "hi", "latin", True)
    return LanguageInfo("en", "latin", False)


# ---------------------------------------------------------------------------
# PII
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PiiFinding:
    kind: str   # EMAIL | PHONE | NAME
    value: str


def find_pii(text: str, known_names: list[str] | None = None) -> list[PiiFinding]:
    """Find e-mail addresses, Indian phone numbers and (optionally) known full names."""
    out = [PiiFinding("EMAIL", m.group(0)) for m in EMAIL_RE.finditer(text)]
    out += [PiiFinding("PHONE", m.group(0)) for m in PHONE_RE.finditer(text)]
    for name in known_names or []:
        if name and re.search(rf"\b{re.escape(name)}\b", text, flags=re.IGNORECASE):
            out.append(PiiFinding("NAME", name))
    return out


def redact_pii(text: str, known_names: list[str] | None = None) -> str:
    """Replace PII with typed placeholders, e.g. ``[EMAIL]``."""
    text = EMAIL_RE.sub("[EMAIL]", text)
    text = PHONE_RE.sub("[PHONE]", text)
    for name in known_names or []:
        if name:
            text = re.sub(rf"\b{re.escape(name)}\b", "[NAME]", text, flags=re.IGNORECASE)
    return text


def mask_for_model(text: str) -> str:
    """Model-input masking: PII, order numbers and digits become placeholders."""
    text = EMAIL_RE.sub(" emailtoken ", text)
    text = PHONE_RE.sub(" phonetoken ", text)
    text = re.sub(r"\bORD-\d+\b", " ordertoken ", text)
    text = re.sub(r"\bTKT-\d{4}-\d{6}\b", " tickettoken ", text)
    return re.sub(r"\d+", " 0 ", text)


# ---------------------------------------------------------------------------
# Model preprocessing
# ---------------------------------------------------------------------------
def preprocess_for_model(text: str) -> str:
    """Normalise text for TF-IDF features.

    English (Latin script): lower-case, mask, tokenise, Snowball stem, and mark
    tokens after a negation with a ``neg_`` prefix until the clause ends.
    Native-script Hindi/Telugu: normalise + mask only (no English stemming).
    """
    text = normalize_text(mask_for_model(text))
    lang = detect_language(text)
    if lang.script != "latin":
        return text.lower()
    out, negate = [], False
    for tok in _TOKENIZER.tokenize(text.lower()):
        if tok in _CLAUSE_END:
            negate = False
            out.append(tok)
            continue
        stem = tok if lang.code_mixed else _STEMMER.stem(tok)
        out.append(f"neg_{stem}" if negate else stem)
        if tok in NEGATIONS:
            negate = True
    return " ".join(out)


# ---------------------------------------------------------------------------
# Multilingual sentiment lexicon (fallback + data-quality heuristics)
# ---------------------------------------------------------------------------
POS_WORDS = frozenset(
    # English
    "excellent great good love loved amazing superb impressed happy helpful quick fast smooth easy "
    "worth recommend premium perfect polite neat seamless intuitive friendly glad fantastic wonderful "
    "brilliant affordable reasonable resolved appreciate thanks thank best nice instantly "
    # Romanised Hindi / Telugu
    "accha acha achha mast khush badhiya bagundi super happy "
    # Hindi (Devanagari) / Telugu (native)
    "अच्छा अच्छी खुश शानदार బాగుంది సంతోషంగా అద్భుతంగా".split()
)
NEG_WORDS = frozenset(
    "poor terrible awful bad worst broke broken defective faulty disappointed disappointing useless rude "
    "late delayed failed failing crash crashes error damaged crushed leaked torn expensive overpriced "
    "frustrating annoying confusing cluttered never cannot unhappy waste stuck rejected cancelled "
    "flimsy cheap irrelevant problem issue "
    "kharab bekaar pareshani baledu assalu worst problem "
    "खराब निराशा बेकार दिक्कत क्रैश एरर చెడ్డగా నిరాశ బాగాలేదు సమస్య క్రాష్ ఎర్రర్".split()
)


def lexicon_score(text: str) -> tuple[int, int]:
    """Return (positive_hits, negative_hits) using the multilingual lexicon.

    Native-script words are matched as substrings (no reliable word boundaries
    after agglutination); Latin words are matched as tokens.
    """
    low = normalize_text(text).lower()
    tokens = re.findall(r"[a-z']+", low)
    pos = sum(1 for t in tokens if t in POS_WORDS)
    neg = sum(1 for t in tokens if t in NEG_WORDS)
    for w in POS_WORDS:
        if not w.isascii() and w in low:
            pos += 1
    for w in NEG_WORDS:
        if not w.isascii() and w in low:
            neg += 1
    # negated positives ("not worth", "nahi accha") count as negative
    negated = len(re.findall(r"\b(?:not|never|nahi|no)\s+(?:\w+\s+)?(?:worth|good|happy|great|accha)\b", low))
    return max(0, pos - negated), neg + negated


def lexicon_sentiment(text: str) -> tuple[str | None, float]:
    """Lexicon fallback label and a crude confidence; None when there is no signal."""
    pos, neg = lexicon_score(text)
    if pos == 0 and neg == 0:
        return None, 0.0
    if pos and neg:
        return "MIXED", 0.5
    label = "POSITIVE" if pos else "NEGATIVE"
    return label, min(0.9, 0.55 + 0.1 * max(pos, neg))
