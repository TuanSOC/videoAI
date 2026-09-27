"""Word-level text helpers shared by the voice, subtitles, sound design and script code.

One implementation of each on purpose: the copies had drifted (only one `bare` applied NFC, so the same
Vietnamese word typed decomposed matched in one place and not the other; sentence ends were detected
six different ways)."""

import unicodedata

SENTENCE_END = (".", "!", "?", "…")
CLAUSE_END = (*SENTENCE_END, ",", ";", ":")
CLOSERS = "'\"”’»)]"


def bare(token: str) -> str:
    """Comparison key: NFC (the same letter can be one code point or base + combining mark),
    case-folded, letters and digits only."""
    return "".join(ch for ch in unicodedata.normalize("NFC", token).casefold() if ch.isalnum())


TYPOGRAPHY = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u00a0": " ", "\u202f": " "})


def plain(text: str) -> str:
    """Typographic hyphens/spaces from hosted models ("12\u2011inch") → plain ones: they trip TTS, number
    matching and read oddly in titles."""
    return text.translate(TYPOGRAPHY)


def is_number(token: str) -> bool:
    return any(ch.isdigit() for ch in token)


def ends_with(word: str, punct: tuple[str, ...] = SENTENCE_END) -> bool:
    """Punctuation at the end of a word, looking past closing quotes/brackets ("không?'")."""
    return word.rstrip(CLOSERS).endswith(punct)


def word_count(text: str) -> int:
    return len(text.split())


def align(tokens: list[str], words: list[str]) -> list[tuple[list[int], list[int]]] | None:
    """Pair runs of `tokens` (the narration as written) with runs of `words` (what the TTS reported)
    that spell the same letters. Either side may split or merge: edge-tts reports "AI-generated" as
    three words, and "năm 2013," as one. Returns [(token indices, word indices)], or None when the two
    don't spell the same text. Punctuation-only items are ignored."""
    bt, bw = [bare(t) for t in tokens], [bare(w) for w in words]
    ti = [k for k, t in enumerate(bt) if t]
    wi = [k for k, w in enumerate(bw) if w]
    groups: list[tuple[list[int], list[int]]] = []
    a = b = 0
    while a < len(ti) and b < len(wi):
        ta, wa = [ti[a]], [wi[b]]
        st, sw = bt[ti[a]], bw[wi[b]]
        a, b = a + 1, b + 1
        while st != sw:
            if not (st.startswith(sw) or sw.startswith(st)):
                return None
            if len(st) < len(sw):
                if a >= len(ti):
                    return None
                ta.append(ti[a])
                st += bt[ti[a]]
                a += 1
            else:
                if b >= len(wi):
                    return None
                wa.append(wi[b])
                sw += bw[wi[b]]
                b += 1
        groups.append((ta, wa))
    return groups if a == len(ti) and b == len(wi) else None
