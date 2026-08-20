"""Script statistics (word/scene/character breakdown) for the ':stats'
popup -- a pure function of the buffer, no Editor/curses dependency."""

from fountain import split_character_cue, DIALOGUE_CHAIN_TYPES

def compute_stats(buffer):
    """Word/scene/character breakdown for the ':stats' popup. A pure
    function of `buffer` (no Editor/curses dependency), so it's directly
    unit-testable without a fake screen.

    Returns a dict:
      - total_words: word count across every element type.
      - dialogue_words / action_words: split out separately since they
        read very differently -- dialogue words roughly track spoken
        runtime, action words roughly track visual/page real estate.
      - scene_count: number of HEADING lines.
      - characters: (name, line_count, word_count) for every distinct
        speaker's DIALOGUE, sorted by word count descending. A speaker's
        (V.O.)/(CONT'D)-style extension is stripped via
        split_character_cue() so e.g. "SRIRAM" and "SRIRAM (V.O.)" count
        as the same character, matching how :rename already treats
        character identity.
    """
    total_words = dialogue_words = action_words = scene_count = 0
    char_stats = {}  # base_name -> [line_count, word_count]
    current_speaker = None
    for ln in buffer:
        t, txt = ln["type"], ln["text"]
        words = len(txt.split())
        total_words += words
        if t not in DIALOGUE_CHAIN_TYPES:
            current_speaker = None
        if t == "heading":
            scene_count += 1
        elif t == "action":
            action_words += words
        elif t == "character":
            base, _ = split_character_cue(txt)
            current_speaker = base.strip().upper() or None
        elif t == "dialogue":
            dialogue_words += words
            if current_speaker:
                lc, wc = char_stats.get(current_speaker, (0, 0))
                char_stats[current_speaker] = (lc + 1, wc + words)
    characters = sorted(
        ((name, lc, wc) for name, (lc, wc) in char_stats.items()),
        key=lambda tup: tup[2], reverse=True,
    )
    return {
        "total_words": total_words,
        "dialogue_words": dialogue_words,
        "action_words": action_words,
        "scene_count": scene_count,
        "characters": characters,
    }


