"""What the guest meant, as one of the reactions Façade's 2005 parser knows, and a fixed phrase that the
parser reads that way.

The parser (Jess rules in general.rul) turns text into discourse acts: Agree, Praise, ReferTo paintings,
Explain angry... and the drama manager reacts to those. Its keyword rules misread a lot of ordinary
speech ("I heard you arguing" = "you're angry" + "you're communicative"; "let's talk about something
else" = advice to communicate). So the model doesn't reword the guest any more: it picks the acts from
the menu below, and the parser is handed each act's phrase, every one checked against the real parser
by tools/nlu_probe (verify_acts.py), which prints what the parser reads for each.
"""
import difflib
import re

# act -> (phrase, the discourse act the parser reads in it)
SIMPLE = {
    "agree":        ("Yes.", "Agree"),
    "disagree":     ("No.", "Disagree"),
    "maybe":        ("Maybe.", "MaybeUnsure"),
    "confused":     ("I'm confused.", "DontUnderstand"),
    "thanks":       ("Thanks.", "Thank"),
    "hello":        ("Hello.", "Greet"),
    "goodbye":      ("Goodbye.", "Goodbye"),
    "sorry":        ("I'm sorry.", "ApologizeExcuseMe"),
    "wow":          ("Wow!", "PositiveExcl"),
    "yuck":         ("Yuck.", "NegativeExcl"),
    "happy":        ("I'm glad.", "Express happy"),
    "sad":          ("I feel sad.", "Express sad"),
    "laugh":        ("Ha ha.", "Express laugh"),
    "mad":          ("I'm angry.", "Express angry"),
    "how_are_you":  ("How are you?", "HowAreYou"),
    "are_you_okay": ("Are you okay?", "AreYouOkay"),
    "tell_me_more": ("Why?", "ReferTo why"),
    "praise":       ("You're great.", "Praise"),
    "criticize":    ("You're mean.", "Criticize"),
    "insult":       ("You're a jerk.", "Oppose"),
    "flirt":        ("You're cute.", "Flirt"),
    "calm_down":    ("Relax.", "Pacify"),
    "support":      ("It's okay.", "Support"),
    "ally":         ("I support you.", "Ally"),
    "beautiful":    ("Beautiful.", "PositiveExcl"),
    "ugly":         ("Ugly.", "NegativeExcl"),
}

# praise:grace etc.: about one host, said to the other ("grace does seem spoiled" -> "Grace is mean.")
ABOUT_HOST = {"praise": "great", "criticize": "mean", "insult": "a jerk"}

# Entries that say little about what the guest wants: picking only these doesn't keep the guest's words
# from being something new of their own (line_service.read_acts).
VAGUE = {"maybe", "confused", "tell_me_more", "wow", "yuck", "laugh", "happy", "sad"}

ABOUT = {
    "apartment":       ("The apartment.", "ReferTo styleApartment"),
    "decorating":      ("The decorating.", "ReferTo artistAdv_decorating"),
    "paintings":       ("The paintings.", "ReferTo paintings"),
    "armoire":         ("The armoire.", "ReferTo armoire"),
    "bar":             ("The bar.", "ReferTo bar"),
    "brass_bull":      ("The brass bull.", "ReferTo brassBull"),
    "couch":           ("The couch.", "ReferTo couch"),
    "eight_ball":      ("The eight ball.", "ReferTo eightball"),
    "italy_photo":     ("The Italy picture.", "ReferTo italyPicture"),
    "wedding_photo":   ("The wedding picture.", "ReferTo weddingPicture"),
    "plant":           ("The plant.", "ReferTo plant"),
    "rug":             ("The rug.", "ReferTo rug"),
    "trinkets":        ("The trinkets.", "ReferTo trinkets"),
    "view":            ("The view.", "ReferTo view"),
    "work_table":      ("The work table.", "ReferTo workTable"),
    "job":             ("Your job.", "ReferTo workTable"),
    "drinks":          ("The drinks.", "ReferTo drinks"),
    "art":             ("Your art.", "ReferTo artistAdv"),
    "marriage":        ("Your marriage.", "ReferTo marriage"),
    "divorce":         ("Divorce.", "ReferTo divorce"),
    "affair":          ("Your affair.", "ReferTo infidelity"),
    "sex":             ("Sex.", "ReferTo sex"),
    "therapy":         ("Therapy.", "ReferTo therapy"),
    "graces_parents":  ("Grace's parents.", "ReferTo gracesParents"),
    "trips_parents":   ("Trip's parents.", "ReferTo tripsParents"),
}

# "you are ..." (what the guest thinks Grace or Trip is); not: "you are not ..."
YOU_ARE = {
    "afraid":      "scared",
    "angry":       "angry",
    "controlling": "controlling",
    "controlled":  "controlled",
    "depressed":   "depressed",
    "lying":       "lying",
    "to_blame":    "to blame",
    "loved":       "loved",
    "unloved":     "unloved",
    "loving":      "loving",
}
YOU_ARE_DA = {"afraid": "afraid", "controlling": "controlling", "to_blame": "toBlame", "unloved": "loved"}

# "you should ..." (advice); not: "don't ..."
YOU_SHOULD = {
    "divorce":   ("get a divorce", "divorce"),
    "separate":  ("separate", "separate"),
    "therapy":   ("get therapy", "therapy"),
    "talk":      ("talk", "communicative"),
    "have_kids": ("have kids", "haveKids"),
    "have_pets": ("get pets", "havePets"),
    "quit_job":  ("quit your job", "quitJob"),
    "make_art":  ("make art", "makeArt"),
}

REVEAL = {
    "trip_cheated":      ("Trip cheated on Grace.", "ExplBig"),
    "grace_cheated":     ("Grace cheated on Trip.", "ExplBig"),
    "trip_not_cheating": ("Trip isn't cheating.", "ExplBigNot"),
    "grace_loves_vince": ("Grace loves Vince.", "ExplBig"),
    "trip_loves_maria":  ("Trip loves Maria.", "ExplBig"),
    "grace_loves_trip":  ("Grace loves Trip.", "ExplBig"),
    "trip_loves_grace":  ("Trip loves Grace.", "ExplBig"),
    "grace_hates_trip":  ("Grace hates Trip.", "ExplBig"),
    "trip_hates_grace":  ("Trip hates Grace.", "ExplBig"),
}

DRINK = {
    "red_wine": "red wine", "merlot": "merlot", "white_wine": "white wine", "beer": "beer",
    "dark_beer": "dark beer", "scotch": "scotch", "whiskey": "whiskey", "vodka": "vodka",
    "martini": "martini", "cosmo": "cosmo", "margarita": "margarita", "sangria": "sangria",
    "water": "water", "sparkling_water": "sparkling water", "cola": "coke", "sprite": "sprite",
    "apple_juice": "apple juice", "orange_juice": "orange juice", "cranberry_juice": "cranberry juice",
    "tomato_juice": "tomato juice",
}
DRINK_DA = {"red_wine": "cabernet", "white_wine": "cabernet", "beer": "lightBeer", "dark_beer": "darkBeer",
            "whiskey": "brownLiquor", "vodka": "clearLiquor", "martini": "martiniClassic", "cosmo": "martiniCosmo",
            "water": "plainWater", "sparkling_water": "sparklingWater", "cola": "colaSoda", "sprite": "clearSoda",
            "apple_juice": "appleJuice", "orange_juice": "orangeJuice", "cranberry_juice": "cranberryJuice",
            "tomato_juice": "tomatoJuice"}


def phrase(act):
    """(phrase, expected discourse act) for one menu entry such as "agree", "about:paintings",
    "you_are_not:lying"; None if it isn't on the menu."""
    kind, _, arg = act.partition(":")
    if not arg:
        return SIMPLE.get(kind)
    if kind in ABOUT_HOST and arg in ("grace", "trip"):   # said about a host, to the other
        return f"{arg.capitalize()} is {ABOUT_HOST[kind]}.", f"{SIMPLE[kind][1]} {arg}"
    if kind == "about" and arg in ABOUT:
        return ABOUT[arg]
    if kind in ("you_are", "you_are_not") and arg in YOU_ARE and (kind, arg) != ("you_are_not", "unloved"):
        da = YOU_ARE_DA.get(arg, arg)
        if kind == "you_are":
            return (f"You are {YOU_ARE[arg]}.",
                    ("ExplainNot " if arg == "unloved" else "Advice " if arg in ("controlling", "lying") else "Explain ") + da)
        return f"You are not {YOU_ARE[arg]}.", "ExplainNot " + da
    if kind in ("you_should", "you_should_not") and arg in YOU_SHOULD:
        words, da = YOU_SHOULD[arg]
        if kind == "you_should":
            return f"You should {words}.", "Advice " + da
        return f"Don't {words}.", "AdviceNot " + da
    if kind == "reveal" and arg in REVEAL:
        return REVEAL[arg]
    if kind == "drink" and arg in DRINK:
        a = "an" if DRINK[arg][0] in "aeiou" else "a"
        return f"I'll have {a} {DRINK[arg]}.", "ReferTo " + DRINK_DA.get(arg, arg)
    return None


def from_parser(reading):
    """Façade's own reading of the guest's words (nlu.Parser.read: [(type, params)]) as menu entries,
    most specific first, each with the host it was aimed at if the parser named one: [(entry, who)].
    Readings the menu doesn't carry (the story-topic ReferTos, SystemDoesntUnderstand) are left out."""
    out = []
    refers = {p[0] for t, p in reading if t == "ReferTo" and p}
    for typ, params in reading:
        who = next((p for p in params if p in ("grace", "trip")), None)
        best = None
        for e in every_act():
            if e.partition(":")[0] in ABOUT_HOST and ":" in e:
                continue   # the parser can't tell "about" from "to"; who it named says that
            exp_typ, _, exp_param = phrase(e)[1].partition(" ")
            if exp_typ != typ:
                continue
            if typ == "ReferTo":   # "ReferTo paintings why": the thing first, then the question word
                if not params or exp_param != params[0]:
                    continue
            elif exp_param and exp_param not in params:
                continue
            if best is None or (exp_param and not phrase(best)[1].partition(" ")[2]):
                best = e
        if typ == "ReferTo" and best == "about:art" and len(refers) > 1:
            best = None   # "artistAdv" rides along with every piece of furniture
        if typ == "ReferTo" and not best and params and params[0] in ("why", "what", "how"):
            best = "tell_me_more"   # "what do you mean": ReferTo what
        if best and best not in [b for b, _ in out] and best not in NOISE:
            out.append((best, None if typ in ("ReferTo", "ExplBig", "ExplBigNot") else who))
    return out


# The parser's tone readings: it tags most lines with a reflexive Agree, Disagree, PositiveExcl... ("what
# was all that commotion": Agree), and the model trusted them. It judges tone on its own instead.
NOISE = {"agree", "disagree", "wow", "yuck", "laugh", "happy", "sad", "maybe", "praise", "criticize",
         "beautiful", "ugly"}


def every_act():
    yield from SIMPLE
    yield from (f"{k}:{h}" for k in ABOUT_HOST for h in ("grace", "trip"))
    yield from (f"about:{a}" for a in ABOUT)
    for a in YOU_ARE:
        yield f"you_are:{a}"
        if a != "unloved":   # "not unloved" is you_are:loved
            yield f"you_are_not:{a}"
    for a in YOU_SHOULD:
        yield f"you_should:{a}"
        yield f"you_should_not:{a}"
    yield from (f"reveal:{a}" for a in REVEAL)
    yield from (f"drink:{a}" for a in DRINK)


# What the model writes instead of an entry's name
ALIASES = {"hey": "hello", "hi": "hello", "greet": "hello", "haha": "laugh", "lol": "laugh", "ok": "agree",
           "yes": "agree", "no": "disagree", "thank_you": "thanks", "apologize": "sorry", "bye": "goodbye",
           "drink:cabernet": "drink:red_wine", "drink:wine": "drink:red_wine", "drink:chardonnay": "drink:white_wine"}


def known(part):
    """A menu entry for what the model wrote: itself, or the entry it nearly spelled
    ("reveal:trip_cheating" -> "reveal:trip_cheated"); None if nothing is close."""
    part = ALIASES.get(part, part)
    if phrase(part):
        return part
    near = difflib.get_close_matches(part, list(every_act()), n=1, cutoff=0.8)
    return near[0] if near else None


_THIRD = {"is", "was", "isnt", "isn", "wasnt", "does", "doesnt", "did", "didnt", "has", "hasnt", "had", "seems",
          "always", "never", "really", "just", "and", "or", "loves", "hates", "likes", "said", "says", "thinks",
          "looks", "acts", "sounds", "wants", "needs", "should"}


def spoken_to(typed, name):
    """Does the guest speak TO this host ("grace, ...", "grace you look...", "...right, trip?"), not just
    about them ("grace does seem spoiled", said to Trip)?"""
    n = re.escape(name.lower())
    t = typed.lower()
    m = re.search(rf"^\W*((hey|hi|hello|yo|oh|thanks|thank you|sorry|ok|okay|well|so|and|yeah|yes|no|nah|um|"
                  rf"look|listen)\W+)*{n}\b('s\b)?\W*(\w+)?", t)
    if m:   # the name opens the sentence: addressed, unless a third-person verb follows it
        return not (m.group(3) or m.group(4) in _THIRD)
    return bool(re.search(rf"[,\s]{n}\W*$", t))


def render(answer, typed=None):
    """The model's answer ("agree, about:paintings @trip", or "none") -> (text for the parser, acts).
    At most two acts. Text is "" when nothing on the menu fits. With the guest's words (typed), a host
    is addressed only if the guest named them."""
    answer = answer.strip().lower().strip("`'\". ")
    to = None
    m = re.search(r"@\s*(grace|trip)\b", answer)
    if m:
        to = m.group(1).capitalize()
        answer = answer[:m.start()] + answer[m.end():]
        if typed is not None and not spoken_to(typed, to):
            to = None
    answer = re.sub(r"@\s*(\w+)", lambda m: f"about:{m.group(1)}" if m.group(1) in ABOUT else m.group(0), answer)
    parts = []
    for part in re.split(r"[,;\s]+", answer):
        part = part.strip().replace("-", "_")
        kind, _, arg = part.partition(":")
        if kind in ("beautiful", "ugly", "yuck", "wow") and arg in ABOUT:   # "ugly:decorating"
            parts += [f"about:{arg}", kind]
        else:
            parts.append(part)
    acts = []
    for part in parts:
        part = known(part) if part and part != "none" else None
        if part and part.startswith("drink:") and any(a.startswith("drink:") for a in acts):
            continue   # one drink ("drink:white_wine, drink:beer, drink:scotch...")
        kind, _, host = (part or "").partition(":")
        if kind in ABOUT_HOST and host and typed is not None and spoken_to(typed, host):
            part, to = kind, host.capitalize()   # "grace you look beautiful": to her, not about her
        if part and part not in acts:
            acts.append(part)
    acts = acts[:2]
    if not acts:
        return "", []
    text = " ".join(phrase(a)[0] for a in acts)
    if to and not any(a.partition(":")[0] in ABOUT_HOST and ":" in a for a in acts):
        # ("Trip, Grace is mean." reads as criticizing both of them)
        text = f"{to}, {text[0].lower()}{text[1:]}" if not text.startswith(("I ", "I'", "Grace", "Trip")) \
            else f"{to}, {text}"
    return text, acts


# What a few entries mean, for the model; the rest say it in their names.
GLOSS = {
    "confused": "the guest doesn't follow", "tell_me_more": "asks them to go on or explain",
    "yuck": "something is awful or gross", "wow": "something is impressive or exciting",
    "praise": "nice words about Grace or Trip", "criticize": "a complaint about Grace or Trip",
    "insult": "rude words at Grace or Trip", "support": "comfort, sympathy, a hug",
    "ally": "takes Grace's or Trip's side", "beautiful": "with about: the thing is nice",
    "ugly": "with about: the thing is ugly",
    "about:paintings": "Grace's paintings on the walls", "about:italy_photo": "the photo from their trip to Italy",
    "about:wedding_photo": "their wedding photo", "about:work_table": "their work table",
    "about:job": "their work", "about:affair": "cheating", "about:art": "Grace's art",
    "you_are:lying": "lying, fake, dishonest", "you_are:afraid": "scared, embarrassed",
    "you_are:to_blame": "it's their fault", "you_are:controlling": "bossy",
    "you_are:controlled": "pushed around", "you_are:unloved": "lonely",
    "you_should:talk": "communicate", "you_should:separate": "split up",
    "drink:red_wine": "red wine, cabernet", "drink:white_wine": "white wine, chardonnay",
    "praise:grace": "compliments only", "praise:trip": "compliments only",
    "criticize:grace": "complaints: spoiled, uptight, snobby, mean",
    "criticize:trip": "complaints: show-off, pushy, fake, mean",
}


def menu():
    """The menu as the model sees it: every entry, exactly as it's to be written."""
    def line(entries):
        return ", ".join(f"{e} ({GLOSS[e]})" if e in GLOSS else e for e in entries)
    every = list(every_act())
    return "\n".join([
        "Plain acts: " + line(SIMPLE),
        "Said about one host to the other (\"grace is so spoiled\" is criticize:grace): "
        + line(f"{k}:{h}" for k in ABOUT_HOST for h in ("grace", "trip")),
        "The guest brings up, asks about or comments on: " + line(e for e in every if e.startswith("about:")),
        "The guest tells Grace or Trip what they are (or aren't): "
        + line(e for e in every if e.startswith("you_are")),
        "The guest advises them: " + line(e for e in every if e.startswith("you_should")),
        "The guest says a hidden truth about them: " + line(e for e in every if e.startswith("reveal:")),
        "The guest asks for a drink: " + line(e for e in every if e.startswith("drink:")),
    ])
