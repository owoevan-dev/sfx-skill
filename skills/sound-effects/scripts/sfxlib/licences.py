"""The licences recordings come under, what each asks of a sound made with them, and how a pack's own text files are
read for them.

Licences are named by their SPDX identifiers (SPDX License List, spdx.org/licenses) where one exists; 'public
domain' covers works marked or certified as such, 'own' the user's own recordings, 'other' any licence the skill does
not know, whose terms the user has to read. What each asks comes from its legal code:

- CC0 1.0 (Creative Commons, CC0 1.0 Universal): the affirmer waives all copyright; nothing is asked. Public domain,
  the Unlicense (unlicense.org) and the user's own recordings ask nothing either.
- CC BY (Attribution): credit the creator, give the licence and say what was changed.
- ShareAlike: adapted material must be shared under the same licence or one the legal code allows as compatible. A
  sound made from a recording is adapted material, so the sound itself would have to carry it.
- NonCommercial: no use primarily for commercial advantage.
- NoDerivatives: adapted material may be made but not shared, so a sound made from it cannot go into a game; such
  recordings are refused.
- Sampling Plus 1.0 (a retired Creative Commons licence still found on Freesound): sampling and transforming are
  allowed with credit; other terms are in its legal code.

A recording under a licence with conditions can still be layered: the render, its audition page and every export
manifest then say which conditions the sounds carry and give the credits.

Reading a pack's files. Which licence holds is the person's to say; this only holds their word against the files.
`detect` finds every licence a text names (URLs, SPDX identifiers, full names, each version); `doubts` finds what would
contradict a free licence: restrictions, terms of a licence this module does not know, a Creative Commons name without a
version, a free licence mentioned without being granted ('not public domain'). These patterns are this skill's own and
cannot understand a licence, so they never grant one: they only stop an import until the person has seen the lines."""
import re

FREE = ('CC0-1.0', 'public domain', 'own', 'Unlicense')
_VERSIONS = ('1.0', '2.0', '2.5', '3.0', '4.0')
_ELEMENTS = {'BY': ['attribution'], 'BY-SA': ['attribution', 'share-alike'], 'BY-NC': ['attribution', 'non-commercial'],
             'BY-NC-SA': ['attribution', 'non-commercial', 'share-alike'], 'BY-ND': ['attribution', 'no-derivatives'],
             'BY-NC-ND': ['attribution', 'non-commercial', 'no-derivatives']}
CONDITIONS = {'CC0-1.0': [], 'public domain': [], 'own': [], 'Unlicense': [],
              'CC-Sampling-Plus-1.0': ['attribution', 'other terms'], 'other': ['other terms']}
CONDITIONS.update({f'CC-{kind}-{v}': list(asks) for kind, asks in _ELEMENTS.items() for v in _VERSIONS})
MEANING = {
    'attribution': 'credit the creator, give the licence and say what was changed',
    'share-alike': 'a sound made from it must be shared under the same licence, or a compatible one its legal code '
                   'allows',
    'non-commercial': 'the sounds may not be used commercially',
    'no-derivatives': 'adapted material may not be shared',
    'other terms': 'read the licence: it has terms of its own',
}
ACCEPTED = ('own', 'CC0-1.0', 'public domain', 'Unlicense', 'CC-BY-4.0', 'CC-BY-SA-4.0', 'CC-BY-NC-4.0', 'other')

_V = r'(1\.0|2\.0|2\.5|3\.0|4\.0)'


def _cc_url(m):
    kind = m.group(1).upper()
    return f"CC-{'BY-NC-ND' if kind == 'BY-ND-NC' else kind}-{m.group(2)}"


def _cc_id(m):
    return f"CC-{re.sub(r'[-_ ]+', '-', m.group(1).upper())}-{m.group(2)}"


def _cc_long(m):
    kind = 'BY' + ('-NC' if m.group('nc') else '') + ('-SA' if m.group('sa') else '') + ('-ND' if m.group('nd') else '')
    return f"CC-{kind}-{m.group('v')}"


_NAMED = [
    (re.compile(r'creativecommons\.org/publicdomain/zero/1\.0', re.I), lambda m: 'CC0-1.0'),
    (re.compile(r'creativecommons\.org/publicdomain/mark/1\.0', re.I), lambda m: 'public domain'),
    (re.compile(r'creativecommons\.org/licenses/sampling\+/1\.0', re.I), lambda m: 'CC-Sampling-Plus-1.0'),
    (re.compile(r'creativecommons\.org/licenses/(by(?:-nc)?(?:-sa|-nd)?|by-nd-nc)/' + _V, re.I), _cc_url),
    (re.compile(r'unlicense\.org|\bthe unlicense\b|\bunlicense\b', re.I), lambda m: 'Unlicense'),
    (re.compile(r'\bCC[-_ ](BY(?:[-_ ]NC)?(?:[-_ ](?:SA|ND))?)[-_ ]?' + _V, re.I), _cc_id),
    (re.compile(r'attribution(?P<nc>[\s-]*non[\s-]?commercial)?(?P<sa>[\s-]*share[\s-]?alike)?'
                r'(?P<nd>[\s-]*no[\s-]?deriv\w*)?[\s-]*(?P<v>1\.0|2\.0|2\.5|3\.0|4\.0)', re.I), _cc_long),
    (re.compile(r'\bCC[-_ ]?0(?!\.?\d)|creative commons zero|no rights reserved', re.I), lambda m: 'CC0-1.0'),
]
_PUBLIC_DOMAIN = re.compile(r'\bpublic[- ]domain\b', re.I)
_PD_GRANT = re.compile(r'(?:releas\w*|dedicat\w*|plac\w*|put|is|are|in|into|to)\s+(?:(?:in|into|to)\s+)?(?:the\s+)?'
                       r'public[- ]domain\b|\bpublic[- ]domain\s+(?:dedication|mark|work|licen[cs]e)\b|'
                       r'\blicen[cs]e[ds]?\s*:?\s*public[- ]domain\b', re.I)
_NEGATION = re.compile(r"\b(?:not|never|neither|nor|isn.?t|aren.?t|no longer|without)\b[^.,;:!?]{0,30}$", re.I)
_CC_BOILERPLATE = re.compile(r'the\s+text\s+of\s+the\s+creative\s+commons\s+public\s+licenses\s+is\s+dedicated\s+to\s+'
                             r'the\s+public\s+domain\s+under\s+the\s+CC0\s+public\s+domain\s+dedication\.?', re.I)
_REFUSING = r"(?:do not|don.t|may not|must not|cannot|can.t|not allowed to|prohibited|forbidden)\b[^.;]{0,40}\b"
_DOUBTS = [
    ('all rights reserved', re.compile(r'all rights reserved', re.I)),
    ('non-commercial', re.compile(r'\bnon[- ]?commercial\b|\bnot? (?:for |to be used for |for any )?commercial\b|'
                                  r'personal[- ]use[- ]only|(?:only|solely) for personal', re.I)),
    ('no derivatives', re.compile(r'\bno[- ]?deriv\w*|' + _REFUSING + r'(?:modify|alter|adapt|edit|remix|change)\b',
                                  re.I)),
    ('no redistribution', re.compile(_REFUSING + r'(?:re-?distribut\w*|re-?sell\w*|sell|resale|re-?upload\w*|'
                                     r're-?packag\w*|sub-?licen[cs]\w*)\b|\bnot for (?:re-?)?sale\b|'
                                     r'\bno (?:re-?distribution|resale)\b', re.I)),
    ('AI or machine-learning terms', re.compile(r'machine[- ]learning|artificial intelligence|generative|neural net',
                                                re.I)),
    ('AI or machine-learning terms', re.compile(r'\bAI\b')),         # in capitals only: 'ai' is a word elsewhere
    ('another licence', re.compile(r'\b(?:[AL]?GPL|GNU (?:lesser |affero )?general public|MIT licen[cs]e|'
                                   r'apache licen[cs]e|BSD licen[cs]e|mozilla public|EULA|end[- ]user licen[cs]e|'
                                   r'licen[cs]e agreement|proprietary)\b', re.I)),
    ('a Creative Commons licence without a version',
     re.compile(r'\bCC[-_ ]BY(?:[-_ ](?:NC|SA|ND))*\b|creative commons attribution', re.I)),
]
_COVERED = {'non-commercial': 'non-commercial', 'no derivatives': 'no-derivatives'}
_LEGAL_CODE_SAYS = ('no redistribution', 'no derivatives')
_LEGAL_SECTIONS = re.compile(r'\b(?:definitions|term and termination|disclaimer|limitation|license grant|'
                             r'statement of purpose|public license fallback)\b', re.I)


def conditions(licence):
    """What a licence asks of a sound made with it (a list; empty for none); 'other terms' for an unknown licence."""
    return list(CONDITIONS.get(licence, ['other terms']))


def free(licence):
    return licence in FREE


def usable(licence):
    """(True, '') or (False, why): a recording may be layered unless its licence forbids sharing adapted material."""
    if licence not in CONDITIONS:
        return False, f"its licence '{licence}' is not one this skill knows; import it with --licence other"
    if 'no-derivatives' in CONDITIONS[licence]:
        return False, (f'{licence} forbids sharing adapted material, and a sound made from a recording is adapted '
                       'material')
    return True, ''


def normalize(text):
    """A licence the person named, as this module's name, or None when it is not recognised."""
    t = re.sub(r'[\s_]+', ' ', str(text or '').strip()).lower()
    simple = {'cc0': 'CC0-1.0', 'cc0-1.0': 'CC0-1.0', 'cc0 1.0': 'CC0-1.0', 'creative commons zero': 'CC0-1.0',
              'public domain': 'public domain', 'public-domain': 'public domain', 'pd': 'public domain',
              'own': 'own', 'mine': 'own', 'my own': 'own', 'unlicense': 'Unlicense', 'other': 'other',
              'sampling+': 'CC-Sampling-Plus-1.0', 'cc-sampling-plus-1.0': 'CC-Sampling-Plus-1.0'}
    if t in simple:
        return simple[t]
    m = re.fullmatch(r'cc[- ]?(by(?:[- ]nc)?(?:[- ](?:sa|nd))?)[- ]?([1-4])(\.[05])?', t)
    if m:
        version = f'{m.group(2)}{m.group(3) or ".0"}'
        if version in _VERSIONS:
            return f"CC-{m.group(1).upper().replace(' ', '-')}-{version}"
    return None


def _lines(text):
    return [ln.strip() for ln in _CC_BOILERPLATE.sub(' ', str(text)).splitlines() if ln.strip()]


def _read_line(line):
    """([(licence, granted)], the line with those names blanked): every licence named in one line."""
    hits, spans = [], []
    for rx, name in _NAMED:
        for m in rx.finditer(line):
            if any(a < m.end() and m.start() < b for a, b in spans):
                continue                                     # part of a longer name already read
            spans.append(m.span())
            hits.append((name(m), not _NEGATION.search(line[:m.start()])))
    for m in _PUBLIC_DOMAIN.finditer(line):
        if any(a < m.end() and m.start() < b for a, b in spans):
            continue
        around = line[max(0, m.start() - 30):m.end() + 12]
        granted = bool(_PD_GRANT.search(around)) and not _NEGATION.search(line[:m.start()])
        spans.append(m.span())
        hits.append(('public domain', granted))
    rest = list(line)
    for a, b in spans:
        rest[a:b] = ' ' * (b - a)
    return hits, ''.join(rest)


def detect(text):
    """[(licence, the line that names it)]: every licence the text grants, each once. A free licence only mentioned
    ('not CC0', 'from public domain recordings') is not among them (see `doubts`); CC0 is itself a public-domain
    dedication, so its legal text's words on the public domain are not a second licence."""
    found, seen = [], set()
    for line in _lines(text):
        for licence, granted in _read_line(line)[0]:
            if granted and licence not in seen:
                seen.add(licence)
                found.append((licence, line[:200]))
    if 'CC0-1.0' in seen:
        found = [f for f in found if f[0] != 'public domain']
    return found


def _is_legal_code(text):
    """True for the full legal text of a Creative Commons licence (whose own words on limits are its terms, already
    named by the licence itself)."""
    t = str(text)
    return len(t) >= 4000 and bool(re.search(r'creative\s+commons', t, re.I)) \
        and len({m.group(0).lower() for m in _LEGAL_SECTIONS.finditer(t)}) >= 3


def doubts(text):
    """[(kind, the line)]: what in the text should stop an automatic reading of its licence and go to a person. The
    full legal text of a Creative Commons licence says on its own that a licensee may not sublicense or distort the
    work, so those two kinds do not count there; anything else added to such a file (an AI clause, 'all rights
    reserved') still does."""
    legal = _is_legal_code(text)
    asks = {c for licence, _ in detect(text) for c in conditions(licence)}
    out, seen = [], set()
    for line in _lines(text):
        hits, rest = _read_line(line)
        kinds = ['an unclear mention of a licence' for _, granted in hits if not granted]
        kinds += [kind for kind, rx in _DOUBTS if rx.search(rest) and _COVERED.get(kind) not in asks
                  and not (legal and kind in _LEGAL_CODE_SAYS)]
        for kind in kinds:
            if kind not in seen:
                seen.add(kind)
                out.append((kind, line[:200]))
    return out


def clash(names):
    """Why recordings under these licences cannot be mixed into one sound, or ''. ShareAlike asks that the sound carry that
    licence or a compatible one, which a second ShareAlike licence or a non-commercial term would contradict. (Some older
    ShareAlike versions do allow a later one; this errs toward asking for separate sounds.)"""
    names = sorted(n for n in set(names) if n)
    alike = [n for n in names if 'share-alike' in CONDITIONS.get(n, [])]
    if len(alike) > 1:
        return f"{' and '.join(alike)} each ask that the sound carry their own licence, so one sound cannot hold both"
    if alike and 'non-commercial' not in CONDITIONS[alike[0]]:
        closed = [n for n in names if 'non-commercial' in CONDITIONS.get(n, [])]
        if closed:
            return f"{alike[0]} asks that the sound carry it, and {closed[0]}'s non-commercial term does not allow that"
    return ''


def licence_url(licence):
    """The address of a Creative Commons licence's text, or ''."""
    m = re.fullmatch(r'CC-(BY(?:-NC)?(?:-SA|-ND)?)-([0-9]\.[0-9])', str(licence))
    if m:
        return f'https://creativecommons.org/licenses/{m.group(1).lower()}/{m.group(2)}/'
    return 'https://creativecommons.org/licenses/sampling+/1.0/' if licence == 'CC-Sampling-Plus-1.0' else ''


def credit(rec):
    """A credit line for one recording (provenance as render.py records it): its title, creator and source, its licence
    with the licence's address, and that it was changed, as CC BY asks."""
    who = rec.get('author') or 'unknown author'
    title = rec.get('recording') or rec.get('file')
    page = f" ({rec['page']})" if rec.get('page') else ''
    url = licence_url(rec.get('licence'))
    return f'"{title}" by {who}{page}, {rec.get("licence")}{", " + url if url else ""}, changed for this sound'


def _what_each_file_asks(recordings, sounds):
    """'file: what it asks' for every file of a sound whose files do not all ask the same, its variants playing different
    takes."""
    asks = {f"{r.get('library')}/{r.get('file')}": r.get('conditions') or [] for r in recordings}
    out = []
    for sound in sounds:
        rows = [(v['file'].rpartition('/')[2].rpartition('.')[0],
                 sorted({c for name in v.get('recordings', []) for c in asks.get(name, [])}))
                for v in sound.get('variants', [])]
        if len({tuple(asked) for _, asked in rows}) > 1:
            out += [f"{name}: {', '.join(asked) or 'nothing'}" for name, asked in rows]
    return out


def note(recordings, sounds=()):
    """What the sounds made with these recordings carry, for the audition page and export manifests; with the render's
    `sounds`, which of them use a recording that asks for something."""
    if not recordings:
        return ''
    bound = [r for r in recordings if r.get('conditions')]
    if not bound:
        return ('These sounds layer the recordings listed, each CC0, public domain or the user\'s own: no credit is '
                'required. They are listed for the record.')
    asks = sorted({c for r in bound for c in r['conditions']})
    parts = [f'{c}: {MEANING[c]}' for c in asks if c in MEANING]
    credits = '; '.join(credit(r) for r in bound)
    named = {f"{r.get('library')}/{r.get('file')}" for r in bound}
    using = [s['id'] for s in sounds if named & set(s.get('recordings', []))]
    only = f" Only these sounds use them: {', '.join(using)}." if using and len(using) < len(sounds) else ''
    each = _what_each_file_asks(bound, sounds)
    apart = f" The files of a sound can differ in this: {'; '.join(each)}." if each else ''
    return ('These sounds layer recordings whose licences ask for something. ' + ' '.join(p + '.' for p in parts) + only +
            apart + f' Credits: {credits}. Keep any copyright or warranty notice that came with them.')
