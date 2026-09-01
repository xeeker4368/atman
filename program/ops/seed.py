"""A small, varied seed corpus for the Phase 1 retrieval checkpoint.

BUILD_PLAN asks for "a handful of varied conversation/chunk records" so the
Phase 1 checkpoint can run **real queries** against something, rather than
confirming retrieval ranks sensibly against three toy strings.

Everything goes through the real pipeline
-----------------------------------------
``db.save_message()`` then ``chunking.finalise_conversation()``. Nothing here
hand-writes a ``chunks`` row. That matters for two reasons: the chunks carry
genuine provenance and genuine embeddings, so a query against them exercises
what production would do; and the corpus cannot drift from whatever the chunking
rules actually are, because it is produced by them.

Provenance vocabulary — provisional, and not this task's to choose
-----------------------------------------------------------------
Every chunk here lands as ``source_type="conversation"`` /
``source_trust="firsthand"``, because that is what ``program/memory/chunking.py``
writes and **task 1.7 owns the vocabulary**. 1.7 is Tier 3 and has not landed.

So this corpus deliberately contains **one source_type only**. Inventing a
second one to make the dataset look more varied would be writing 1.7's
vocabulary ahead of its design pass, which is exactly the kind of quiet decision
that task is gated for. When 1.7 lands and defines real source types, this
module is where a mixed-provenance corpus should be added.

What the corpus is built to exercise
------------------------------------
Retrieval quality is not "did it find the only matching document". The shapes
here are chosen so that a later checkpoint can tell good ranking from lucky
ranking:

* **Distinct topics** — the easy positives.
* **Two deliberately adjacent topics** (espresso vs. pour-over coffee) — a
  ranking has to discriminate between near neighbours, not merely find a match.
  A retrieval that cannot separate these looks fine on distinct topics alone.
* **Two users** — Lyle and Jodie, so per-user attribution and filtering have
  something to filter.
* **One very long pasted message** — over the 5,000-character embedding budget,
  so sub-chunk splitting actually runs and sibling chunks exist to be retrieved.
* **A long multi-turn conversation** — enough turns to seal several groups, so
  chunk ordering within a conversation is real.
* **Short exchanges** — well under the 2,500-character target, so not every
  chunk is a full one.
* **One conversation left open** — its trailing group is deliberately
  unindexed, which is the state idle-close exists to resolve. A corpus of only
  closed conversations would hide that distinction.

Safety
------
Additive only. Nothing here deletes or overwrites: seeding a store that already
holds conversations is refused unless the caller explicitly allows it, and even
then it only adds. This module is for disposable development data — which is
what `PROJECT.md` says this build's database is throughout.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from program.memory import chunking, db

logger = logging.getLogger(__name__)


class SeedError(RuntimeError):
    """Seeding was refused or could not complete."""


@dataclass(frozen=True)
class SeedConversation:
    key: str
    user: str
    topic: str
    turns: tuple[tuple[str, str], ...]
    close: bool = True
    note: str = ""


@dataclass
class SeedResult:
    users: dict[str, str] = field(default_factory=dict)
    conversations: dict[str, str] = field(default_factory=dict)
    messages: int = 0
    chunks: int = 0
    open_conversations: list[str] = field(default_factory=list)
    split_conversations: list[str] = field(default_factory=list)


USERS: tuple[tuple[str, str], ...] = (
    ("Lyle", "admin"),
    ("Jodie", "user"),
)


# A single message over the 5,000-character embedding budget, so sub-chunk
# splitting runs. Written as real paragraphs rather than padding: the splitter
# prefers paragraph boundaries, and filler would not exercise that preference.
_LONG_PASTE = "\n\n".join(
    [
        "I found my grandmother's notebook in the box from the move and I want "
        "to type it up before the paper gets any worse. Here is the first part "
        "of it. The handwriting is difficult in places so some of this is my "
        "best guess at what she meant.",
        "On bread. The flour matters more than anything else you will do to it. "
        "A soft flour will give you a soft loaf no matter how long you work it, "
        "and a strong flour will forgive a great deal of impatience. If you can "
        "only control one thing, control the flour and let the rest follow. She "
        "underlines this twice, which I think means she had been arguing with "
        "somebody about it.",
        "On the starter. Keep it on the shelf above the stove where it is warm "
        "but not hot. Feed it the same weight of flour and water, by weight and "
        "not by eye, because eyes lie and scales do not. If it smells like "
        "acetone it is hungry and not dead, and it will come back if you feed it "
        "twice a day for three days. She notes that she killed two before she "
        "understood this, and that the third one outlived the war.",
        "On the oven. Every oven lies about its temperature. Find out how yours "
        "lies and correct for it rather than trusting the dial. Bake the first "
        "twenty minutes with steam and the rest without. If the crust is pale "
        "the oven is cool, and if the base is burnt the shelf is too low.",
        "On timing. Bread does not care what time it is. If the dough is not "
        "ready then it is not ready, and the schedule is your problem and not "
        "its. She says this more sharply than the rest, and I can hear her "
        "saying it.",
        "On the tin loaf, which she calls the everyday loaf. Nine hundred grams "
        "of strong white flour, six hundred grams of water, eighteen grams of "
        "salt, two hundred grams of starter at its peak. Mix, rest an hour, "
        "fold four times at half-hour intervals, shape, prove until it springs "
        "back slowly, bake. She does not give a proving time, only the test, "
        "which I think is the point she has been making the whole way through.",
        "On what to do with a failure. Slice it thin, dry it in a cool oven, and "
        "keep it for crumbs. Nothing that came out of good flour is wasted, only "
        "reassigned. There is a note in the margin here that just says 'Michael' "
        "and I do not know what that refers to.",
        "On preserves, which begins a new section and a much neater hand, so I "
        "think she wrote this part earlier or on a better day. Fruit for jam "
        "should be slightly under-ripe rather than over, because the setting "
        "comes from the fruit and not from the sugar, and ripe fruit has spent "
        "what it had. If you must use ripe fruit, add a cut apple in muslin and "
        "take it out before potting.",
        "On sugar in preserves. Warm the sugar before it goes in or it will "
        "drop the temperature and you will boil the life out of the fruit "
        "waiting to get it back. She is emphatic about this and repeats it two "
        "lines later, which suggests she expected to be ignored.",
        "On the setting point. Cold saucer, drop of jam, push it with a finger. "
        "If it wrinkles it is done. She does not trust thermometers for this and "
        "says so, and then concedes that a thermometer is better than nothing "
        "if your hands are cold, which is as close to agreement as she comes.",
        "On potting. Hot jars, hot jam, lids on immediately. A jar that is cool "
        "when the jam goes in will crack, and a lid that goes on cool will let "
        "in what you spent the afternoon keeping out. Label everything with the "
        "month and the fruit, because you will not remember and you will be "
        "annoyed about it in February.",
        "On the neighbour's dog, which I had assumed was a story and is instead "
        "four pages of sustained complaint about a terrier that got into the "
        "fruit cage three summers running. She names the dog. She does not name "
        "the neighbour. There is a drawing of the fruit cage with what I think "
        "are proposed modifications to it, and one of them appears to involve "
        "electricity.",
        "The last page of this section is a list of what she planted and when, "
        "going back further than I expected, and it stops mid-line in the middle "
        "of an entry for autumn raspberries. I do not know whether she "
        "continued somewhere else or whether that is simply where she stopped.",
        "There is a shorter section after the preserves that I nearly missed "
        "because it is written sideways in the margin over four pages. It is "
        "about storing things through the winter. Apples wrapped individually "
        "in newspaper and not touching each other, because one going over takes "
        "the whole tray with it. Onions plaited and hung somewhere with moving "
        "air. Potatoes in the dark, and she is specific that a light-struck "
        "potato is not merely green but actually dangerous, which I did not "
        "know and have now looked up and she is right.",
        "On root vegetables in sand, which I have never seen anyone do. A box, "
        "damp sand, carrots laid in so they do not touch, topped up and kept "
        "somewhere cold but not freezing. She says they will keep until spring "
        "and taste better in March than they did in October, which sounds like "
        "an exaggeration until you notice she has written the same claim twice, "
        "years apart, in different ink.",
        "On what not to bother storing. Courgettes, which will not keep and "
        "should be given away while they are still small enough that people "
        "want them. Lettuce, obviously. And anything you did not enjoy eating "
        "fresh, because you will enjoy it less in February and it will sit "
        "there reproaching you until it is thrown out anyway. This is the only "
        "genuinely funny line in the whole notebook and I think it was "
        "accidental.",
        "The very last entry in this hand is a note to herself that reads, in "
        "full, 'ask about the greengage'. There is no further reference to a "
        "greengage anywhere in the notebook. I have read it twice now looking.",
    ]
)


CONVERSATIONS: tuple[SeedConversation, ...] = (
    SeedConversation(
        key="fans",
        user="Lyle",
        topic="Mac mini fan noise",
        note="short exchange, well under the chunk target",
        turns=(
            ("user", "The Mac mini's fan has been running hard all afternoon. "
                     "Is that something to worry about?"),
            ("assistant", "Sustained fan noise usually means something is "
                          "holding the CPU or GPU busy rather than a hardware "
                          "fault. A model kept loaded in memory will do it. "
                          "Worth checking what is actually resident before "
                          "assuming the fan itself is the problem."),
        ),
    ),
    SeedConversation(
        key="espresso",
        user="Lyle",
        topic="espresso extraction",
        note="deliberately adjacent to the pour-over conversation",
        turns=(
            ("user", "My espresso is coming out sour and it runs too fast "
                     "through the basket. What should I change first?"),
            ("assistant", "Sour with a fast shot points at under-extraction, so "
                          "grind finer before you change anything else. That "
                          "raises resistance, slows the flow, and pulls more "
                          "out of the puck. Change one variable at a time — "
                          "dose and temperature can wait until the grind is "
                          "close."),
            ("user", "How fine before I have gone too far?"),
            ("assistant", "When the shot starts choking and tasting bitter and "
                          "ashy, you have gone past it. The window between "
                          "sour and bitter is narrow on espresso, which is why "
                          "small grind adjustments matter so much more here "
                          "than in other methods."),
        ),
    ),
    SeedConversation(
        key="pourover",
        user="Jodie",
        topic="pour-over coffee",
        note="near neighbour of the espresso conversation; different user",
        turns=(
            ("user", "I want to start making pour-over coffee in the mornings. "
                     "What do I actually need?"),
            ("assistant", "A cone, filters, a kettle you can pour slowly from, "
                          "and a grinder. The grinder matters most — pour-over "
                          "is forgiving about almost everything except uneven "
                          "grounds, which give you sour and bitter in the same "
                          "cup."),
            ("user", "Does the water temperature matter as much as people say?"),
            ("assistant", "It matters, but less than grind consistency. Just "
                          "off the boil is fine for most roasts. Pour-over has "
                          "a much wider forgiving window than espresso does, so "
                          "you can be approximate about temperature and still "
                          "get something good."),
        ),
    ),
    SeedConversation(
        key="tomatoes",
        user="Jodie",
        topic="tomato plants",
        note="short exchange, distinct topic",
        turns=(
            ("user", "The leaves on the tomato plants are going yellow from the "
                     "bottom up. Is that overwatering?"),
            ("assistant", "Bottom-up yellowing is more often nitrogen moving to "
                          "newer growth than it is overwatering. If the soil "
                          "is draining and the lower leaves are the only ones "
                          "affected, feeding is the likelier answer than "
                          "cutting back the water."),
        ),
    ),
    SeedConversation(
        key="notebook",
        user="Jodie",
        topic="grandmother's baking notebook",
        note="contains one message over the 5,000-char budget; forces splitting",
        turns=(
            ("user", "I'm typing up my grandmother's notebook. Can I paste what "
                     "I have so far and get your thoughts on it?"),
            ("assistant", "Yes, go ahead and paste it."),
            ("user", _LONG_PASTE),
            ("assistant", "What comes through is that almost every instruction "
                          "is a test rather than a number — prove until it "
                          "springs back slowly, feed until it stops smelling of "
                          "acetone. She is teaching you to read the dough "
                          "instead of the clock, which is why the one place she "
                          "does give quantities is the everyday loaf and even "
                          "there she withholds the proving time."),
        ),
    ),
    SeedConversation(
        key="retrieval",
        user="Lyle",
        topic="the memory system's retrieval design",
        note="long multi-turn; seals several groups so chunk ordering is real",
        turns=(
            ("user", "I want to talk through how retrieval should work before I "
                     "build any of it."),
            ("assistant", "Start with what failure looks like. A retrieval that "
                          "always returns its top few results regardless of "
                          "match quality will feel like it is working right up "
                          "until it confidently supplies something irrelevant."),
            ("user", "That is the failure I keep hitting. A bare top-K with no "
                     "sense of whether anything actually matched."),
            ("assistant", "Then the floor is the first thing to design, not the "
                          "last. Both legs need one — the lexical side and the "
                          "vector side — because they fail differently. A "
                          "vector search always returns its nearest neighbours "
                          "even when the nearest is far away."),
            ("user", "And fusing the two legs? I was going to use reciprocal "
                     "rank fusion."),
            ("assistant", "Rank fusion avoids having to make two different "
                          "score scales comparable, which is the part that "
                          "usually goes wrong. It only needs the orderings, not "
                          "the magnitudes, so a distance and a BM25 score can "
                          "be combined without inventing a conversion between "
                          "them."),
            ("user", "What about the thresholds themselves? I have numbers from "
                     "the previous build."),
            ("assistant", "Those were measurements against that corpus, not "
                          "constants. Carrying them over would look like "
                          "calibration while being a guess, and a guess wearing "
                          "the costume of a measurement is worse than an "
                          "obvious placeholder, because nobody revisits it. "
                          "Build the floor as a mechanism and leave it "
                          "permissive until there is real material here to "
                          "calibrate against."),
            ("user", "That means the degenerate-query rule cannot fire yet."),
            ("assistant", "It cannot, and the honest thing is to say so in the "
                          "checkpoint rather than let a passing test imply the "
                          "rejection path was exercised. It structurally has "
                          "nothing to reject while the floors are permissive. "
                          "Writing that down is what stops someone reading the "
                          "checkpoint later and concluding the behaviour was "
                          "verified when all that was verified was that the "
                          "code path exists."),
            ("user", "How should the two indexes stay in step with each other? "
                     "I do not want a lexical index pointing at chunks that "
                     "have been rewritten underneath it."),
            ("assistant", "Make one of them canonical and derive the rest. If "
                          "the chunk rows are the source of truth then both the "
                          "vector store and the lexical index are rebuildable "
                          "from them, and a disagreement is repairable rather "
                          "than a puzzle. Keep the lexical index in step with "
                          "triggers rather than with convention, because "
                          "convention is what fails quietly at three in the "
                          "morning six months from now."),
            ("user", "And when a chunk's text changes?"),
            ("assistant", "It should not. Sealed chunks are final — that is "
                          "what makes the derived indexes safe to trust. Store "
                          "a hash of the text on the row so that a change is "
                          "detectable rather than merely unlikely, and treat a "
                          "mismatch as a real error instead of re-embedding "
                          "over the top of it and moving on."),
            ("user", "Last one. How do I know any of this is actually working "
                     "rather than looking like it is working?"),
            ("assistant", "Run real queries against real material and read the "
                          "results yourself. A test proves the code does what "
                          "it was written to do; only looking at rankings over "
                          "genuine content tells you whether what it was "
                          "written to do was the right thing. That is a "
                          "different question and no test answers it."),
        ),
    ),
    SeedConversation(
        key="checkin",
        user="Jodie",
        topic="short back-and-forth about the week",
        note="nine short turns: the 8-turn cap decides the boundary, not the "
             "character target, which the other long conversation exercises",
        turns=(
            ("user", "Are we still doing the thing on Saturday?"),
            ("assistant", "I have no calendar access, so I only know what has "
                          "been said here. Nothing about Saturday has come up "
                          "before this message."),
            ("user", "Fair. It is the market thing."),
            ("assistant", "Noted. If you tell me what time, I will have it in "
                          "the record for next time you ask."),
            ("user", "Nine, probably. Earlier if it rains."),
            ("assistant", "Nine, earlier if it rains. That is a strange rule "
                          "and I assume there is a reason for it."),
            ("user", "The good stall packs up when it rains."),
            ("assistant", "That explains it. Nine, or earlier if it rains, "
                          "because the good stall leaves early in bad weather."),
            ("user", "You have got it."),
            ("assistant", "Then that is what I have."),
            ("user", "Also we need more of the coffee."),
            ("assistant", "Understood. That is the fourth thing for Saturday."),
            ("user", "It is the second thing."),
            ("assistant", "You are right, it is the second. Market at nine, and "
                          "coffee."),
            ("user", "Right."),
            ("assistant", "Right."),
            ("user", "Thanks."),
            ("assistant", "Any time."),
        ),
    ),
    SeedConversation(
        key="open-thread",
        user="Lyle",
        topic="an unfinished conversation",
        close=False,
        note="left OPEN on purpose: its trailing group stays unindexed",
        turns=(
            ("user", "Remind me tomorrow to look at whether the backup should "
                     "run on a schedule."),
            ("assistant", "Noted. There is no scheduler yet, so that is a "
                          "manual invocation for now."),
            ("user", "Right. And I still want to think about where the backups "
                     "should actually live."),
        ),
    ),
)


def _existing_conversation_count() -> int:
    with db.connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]


def seed(allow_existing: bool = False) -> SeedResult:
    """Write the corpus. Additive; never deletes or overwrites anything.

    Requires a reachable Ollama instance — chunking embeds every sealed group,
    and a corpus of chunks with no vectors would be useless to the retrieval
    checkpoint this exists for.
    """
    db.init_databases()

    existing = _existing_conversation_count()
    if existing and not allow_existing:
        raise SeedError(
            f"the store already holds {existing} conversation(s). Seeding is "
            f"additive and will not deduplicate, so this is refused by default. "
            f"Pass allow_existing=True (or --allow-existing) to add the corpus "
            f"anyway. Nothing is ever deleted to make room for it."
        )

    result = SeedResult()

    for name, role in USERS:
        found = db.get_user_by_name(name)
        result.users[name] = found["id"] if found else db.create_user(name, role=role)

    for spec in CONVERSATIONS:
        user_id = result.users[spec.user]
        conversation_id = db.start_conversation(user_id)
        result.conversations[spec.key] = conversation_id

        for role, content in spec.turns:
            db.save_message(conversation_id, user_id, role, content)
            result.messages += 1

        if spec.close:
            db.end_conversation(conversation_id)
            chunk_result = chunking.finalise_conversation(conversation_id)
            written = getattr(chunk_result, "chunks_written", None)
            if written is None:
                written = len(db.get_conversation_chunks(conversation_id))
            result.chunks += written
            logger.info(
                "seeded %s (%s): %d turns, %d chunk(s)",
                spec.key, spec.topic, len(spec.turns), written,
            )
        else:
            result.open_conversations.append(spec.key)
            logger.info("seeded %s (%s): left open", spec.key, spec.topic)

    # Which conversations actually split — reported rather than assumed, since
    # it depends on the embedding budget and the packing rules, not on this file.
    for key, conversation_id in result.conversations.items():
        rows = db.get_conversation_chunks(conversation_id)
        firsts = [r["first_message_id"] for r in rows]
        if len(firsts) != len(set(firsts)):
            result.split_conversations.append(key)

    return result
