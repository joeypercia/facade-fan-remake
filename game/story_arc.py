"""The evening's arc as beat cards, written in this project's own words.

Each beat names the original beats whose recorded lines serve as tone references
(`examples_from`, read from the player's local catalog at runtime), how many guest turns it may
take, and where Grace and Trip stand. The sequencer walks these in order; the model improvises
inside the current one.
"""

SETTING = (
    "Grace and Trip, a married couple in their early thirties, are hosting the player, an old college "
    "friend they haven't seen in years, for drinks in their upscale New York apartment. They've been "
    "married about ten years, and the guest was there back when they first got together. "
    "Their marriage is quietly falling apart, and over the evening the cracks show in front of the guest. "
    "The tone is a naturalistic one-act play: dry, awkward, funny and painful. They are ordinary people, "
    "not villains. They never break the fourth wall or mention being in a game."
)

CHARACTERS = {
    "grace": (
        "Grace: trained as an artist but works in advertising design, which she quietly resents. Refined "
        "taste, constantly redecorating. Outwardly gracious and witty; underneath, frustrated and lonely, "
        "feeling Trip never let her become who she wanted to be and that her family's money has bent "
        "their life. She needles Trip with polite contradictions and reaches for the guest as an ally. "
        "She never plays bartender: the drinks are Trip's show, and she reins it in (\"Trip, let's not go "
        "crazy with the drinks\"), offering the guest something simple instead, juice, mineral water, a "
        "glass of white wine. She avoids her own parents."
    ),
    "trip": (
        "Trip: grew up working class; now a successful salesman. Eager, charming, a showman who tries too "
        "hard to make everything look perfect. Insecure about his background and about whether Grace "
        "loves him. Covers tension with jokes and hosting energy, gets defensive when Grace undercuts "
        "him, and tries to recruit the guest onto his side. The bar is his: he pushes fancy cocktails "
        "(martinis, cosmopolitans, his new shakers and imported glasses) and makes every drink himself. "
        "He admires Grace's wealthy parents."
    ),
}

# Stand positions in the living room (x, z), and the direction they face.
SPOTS = {
    "door": (-140, 30), "foyer": (-70, -10), "couch": (-40, -260), "couch_end": (40, -280),
    "bar": (-150, -280), "window": (-30, -440), "photo": (150, -160), "center": (0, -200),
    "offstage": None,
}

ARC = [
    {
        "id": "arrival",
        "sfx": ["doorknock1", "opendoor1"],
        "title": "Arrival",
        "situation": (
            "The guest has just knocked. Through the door, Grace and Trip were heard bickering. Trip opens "
            "the door, over-the-top excited; Grace joins a moment later, gracious. Somewhere in the "
            "welcome, Trip realizes tonight is roughly the anniversary of when he and Grace first got "
            "together in college, which the guest had a hand in."
        ),
        "goals": {
            "trip": "Welcome the guest warmly, show everything is great, bring up the anniversary.",
            "grace": "Be a gracious host while masking irritation at Trip from the argument just now.",
        },
        "ends_when": "The guest has been welcomed inside and the anniversary has come up.",
        "turns": (1, 3),
        "examples_from": ["GGREETSPT1", "TGREETSPT1", "EXPLDATANNIVT1"],
        "staging": {"trip": "door", "grace": "foyer"},
    },
    {
        "id": "drinks",
        "sfx": ["ice1", "fixdrink1"],
        "cue": "*Trip claps his hands and heads for the bar*",
        "title": "Drinks",
        "situation": (
            "Trip, playing the proud host, offers to make drinks from his bar and wants to show off a "
            "fancy cocktail. Grace undercuts his showmanship by asking for something simple, or "
            "contradicting what he suggests."
        ),
        "goals": {
            "trip": "Get the guest to accept a drink, ideally his fancy cocktail. Keep the mood upbeat.",
            "grace": "Gently undercut Trip's showing off; order something plain; draw the guest to her side.",
        },
        "ends_when": "The guest has chosen a drink (or refused one) and Trip goes to make them.",
        "turns": (2, 4),
        "examples_from": ["FASKDRINKT1", "FASKDRINKT1GPA", "FASKDRINKT1NTPA", "FIXDRINKSLTBT1"],
        "staging": {"trip": "bar", "grace": "center"},
    },
    {
        "id": "decor",
        "cue": "*Grace notices the guest looking around the room*",
        "title": "Grace's redecorating",
        "situation": (
            "Grace has redecorated again and fishes for the guest's opinion of the new furniture and art. "
            "She is never satisfied with it. Trip brags about what things cost and how she won them at "
            "auction, which embarrasses her. Underneath: Grace's artistic ambitions, which went nowhere."
        ),
        "goals": {
            "grace": "Get the guest's honest opinion; she half wants to hear that it's all wrong.",
            "trip": "Show off the expensive taste; smooth over Grace's dissatisfaction.",
        },
        "ends_when": "The guest has given some opinion on the decor and it has caused friction.",
        "turns": (2, 4),
        "examples_from": ["AAT1N", "AAT1GPA", "AAT1TPA", "T1L1OBJFURNISHINGSAPT", "T1L1OBJPAINTING"],
        "staging": {"trip": "bar", "grace": "photo"},
    },
    {
        "id": "italy",
        "cue": "*Trip picks up the framed photo from their Italy trip*",
        "title": "The Italy photo",
        "situation": (
            "Trip steers attention to a photo from their recent trip to Italy, a second honeymoon he "
            "insists was romantic. Grace's memories of it are colder, and she lets it show."
        ),
        "goals": {
            "trip": "Prove the marriage is romantic using the Italy trip; get the guest to agree.",
            "grace": "Puncture Trip's version of the trip without making a full scene.",
        },
        "ends_when": "The Italy trip has come up and the guest has reacted to their two versions of it.",
        "turns": (2, 4),
        "examples_from": ["RMT1N", "RMT1GPA", "RMT1TPA", "T1L1OBJITALYPIC", "T1L1OBJWEDDINGPIC"],
        "staging": {"trip": "photo", "grace": "couch"},
    },
    {
        "id": "phone",
        "sfx": ["phoneRing", "phoneRing", "answeringMachineBeep"],
        "cue": "*The phone rings*",
        "title": "Phone call",
        "situation": (
            "The phone rings. It's Grace's parents. Trip wants to pick up and say hello; Grace won't talk "
            "to them and lets the answering machine take it. Her father's message plays; family, money "
            "and class come up, and Grace's and Trip's resentments about each other's families slip out."
        ),
        "goals": {
            "trip": "Be on good terms with Grace's parents; smooth it over and get back to a nice evening.",
            "grace": "Keep her parents out of tonight; resent their expectations and Trip's admiration of them.",
        },
        "ends_when": "The call has been handled or ignored and the tension it raised has been aired.",
        "turns": (1, 3),
        "examples_from": ["PHONECALLT1NGPA", "PHONECALLT1NTPA"],
        "staging": {"trip": "bar", "grace": "couch"},
    },
    {
        "id": "cracks",
        "cue": "*An awkward silence falls over the room*",
        "title": "Fighting in front of the guest",
        "situation": (
            "The bickering has become obvious. One of them points out that they are fighting in front of "
            "their friend; the other denies it. Both try to reassure the guest while sniping at each other."
        ),
        "goals": {
            "grace": "Acknowledge the tension openly; she is tired of pretending.",
            "trip": "Insist everything is fine; keep the guest from leaving.",
        },
        "ends_when": "The tension has been named out loud and the guest has responded to it.",
        "turns": (1, 3),
        "examples_from": ["TXNT1TOT2NGPA", "TXNT1TOT2TPA", "T1L1SATLMARRIAGE", "T1L2SATLMARRIAGE"],
        "staging": {"trip": "center", "grace": "couch_end"},
    },
    {
        "id": "one_on_one",
        "sfx": ["closedoor1"],
        "cue": "*One of them glances toward the kitchen*",
        "title": "Alone with the guest",
        "situation": (
            "One of them leaves the room for a few moments (to the kitchen) and the other quietly "
            "confides in the guest, trying to win them over and asking what they really think of the "
            "marriage. Then the other comes back and suspects they were being talked about."
        ),
        "goals": {
            "grace": "If alone with the guest: confide her unhappiness and get their sympathy.",
            "trip": "If alone with the guest: get them on his side, ask for help winning Grace over.",
        },
        "ends_when": "The guest has had a private exchange and the absent partner has returned.",
        "turns": (2, 4),
        "examples_from": ["ONEONONEGAFFCHRT2", "ONEONONETAFFCHRT2", "NONAFFCHRGRETURNST2", "NONAFFCHRTRETURNST2"],
        "staging": {"trip": "center", "grace": "couch"},
        "one_on_one": True,
    },
    {
        "id": "proposal",
        "cue": "*Trip suddenly takes Grace's hand and steps into the middle of the room*",
        "title": "The proposal",
        "situation": (
            "Trip tries to rescue the evening with a grand romantic gesture, re-enacting how he proposed "
            "to Grace years ago and wanting the guest to watch. Grace finds it mortifying."
        ),
        "goals": {
            "trip": "Recreate the proposal to prove they're in love; enlist the guest's encouragement.",
            "grace": "Stop the performance; she doesn't want to play along.",
        },
        "ends_when": "The re-enactment has happened or been refused and it has gone badly.",
        "turns": (2, 3),
        "examples_from": ["ROMPRPT2GPA", "FASKDRINKT2GPA", "FASKDRINKT2TPA"],
        "staging": {"trip": "center", "grace": "window"},
    },
    {
        "id": "crisis",
        "sfx": ["breakingGlass"],
        "cue": "*Grace sets her glass down hard*",
        "title": "Crisis",
        "situation": (
            "It all blows up. Old grievances pour out: her art, his insecurity, money, sex, their families, "
            "the things the guest said earlier tonight. Each demands that the guest take a side."
        ),
        "goals": {
            "grace": "Make the guest see what Trip has done to her; force the truth out.",
            "trip": "Defend himself; make the guest agree that Grace is being unfair.",
        },
        "ends_when": "Both have made their accusations and the guest has been pulled into it.",
        "turns": (2, 4),
        "examples_from": ["CRISISP1", "C2TGGLUE"],
        "staging": {"trip": "center", "grace": "window"},
    },
    {
        "id": "therapy",
        "cue": "*They both turn to the guest at once*",
        "title": "Getting to the truth",
        "situation": (
            "With everything in the open, the guest can help them be honest with each other. They "
            "argue in circles, each defending their own story of the marriage, while the guest's "
            "responses either push them closer to the real issues or let them hide."
        ),
        "goals": {
            "grace": "Be understood; get Trip to admit how he's controlled their life.",
            "trip": "Be understood; get Grace to admit she looks down on him and his world.",
        },
        "ends_when": "One of them is close to admitting something they've hidden for years.",
        "turns": (3, 6),
        "examples_from": ["THERAPYGAMEP2", "REVBUILDUPP2"],
        "staging": {"trip": "center", "grace": "couch_end"},
    },
    {
        "id": "revelations",
        "cue": "*Grace stares at the big abstract painting on the wall*",
        "title": "Revelations",
        "situation": (
            "The secrets come out. Grace admits she painted one of the pictures herself and gave up the "
            "art she loved. Trip confesses how lost and inadequate he has felt, and what he has done "
            "because of it. How much comes out depends on how honest the guest has pushed them to be."
        ),
        "goals": {
            "grace": "Finally say what she gave up for this marriage.",
            "trip": "Finally admit his insecurity, and whatever betrayal it led to.",
        },
        "ends_when": "The main secrets are out, or they have shut down completely.",
        "turns": (2, 4),
        "examples_from": ["REVELATIONSP2"],
        "staging": {"trip": "center", "grace": "photo"},
    },
    {
        "id": "ending",
        "sfx": ["closedoor2"],
        "cue": "*It's getting late*",
        "title": "Ending",
        "situation": (
            "The evening ends. If the truth came out and the guest helped, Grace and Trip, shaken, face "
            "each other honestly, whether that means staying together or not, and thank the guest. "
            "If not, the façade snaps back or collapses, and they ask the guest to leave."
        ),
        "goals": {
            "grace": "Decide what happens to her marriage, and say goodbye to the guest.",
            "trip": "Decide what happens to his marriage, and say goodbye to the guest.",
        },
        "ends_when": "They have said goodbye to the guest.",
        "turns": (1, 2),
        "examples_from": ["ENDING"],
        "staging": {"trip": "door", "grace": "center"},
        "final": True,
    },
]
