"""The first 3 seconds decide whether a short is watched. Generic openers ("Bạn có biết rằng…", "Did you
know…", "Hôm nay chúng ta…") waste them, so they are cut off without asking the model again: what follows
them is usually the real hook ("Bạn có biết rằng bạch tuộc có ba trái tim?" → "Bạch tuộc có ba trái
tim?"). Only a hook that is still unusable costs one rewrite call (writer.fix_hook)."""

from vidgen.text import bare, word_count

HOOK_MAX_WORDS = 14   # ~3 s of speech
MIN_LEFT_WORDS = 3    # what remains after cutting an opener must still say something

# longest first: "bạn có biết rằng" must win over "bạn có biết"
OPENERS = sorted({
    # vi
    "bạn có biết rằng", "bạn có biết", "các bạn có biết", "hôm nay chúng ta sẽ nói về", "hôm nay chúng ta nói về",
    "hôm nay chúng ta sẽ tìm hiểu về", "hôm nay chúng ta sẽ tìm hiểu", "hôm nay chúng ta", "hôm nay mình sẽ",
    "xin chào các bạn", "xin chào", "chào các bạn", "trong video này", "bạn đã bao giờ tự hỏi",
    "bạn đã bao giờ", "có bao giờ bạn tự hỏi",
    # en
    "did you know", "have you ever wondered", "have you ever", "in this video", "in today's video",
    "today we will talk about", "today we're talking about", "today we", "hello everyone", "hi everyone",
    "hello", "welcome to", "let's talk about",
}, key=lambda o: -len(o.split()))
CONNECTORS = {"rằng", "là", "that"}  # left dangling after the opener ("Did you know THAT …")


def _opener_length(tokens: list[str]) -> int:
    keys = [bare(t) for t in tokens]
    for opener in OPENERS:
        words = [bare(w) for w in opener.split()]
        if keys[:len(words)] == words:
            n = len(words)
            while n < len(keys) and keys[n] in CONNECTORS:
                n += 1
            return n
    return 0


def strip_generic_opener(text: str) -> str | None:
    """The hook without its generic opener (capitalised), "" if nothing meaningful is left, or None when
    the text doesn't start with one."""
    tokens = text.split()
    n = _opener_length(tokens)
    if n == 0:
        return None
    rest = tokens[n:]
    while rest and not bare(rest[0]):   # a stray "," or "—"
        rest = rest[1:]
    if len(rest) < MIN_LEFT_WORDS:
        return ""
    first = rest[0].lstrip(",;:—-")
    return " ".join([first[:1].upper() + first[1:], *rest[1:]])


def hook_problem(text: str) -> str | None:
    """"generic", "too long" or None (a usable hook)."""
    if strip_generic_opener(text) is not None:
        return "generic"
    if word_count(text) > HOOK_MAX_WORDS:
        return "too long"
    return None


# asking viewers to comment belongs to the closing scene only (seen live: a second one in scene 12 of 15)
CTA_PHRASES = ("hãy chia sẻ", "chia sẻ ý kiến", "để lại bình luận", "bình luận bên dưới", "để lại ý tưởng",
               "comment below", "in the comments", "let me know", "leave a comment", "share your")


def is_comment_cta(text: str) -> bool:
    low = text.casefold()
    return any(p in low for p in CTA_PHRASES)
