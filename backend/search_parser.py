"""Deterministic customer-search parsing for LEIA (no AI provider calls).

Principle: AI understands intent only when needed; PostgreSQL provides property facts.
This module turns simple English, Telugu and Telugu-English (romanised) searches into
validated filter updates. Anything it cannot fully account for is returned as
``needs_ai`` so the caller can decide whether a (budget-guarded) AI call is allowed.

It never invents values: unsupported requirements (lift, possession status, RERA, …)
are reported back so the reply can say they are not verified, instead of being dropped.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

LAKH = 100_000
CRORE = 10_000_000
DIRECTIONS = ("north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest")

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
# Canonical locality -> spelling variants (romanised + Telugu script).
LOCALITIES: dict[str, tuple[str, ...]] = {
    "Vepagunta": ("vepagunta", "vepagunt", "vepa gunta", "vepagunda", "veepagunta", "వేపగుంట"),
    "Lakshmipuram": ("lakshmipuram", "laxmipuram", "lakshmi puram", "lakshmipuram colony", "లక్ష్మీపురం", "లక్ష్మిపురం"),
    "Chinthalagraharam": ("chinthalagraharam", "chintalagraharam", "chintalaagraharam", "chinthala agraharam",
                          "chintala agraharam", "chintalagrahram", "చింతలగ్రహారం", "చింతల అగ్రహారం"),
    "Pinagadi": ("pinagadi", "pinagaadi", "pinnagadi", "పినగాడి"),
    "Pedagadi": ("pedagadi", "pedagaadi", "peda gadi", "పెదగాడి"),
    "Rampuram": ("rampuram", "raampuram", "ram puram", "రాంపురం", "రామపురం"),
    "PM Palem": ("pm palem", "p m palem", "p.m. palem", "p.m.palem", "pmpalem", "pothinamallayya palem",
                 "pothinamallayyapalem", "పీఎం పాలెం", "పి ఎం పాలెం", "పోతినమల్లయ్యపాలెం"),
    "Madhurawada": ("madhurawada", "madhuravada", "madhurwada", "madurawada", "మధురవాడ"),
    "MVP Colony": ("mvp colony", "m v p colony", "mvp", "m.v.p colony", "ఎంవీపీ కాలనీ", "ఎం వి పి కాలనీ"),
    "Gajuwaka": ("gajuwaka", "gajuvaka", "gajwaka", "గాజువాక"),
    "Lankelapalem": ("lankelapalem", "lankalapalem", "lankelapalam", "లంకెలపాలెం"),
    "Pendurthi": ("pendurthi", "pendurti", "pendurthy", "పెందుర్తి"),
    "Simhachalam": ("simhachalam", "simhachalem", "సింహాచలం"),
    "Kommadi": ("kommadi", "komadi", "కొమ్మాది"),
    "Yendada": ("yendada", "endada", "ఎండాడ"),
    "Rushikonda": ("rushikonda", "rishikonda", "రుషికొండ"),
    "Seethammadhara": ("seethammadhara", "sitammadhara", "seetammadhara", "సీతమ్మధార"),
    "Dwaraka Nagar": ("dwaraka nagar", "dwarakanagar", "dwarka nagar", "ద్వారకానగర్", "ద్వారకా నగర్"),
    "Akkayyapalem": ("akkayyapalem", "akkayapalem", "అక్కయ్యపాలెం"),
    "Arilova": ("arilova", "ఆరిలోవ"),
    "Kurmannapalem": ("kurmannapalem", "kurmanapalem", "కూర్మన్నపాలెం"),
    "Duvvada": ("duvvada", "duvada", "దువ్వాడ"),
    "Anakapalli": ("anakapalli", "anakapalle", "అనకాపల్లి"),
    "Gopalapatnam": ("gopalapatnam", "గోపాలపట్నం"),
    "NAD Junction": ("nad junction", "nad", "ఎన్ఏడీ"),
    "Marripalem": ("marripalem", "మర్రిపాలెం"),
    "Sujatha Nagar": ("sujatha nagar", "sujathanagar", "సుజాతనగర్"),
    "Kapuluppada": ("kapuluppada", "కాపులుప్పాడ"),
    "Anandapuram": ("anandapuram", "ఆనందపురం"),
    "Bheemili": ("bheemili", "bheemunipatnam", "భీమిలి"),
    "Visakhapatnam": ("visakhapatnam", "vizag", "vishakapatnam", "visakapatnam", "vishakhapatnam", "vsp",
                      "విశాఖపట్నం", "విశాఖ", "వైజాగ్"),
}
CITY = "Visakhapatnam"

FACING_TERMS: dict[str, tuple[str, ...]] = {
    "northeast": ("north east", "north-east", "northeast", "n e", "ne", "eesanyam", "eeshanyam", "ishanyam",
                  "eesanya", "ishanya", "ఈశాన్యం", "ఈశాన్య"),
    "northwest": ("north west", "north-west", "northwest", "nw", "vayuvyam", "vayavyam", "vayuvya", "వాయువ్యం",
                  "వాయువ్య"),
    "southeast": ("south east", "south-east", "southeast", "se", "agneyam", "aagneyam", "agneya", "ఆగ్నేయం",
                  "ఆగ్నేయ"),
    "southwest": ("south west", "south-west", "southwest", "sw", "nairuti", "nairuthi", "nairutyam", "నైరుతి"),
    "north": ("north", "uttaram", "uttara", "utharam", "uttharam", "ఉత్తరం", "ఉత్తర"),
    "south": ("south", "dakshinam", "dakshina", "dhakshinam", "దక్షిణం", "దక్షిణ"),
    "east": ("east", "turpu", "thurpu", "toorpu", "తూర్పు"),
    "west": ("west", "padamara", "paschimam", "pashchimam", "padamati", "పడమర", "పశ్చిమం", "పశ్చిమ"),
}
TYPE_TERMS: dict[str, tuple[str, ...]] = {
    "independent_house": ("independent houses", "independent house", "individual houses", "individual house",
                          "independent home", "independent homes", "ఇండిపెండెంట్ హౌస్", "ఇండిపెండెంట్ ఇల్లు",
                          "స్వతంత్ర ఇల్లు"),
    "flat": ("apartments", "apartment", "flats", "flat", "అపార్ట్మెంట్", "అపార్ట్‌మెంట్", "ఫ్లాట్"),
    "plot": ("plots", "plot", "open plot", "site", "sites", "land", "ప్లాట్", "స్థలం", "స్థలాలు"),
}
AMENITY_FILTERS = {  # supported by real columns
    "parking": ("car parking", "parking", "పార్కింగ్"),
    "power_backup": ("power backup", "generator backup", "generator", "inverter backup", "పవర్ బ్యాకప్"),
}
UNSUPPORTED_TERMS: dict[str, tuple[str, ...]] = {
    "lift": ("lift", "elevator", "లిఫ్ట్"),
    "gym": ("gym", "gymnasium", "జిమ్"),
    "swimming pool": ("swimming pool", "pool"),
    "security": ("24x7 security", "security", "cctv", "watchman"),
    "gated community": ("gated community", "gated"),
    "clubhouse": ("clubhouse", "club house"),
    "garden": ("garden", "park facing", "children's park"),
    "possession status": ("ready to move", "ready-to-move", "ready for possession", "immediate possession",
                          "under construction", "under-construction", "new launch", "possession",
                          "రెడీ టు మూవ్", "గృహప్రవేశానికి సిద్ధం"),
    "RERA registration": ("rera approved", "rera registered", "rera registration", "rera number", "rera"),
    "legal approvals": ("vmrda approved layout", "vmrda approved", "vuda approved", "gvmc approved", "dtcp approved", "approved layout",
                        "legal approval", "approvals", "clear title", "title clear"),
    "builder": ("builder", "developer", "బిల్డర్"),
    "loan eligibility": ("loan", "emi", "home loan", "లోన్"),
    "nearby schools/hospitals": ("near school", "near schools", "schools nearby", "near hospital",
                                 "hospitals nearby", "near metro", "near bus stop", "school daggara",
                                 "hospital daggara", "schools", "school", "hospitals", "hospital",
                                 "స్కూల్", "ఆసుపత్రి"),
    "rent": ("for rent", "rent", "rental", "lease", "అద్దె"),
}
GREETINGS = re.compile(
    r"^\s*(?:hi+|hello+|hey+|namaste|namaskaram|good (?:morning|afternoon|evening)|thanks?|thank you|"
    r"నమస్తే|నమస్కారం|హలో|ధన్యవాదాలు)[\s!.,🙏]*$", re.I)
RESET = re.compile(r"^\s*(?:start (?:over|again)|reset(?: (?:search|filters))?|clear (?:all )?(?:filters|search)|"
                   r"new search|మళ్ళీ మొదలు(?:పెట్టు)?|కొత్త సెర్చ్)[\s!.]*$", re.I)
SHOW_ALL = re.compile(r"^\s*(?:show|list|see)(?: me)? all(?: the)? (?:properties|listings|homes|flats)[\s!.]*$"
                      r"|^\s*అన్ని (?:ప్రాపర్టీలు|ఇళ్ళు) (?:చూపించు|చూపించండి)[\s!.]*$", re.I)
BLOCKED = re.compile(
    r"ignore (?:all |the |your )?(?:previous|prior|above) (?:instructions|rules|prompts?)|system prompt|"
    r"developer mode|jailbreak|you are now|act as (?:a|an|my) |pretend (?:to be|you are)|reveal (?:your|the) |"
    r"api[_ ]?key|secret key|password|access token|\bdrop\s+table\b|\bdelete\s+from\b|\bselect\s+\*|"
    r"\binsert\s+into\b|<\s*script|\bunion\s+select\b|show (?:me )?(?:unpublished|rejected|draft|private) "
    r"(?:listings|properties)|owner(?:'s)? (?:phone|number|contact)", re.I)
IRRELEVANT = re.compile(
    r"\b(?:write (?:a|me) (?:poem|story|essay|song|code)|weather|cricket|ipl|movie|recipe|prime minister|"
    r"stock price|bitcoin|crypto|translate this|python code|javascript)\b", re.I)
ABUSE = re.compile(r"\b(?:idiot|stupid|bloody|bastard|fuck\w*|shit)\b", re.I)

STOPWORDS = set("""
show me find search searching looking look for want need i im i'm a an the some with in at of to and please pls plz
properties property listings listing homes home house houses options option available sale buy buying purchase
which are there do you have get give that is it its can could would like any my our we us
budget price priced cost costing rs rupees inr range around within total
make change set update increase raise reduce lower decrease move instead now also only just then
facing face faced facing direction directions sorted sort by order ordered first wise
bhk bhks bedroom bedrooms bed beds room rooms br
sq sqft sft ft feet square area size carpet built up builtup super
city locality location area place visakhapatnam
lo loni ki ku ni kavali kaavali kavalandi chupinchu chupinchandi choopinchu choopinchandi unna unnaayi unnavi
illu illulu intlu kosam naaku naku dhara dharalo varaku madhya mariyu oka emaina andi undi untey unte gaani
corner latest new these those built who what am eligible eligibility
""".split())
TELUGU_STOPWORDS = {
    "లో", "లోని", "కావాలి", "కావాలండి", "చూపించు", "చూపించండి", "ఉన్న", "ఉన్నవి", "ఇళ్ళు", "ఇల్లు", "ఇల్లులు",
    "కోసం", "నాకు", "ధర", "వరకు", "మధ్య", "మరియు", "ఒక", "ఏదైనా", "అండి", "ఫేసింగ్", "దిశ", "బడ్జెట్",
    "ఇంటి", "ఇంటికి", "ప్రాపర్టీలు", "ప్రాపర్టీ", "ఉండాలి", "తో", "కి", "కు", "లు", "గల", "ఉన్నాయా", "ఏమైనా",
    "చెప్పండి", "రూపాయలు", "బీహెచ్కే", "బిహెచ్కె", "బెడ్రూమ్", "బెడ్", "రూమ్", "గదుల", "గదులు", "పడక",
}
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "single": 1, "double": 2,
    "oka": 1, "rendu": 2, "moodu": 3, "mudu": 3, "naalugu": 4, "nalugu": 4, "aidu": 5,
    "ఒక": 1, "రెండు": 2, "మూడు": 3, "నాలుగు": 4, "ఐదు": 5,
}
TELUGU_DIGITS = str.maketrans("౦౧౨౩౪౫౬౭౮౯", "0123456789")

AMOUNT = r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>crores?|crs?|cr\.?|కోట్లు|కోట్ల|కోటి|lakhs?|lacs?|lac|lks?|l\b|లక్షలు|లక్షల|లక్ష)?"
MAX_WORDS = (r"under|below|less than|upto|up to|within|maximum|max|not more than|budget(?: is| of)?|"
             r"increase (?:my )?budget to|raise (?:my )?budget to|reduce (?:my )?budget to|lower (?:my )?budget to|"
             r"budget to|lopu|lopala|varaku|లోపు|లోపల|వరకు|కంటే తక్కువ|తక్కువ")
MIN_WORDS = r"above|over|more than|at least|atleast|minimum|min|starting from|paina|పైన|కంటే ఎక్కువ|ఎక్కువ"
AREA_UNIT = r"(?:sq\.?\s*ft\.?|sqft|sft|square\s*feet|sq\s*feet|చదరపు అడుగులు|చ\.అ)"


@dataclass
class ParseResult:
    kind: str  # search | show_all | reset | conversation | clarify | blocked | needs_ai | too_long
    updates: dict = field(default_factory=dict)
    unsupported: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    question: str | None = None
    language: str = "en"
    leftover: list[str] = field(default_factory=list)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).translate(TELUGU_DIGITS)
    text = text.replace("₹", " rs ").replace("’", "'").replace("–", "-").replace("—", "-")
    text = re.sub(r"(\d),(\d)", r"\1\2", text)  # 65,00,000 -> 6500000 ; 1,200 -> 1200
    text = re.sub(r"(\d),(\d)", r"\1\2", text)
    text = re.sub(r"(\d)\s*(bhk|bed|br)\b", r"\1 \2", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def language_of(text: str) -> str:
    telugu = sum(1 for ch in text if "ఀ" <= ch <= "౿")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if telugu and latin:
        return "mixed"
    if telugu:
        return "te"
    romanised = re.search(r"\b(?:lo|kavali|chupinchu|lopu|paina|illu|kosam|naaku|unna|varaku)\b", text, re.I)
    return "mixed" if romanised else "en"


def _term_pattern(term: str) -> str:
    if any("ఀ" <= ch <= "౿" for ch in term):
        return re.escape(term)
    escaped = re.escape(term).replace(r"\ ", r"[\s.-]*")
    return rf"(?<![a-z0-9]){escaped}(?:lo|loni|ki|ku|daggara)?(?![a-z0-9])"


def _rupees(num: str, unit: str | None) -> int | None:
    value = float(num)
    unit = (unit or "").lower().rstrip(".")
    if unit.startswith("cr") or unit in {"కోట్లు", "కోట్ల", "కోటి"}:
        return round(value * CRORE)
    if unit.startswith(("lakh", "lac", "lk")) or unit == "l" or unit in {"లక్షలు", "లక్షల", "లక్ష"}:
        return round(value * LAKH)
    if value >= 100_000:  # already in rupees
        return round(value)
    return None  # bare small number: ambiguous unit


class _Text:
    """Lower-cased working copy; consumed spans are blanked out for coverage checks."""

    def __init__(self, text: str):
        self.text = text.lower()

    def take(self, pattern: str, flags=re.I) -> list[re.Match]:
        matches = list(re.finditer(pattern, self.text, flags))
        for m in reversed(matches):
            self.text = self.text[:m.start()] + " " * (m.end() - m.start()) + self.text[m.end():]
        return matches

    def find(self, pattern: str) -> re.Match | None:
        return re.search(pattern, self.text, re.I)


def _words_to_numbers(text: str) -> str:
    for word, value in NUMBER_WORDS.items():
        pattern = re.escape(word) if not word.isascii() else rf"\b{word}\b"
        text = re.sub(pattern + r"(?=\s*(?:bhk|bed|br|బీహెచ్కే|బెడ్|పడక|bedroom))", str(value), text, flags=re.I)
    return text


def parse(message: str) -> ParseResult:
    raw = normalize(message)
    language = language_of(raw)
    result = ParseResult(kind="search", language=language)

    if not raw:
        result.kind = "conversation"
        return result
    if BLOCKED.search(raw):
        result.kind = "blocked"
        return result
    if ABUSE.search(raw):
        result.kind = "blocked"
        return result
    if IRRELEVANT.search(raw):
        result.kind = "blocked"
        result.question = "irrelevant"
        return result
    if GREETINGS.match(raw):
        result.kind = "conversation"
        return result
    if RESET.match(raw):
        result.kind = "reset"
        return result
    if SHOW_ALL.match(raw):
        result.kind = "show_all"
        return result

    t = _Text(_words_to_numbers(raw))
    updates = result.updates

    # "any X" clears.
    clears = {
        "facing": r"\b(?:any|no) (?:facing|direction)(?: preference)?(?: is fine| is ok)?\b",
        "location": r"\b(?:any (?:location|locality|area)|anywhere)\b",
        "bedrooms": r"\bany (?:bhk|bedrooms?|number of bedrooms)\b",
        "property_type": r"\bany (?:property )?type\b",
        "price": r"\b(?:no budget(?: limit)?|any budget|any price)\b",
    }
    for key, pattern in clears.items():
        if t.take(pattern):
            if key == "price":
                updates["min_price"] = None
                updates["max_price"] = None
            else:
                updates[key] = None

    # Unsupported requirements are reported, never silently ignored.
    for label, terms in UNSUPPORTED_TERMS.items():
        for term in sorted(terms, key=len, reverse=True):
            if t.take(_term_pattern(term)):
                if label not in result.unsupported:
                    result.unsupported.append(label)
    if "rent" in result.unsupported:
        result.kind = "clarify"
        result.question = ("LEIA currently lists properties for sale only. Rental listings aren't available yet. "
                           "Would you like to search homes for sale instead?")
        return result

    # Area (before price so "1200 sq ft" is not read as money).
    area_range = t.take(rf"(?:between\s+)?(\d{{3,5}})\s*{AREA_UNIT}?\s*(?:-|to|and|నుండి)\s*(\d{{3,5}})\s*{AREA_UNIT}")
    if area_range:
        low, high = sorted(int(v) for v in area_range[0].groups())
        updates["min_area_sqft"], updates["max_area_sqft"] = low, high
    else:
        for m in t.take(rf"(?:(?P<q1>{MAX_WORDS}|{MIN_WORDS})\s+)?(?P<n>\d{{3,5}})\s*{AREA_UNIT}(?:\s+(?P<q2>{MAX_WORDS}|{MIN_WORDS}))?"):
            q = (m.group("q1") or m.group("q2") or "").strip()
            value = int(m.group("n"))
            if re.fullmatch(MIN_WORDS, q, re.I):
                updates["min_area_sqft"] = value
            elif re.fullmatch(MAX_WORDS, q, re.I):
                updates["max_area_sqft"] = value
            else:  # "1200 sq ft": +/- 10% window, stated in the reply
                updates["min_area_sqft"], updates["max_area_sqft"] = round(value * 0.9), round(value * 1.1)
                result.notes.append(f"I searched {round(value * 0.9):,}–{round(value * 1.1):,} sq ft for “{value:,} sq ft”.")
        t.take(AREA_UNIT)

    # Bedrooms.
    bhk = t.take(r"(?<![\d.])(\d{1,2})\s*(?:bhk|bed(?:room)?s?|br|బీహెచ్కే|బిహెచ్కె|బెడ్\s*రూమ్|బెడ్రూమ్|పడక\s*గదుల?|పడక\s*గదులు)(?![a-z])")
    alt_bhk = re.search(r"\d\s*(?:or|/|-|లేదా)\s*\d\s*(?:bhk|bed)", t.text + " " + raw.lower())
    if alt_bhk and len({m.group(1) for m in bhk}) <= 1:
        result.kind = "clarify"
        result.question = "LEIA searches one bedroom count at a time. Which should I use, for example 2 BHK or 3 BHK?"
        return result
    if len({m.group(1) for m in bhk}) > 1:
        result.kind = "clarify"
        result.question = "LEIA searches one bedroom count at a time. Which bedroom count should I use?"
        return result
    if bhk:
        value = int(bhk[0].group(1))
        if not 1 <= value <= 20:
            result.kind = "clarify"
            result.question = "Please give a bedroom count between 1 and 20."
            return result
        updates["bedrooms"] = value

    # Price: ranges first.
    rng = t.take(rf"(?:between\s+|from\s+)?(?:rs\s*)?(?P<a>\d+(?:\.\d+)?)\s*(?P<ua>crores?|crs?|cr|lakhs?|lacs?|lac|l\b|లక్షల|లక్షలు|లక్ష|కోట్ల|కోటి)?"
                 rf"\s*(?:-|to|and|నుండి|నుంచి)\s*(?:rs\s*)?(?P<b>\d+(?:\.\d+)?)\s*(?P<ub>crores?|crs?|cr|lakhs?|lacs?|lac|l\b|లక్షల|లక్షలు|లక్ష|కోట్ల|కోటి)"
                 rf"(?:\s*(?:మధ్య|madhya|lo|లో))?")
    if rng:
        m = rng[0]
        a = _rupees(m.group("a"), m.group("ua") or m.group("ub"))
        b = _rupees(m.group("b"), m.group("ub"))
        if a is None or b is None:
            return _ambiguous_money(result)
        if a > b:
            result.kind = "clarify"
            result.question = "Your minimum budget is higher than your maximum. What price range should I use?"
            return result
        updates["min_price"], updates["max_price"] = a, b
    elif bare := t.take(r"(?:between\s+|from\s+)?(?:rs\s*)?(\d{6,})\s*(?:-|to|and)\s*(?:rs\s*)?(\d{6,})"):
        a, b = (int(v) for v in bare[0].groups())
        if a > b:
            result.kind = "clarify"
            result.question = "Your minimum budget is higher than your maximum. What price range should I use?"
            return result
        updates["min_price"], updates["max_price"] = a, b
    else:
        around = t.take(rf"(?:around|about|approx(?:imately)?|near(?:ly)?|సుమారు|daggara)\s+(?:rs\s*)?{AMOUNT}")
        if around:
            value = _rupees(around[0].group("num"), around[0].group("unit"))
            if value is None:
                return _ambiguous_money(result)
            updates["min_price"], updates["max_price"] = round(value * 0.9), round(value * 1.1)
            result.notes.append(f"I used {_fmt_money(round(value * 0.9))}–{_fmt_money(round(value * 1.1))} for “around {_fmt_money(value)}”.")
        for m in t.take(rf"(?:(?P<q1>{MAX_WORDS}|{MIN_WORDS})\s+)?(?:rs\s*)?{AMOUNT}(?:\s*(?P<q2>{MAX_WORDS}|{MIN_WORDS}))?"):
            num, unit = m.group("num"), m.group("unit")
            q = (m.group("q1") or m.group("q2") or "").strip()
            if not unit and not q and float(num) < 100_000:
                # A stray number (e.g. "listing 2") — leave for coverage check.
                t.text = t.text[:m.start()] + m.group(0) + t.text[m.end():]
                continue
            value = _rupees(num, unit)
            if value is None:
                return _ambiguous_money(result)
            if re.fullmatch(MIN_WORDS, q, re.I):
                updates["min_price"] = value
                updates.setdefault("max_price", None)
            else:
                updates["max_price"] = value
                updates.setdefault("min_price", None)
                if not q:
                    result.notes.append(f"I treated {_fmt_money(value)} as your maximum budget.")
        t.take(rf"\b(?:{MAX_WORDS}|{MIN_WORDS})\b") if updates.get("max_price") or updates.get("min_price") else None

    # Facing (longest terms first so "north east" wins over "north").
    found_dirs: list[str] = []
    for direction, terms in FACING_TERMS.items():
        for term in sorted(terms, key=len, reverse=True):
            pattern = _term_pattern(term)
            if len(term) <= 2:  # ne/nw/se/sw only next to "facing"
                pattern = rf"(?<![a-z]){re.escape(term)}(?=\s*(?:-|\s)?facing)"
            if t.take(pattern):
                found_dirs.append(direction)
    found_dirs = list(dict.fromkeys(found_dirs))
    if len(found_dirs) > 1:
        result.kind = "clarify"
        result.question = "LEIA searches one facing direction at a time. Which direction should I use?"
        return result
    if found_dirs:
        updates["facing"] = found_dirs[0]

    # Property type.
    types = []
    for ptype, terms in TYPE_TERMS.items():
        for term in sorted(terms, key=len, reverse=True):
            if t.take(_term_pattern(term)):
                types.append(ptype)
    types = list(dict.fromkeys(types))
    if len(types) > 1:
        result.kind = "clarify"
        result.question = "Should I search flats, independent houses, or plots? LEIA searches one type at a time."
        return result
    if types:
        updates["property_type"] = types[0]

    # Supported amenities.
    for key, terms in AMENITY_FILTERS.items():
        for term in sorted(terms, key=len, reverse=True):
            if t.take(_term_pattern(term)):
                updates[key] = True

    # Sorting.
    sorts = {
        "price_asc": r"\b(?:cheapest(?: first)?|lowest price|price low to high|low to high|sorted by price|sort by price|"
                     r"by price|price wise|తక్కువ ధర)\b",
        "price_desc": r"\b(?:most expensive|highest price|price high to low|high to low|costliest)\b",
        "newest": r"\b(?:newest|latest listings?|recently added)\b",
        "area_desc": r"\b(?:largest|biggest|most spacious)\b",
    }
    for key, pattern in sorts.items():
        if t.take(pattern):
            updates["sort"] = key

    # Localities.
    localities = []
    excluded = re.search(r"\b(?:not in|except|excluding|other than|apart from)\b", t.text)
    for canonical, variants in LOCALITIES.items():
        for variant in sorted(variants, key=len, reverse=True):
            pattern = _term_pattern(variant)
            if variant in {"mvp", "nad", "vsp"}:
                pattern = rf"(?<![a-z]){variant}(?![a-z])"
            if t.take(pattern):
                localities.append(canonical)
    localities = list(dict.fromkeys(localities))
    if excluded and localities:
        result.kind = "clarify"
        result.question = "LEIA can't exclude areas yet. Which single locality should I search?"
        return result
    city_only = [loc for loc in localities if loc != CITY]
    if len(city_only) > 1:
        result.kind = "clarify"
        result.question = f"LEIA searches one locality at a time. Should I search {city_only[0]} or {city_only[1]}?"
        return result
    if city_only:
        updates["location"] = city_only[0]
    elif localities:
        updates["location"] = CITY

    # Coverage: anything left that we did not understand needs AI (or clarification).
    leftover = []
    for token in re.findall(r"[\w'ఀ-౿]+", t.text):
        if token in STOPWORDS or token in TELUGU_STOPWORDS:
            continue
        if any("ఀ" <= ch <= "౿" for ch in token) and len(token) <= 3:
            continue  # Telugu case suffix fragments (లో, కి, లు …)
        if token.isdigit() and token in {"0"}:
            continue
        leftover.append(token)
    result.leftover = leftover

    meaningful = {k: v for k, v in updates.items()}
    if leftover:
        result.kind = "needs_ai"
    elif not meaningful and not result.unsupported:
        result.kind = "needs_ai"
    return result


def _ambiguous_money(result: ParseResult) -> ParseResult:
    result.kind = "clarify"
    result.question = "Is that amount in lakhs or crores? For example, “under 65 lakh” or “under 1.2 crore”."
    return result


def _fmt_money(value: int) -> str:
    if value >= CRORE:
        return f"₹{value / CRORE:.2f}".rstrip("0").rstrip(".") + " crore"
    if value >= LAKH:
        return f"₹{value / LAKH:.2f}".rstrip("0").rstrip(".") + " lakh"
    return f"₹{value:,}"


def location_keys(location: str | None) -> list[str] | None:
    """Normalised match keys for a requested location (all known spellings)."""
    if not location:
        return None
    key = re.sub(r"[^a-z0-9ఀ-౿]", "", location.lower())
    for canonical, variants in LOCALITIES.items():
        names = {canonical.lower(), *variants}
        keys = {re.sub(r"[^a-z0-9ఀ-౿]", "", n.lower()) for n in names}
        if key in keys:
            return sorted(k for k in keys if k)
    return [key] if key else None


def describe(filters: dict) -> str:
    parts = []
    if filters.get("bedrooms"):
        parts.append(f"{filters['bedrooms']} BHK")
    if filters.get("property_type"):
        parts.append({"flat": "flats", "independent_house": "independent houses", "plot": "plots"}[filters["property_type"]])
    if filters.get("facing"):
        parts.append(f"{filters['facing']}-facing")
    if filters.get("location"):
        parts.append(f"in {filters['location']}")
    lo, hi = filters.get("min_price"), filters.get("max_price")
    if lo and hi:
        parts.append(f"{_fmt_money(lo)}–{_fmt_money(hi)}")
    elif hi:
        parts.append(f"up to {_fmt_money(hi)}")
    elif lo:
        parts.append(f"from {_fmt_money(lo)}")
    alo, ahi = filters.get("min_area_sqft"), filters.get("max_area_sqft")
    if alo and ahi:
        parts.append(f"{alo:,}–{ahi:,} sq ft")
    elif ahi:
        parts.append(f"up to {ahi:,} sq ft")
    elif alo:
        parts.append(f"at least {alo:,} sq ft")
    if filters.get("parking"):
        parts.append("with parking")
    if filters.get("power_backup"):
        parts.append("with power backup")
    return ", ".join(parts) if parts else "all approved listings"
