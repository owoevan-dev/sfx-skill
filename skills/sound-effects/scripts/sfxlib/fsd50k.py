"""FSD50K's CC0 recordings as a sample library: download, import, screen.

FSD50K (Fonseca, Favory, Pons, Font and Serra, "FSD50K: An Open Dataset of Human-Labeled Sound Events", IEEE/ACM
Transactions on Audio, Speech, and Language Processing 30, 829-852, 2022) gathers 51,197 Freesound recordings labelled with
200 classes of Google's AudioSet ontology: uncompressed 16-bit PCM at 44.1 kHz, mono, 0.3 to 30 s each. The dataset as
a compilation (its selection, labels and metadata) is under CC BY 4.0; each recording keeps the licence its author gave
it on Freesound. Only the recordings their authors dedicated to the public domain (CC0 1.0) are imported, so a sound
made with them needs no credit. The evaluation set holds 4,914 of them, the development set 14,959. Those this project
has withdrawn (WITHDRAWN) are neither imported nor offered.

The files come from the dataset's Zenodo record (record 4060432): `download` fetches them through Zenodo's files API,
which its robots.txt allows (it asks for 10 s between requests), one request at a time, and checks each file against
the MD5 sum known here; `add` also accepts files a person downloaded. The audio is one Info-ZIP split archive per set
(NAME.z01 ... NAME.zip, ZIP64), read here member by member without joining the parts: each member's local header lies
in the part its central-directory entry names, at an offset within that part; its data is inflated and checked
against its CRC-32. Only CC0 members are written, under names made of the clip's number; a name that could leave the
folder is refused, and nothing is ever executed.

Each recording is measured as it is imported (`measure`) and screened (`screen`) by this skill's own thresholds, so
the agent can prefer clean recordings: runs of samples at full scale (clipping), little energy above 16 kHz against
8-16 kHz (a lossy original), a low peak, a short range between the body and the noise floor (a noisy recording) and
FSD50K's own ratings of whether a label's sound is predominant. A recording whose description asks for credit,
permission or notice, or speaks of commercial use, is flagged too ('author note', with the description kept): CC0
puts no condition on it, but the author's wish is read and respected before it is used. A recording that fails is
still listed, with its reasons. Importing a set again refreshes these fields from the metadata without touching the
audio.

A release of this skill can publish packs: the CC0 recordings of both sets that pass every screen, unchanged, in parts
of under 2 GiB, with an index naming each part's SHA-256 and each recording's entry (tools/make_fsd50k_packs.py).
`download_packs` fetches them, resuming as above, against the index's sums (and the index against a sum known to this
version), and `add_packs` imports them. The full set from Zenodo can still follow them."""
import csv
import hashlib
import http.client
import io
import json
import re
import struct
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from pathlib import Path

import numpy as np

from . import wav
from .record import is_link

RECORD = 4060432
PAGE = f'https://zenodo.org/records/{RECORD}'
FILES_API = f'https://zenodo.org/api/records/{RECORD}/files'
CRAWL_DELAY_S = 10                 # Zenodo's robots.txt (read 2026-11-20)
META_ZIP = 'FSD50K.metadata.zip'
TRUTH_ZIP = 'FSD50K.ground_truth.zip'
SETS = {
    'eval': {'audio': 'FSD50K.eval_audio', 'parts': 1, 'info': 'eval_clips_info_FSD50K.json', 'truth': 'eval.csv'},
    'dev': {'audio': 'FSD50K.dev_audio', 'parts': 5, 'info': 'dev_clips_info_FSD50K.json', 'truth': 'dev.csv'},
}
KNOWN_MD5 = {                      # the record's own sums (Zenodo files API, read 2026-11-20)
    'FSD50K.metadata.zip': 'b9ea0c829a411c1d42adb9da539ed237',
    'FSD50K.ground_truth.zip': 'ca27382c195e37d2269c4c866dd73485',
    'FSD50K.eval_audio.z01': '3090670eaeecc013ca1ff84fe4442aeb',
    'FSD50K.eval_audio.zip': '6fa47636c3a3ad5c7dfeba99f2637982',
    'FSD50K.dev_audio.z01': 'faa7cf4cc076fc34a44a479a5ed862a3',
    'FSD50K.dev_audio.z02': '8f9b66153e68571164fb1315d00bc7bc',
    'FSD50K.dev_audio.z03': '1196ef47d267a993d30fa98af54b7159',
    'FSD50K.dev_audio.z04': 'd088ac4e11ba53daf9f7574c11cccac9',
    'FSD50K.dev_audio.z05': '81356521aa159accd3c35de22da28c7f',
    'FSD50K.dev_audio.zip': 'c480d119b8f7a7e32fdb58f3ea4d6c5a',
}
CC0_URL = 'creativecommons.org/publicdomain/zero/1.0'
CC0_COUNTS = {'eval': 4914, 'dev': 14959}   # CC0 clips per set, counted from the sets' metadata (2026-11-20)
PACK_SCHEMA = 'sound-effects-fsd50k-packs/1'
PACK_INDEX = 'fsd50k-cc0-index.json'
PACK_STATUS = 'release pack'
PACK_PART_RE = re.compile(r'fsd50k-cc0-[0-9]{2,3}\.zip')
PACK_MEMBER_RE = re.compile(r'(fsd50k-(?:eval|dev))/([0-9]{1,12})\.wav')
RELEASE_URL = 'https://github.com/owoevan-dev/sfx-skill/releases/download/v0.1.3'     # where the release keeps its packs
RELEASE_INDEX_SHA256 = '3911bb1cd0c39305afc721ab028dea018d0cd18fa3edd7da33ebbe6f010c3b6e'     # its index, whose sums pin every part
RELEASE_RECORDINGS = 6718
# Recordings this project no longer offers: their source, terms of use or content left room for doubt when their
# descriptions were read. A library that imported one earlier keeps the file; the skill neither lists nor plays it.
WITHDRAWN = frozenset("""
7979 9102 19437 30149 31006 34374 36111 36131 37248 38246 46702 46796 47459 47464 50709 50715 52219 55071 58362 59507
61505 64258 64259 64260 64261 64272 64286 64298 66585 66586 69257 69552 76705 77892 79867 79869 82418 82685 82798
83593 83707 83710 83721 83727 83730 87967 88590 89496 89651 90143 91580 91584 91594 91599 91601 91605 91607 91608
91610 91614 91617 91621 91622 91623 91629 91635 91640 91645 91646 91652 91655 91661 91667 91672 91690 95337 96076
97769 100476 101894 101900 101901 101902 101903 101904 101905 101906 101908 101909 101910 101913 101914 102004 102007
102008 102018 102020 102024 102026 102696 104616 110139 110140 110570 112068 113746 113748 113749 114046 114047 114048
114049 114050 117511 118487 120468 121941 123306 123307 123308 127265 127824 127845 127846 127909 128307 130105 130833 131009
131709 133456 133833 135584 135691 135698 140697 143919 144823 146933 147597 148016 148933 149281 149282 149289 149291
149453 149895 151081 151085 151810 151820 152605 155145 156311 156321 156325 156327 156331 156333 156334 157436 157441
157446 157448 157449 157450 159197 161347 161760 162880 164089 164090 165142 165391 165599 166192 166225 166227 166229
166232 166233 166238 166241 166242 166244 166246 166732 169289 171658 171765 172951 173137 173933 175409 176929 177253
178176 178182 178419 179671 179878 179884 180129 180131 180135 180180 180699 180953 182271 182272 182273 185838 186773
186925 186933 186936 186939 186940 186945 186949 186952 186955 186956 186958 186960 186962 186965 187309 187314 188012
188209 191600 191766 192761 193046 193050 198810 199820 199824 199825 199826 199869 199906 201814 202512 202730 202898
203522 203528 204186 205982 207327 209627 209865 209866 209870 209873 209874 209880 209884 209888 209904 209909 209917
209918 209940 209941 210390 211447 211451 211453 211512 214336 216752 216753 216757 216758 216763 217388 217389 217390
217391 218308 219250 219252 219254 219257 219262 219265 219657 219658 220104 220105 220106 232425 234858 234867 234950
235410 238570 238571 238572 238573 238574 238576 238577 238579 238580 238581 238582 238583 238695 238696 238697 238698
238699 238701 238703 238704 238705 238724 238726 238727 238884 239044 239067 239068 239069 239070 239072 239073 239074
239075 239464 239465 239466 239467 239468 239469 239471 239472 239473 239562 239564 239906 240081 240085 241591 241592
242590 242591 242856 243661 250741 252210 253713 253772 255009 255179 255223 255902 257368 257483 260388 260395 260397
260404 260405 260410 260471 260473 260476 260496 260505 260514 260774 262648 262696 262812 263786 264346 264599 264744
265180 265181 267889 268100 268254 268255 268256 268257 268258 268259 268260 268261 268263 268265 268266 268268 268269
268270 269488 270271 270383 271273 271404 271407 271409 271410 271412 271416 271417 271421 271422 272008 272009 272010 274451
274510 274511 278108 315833 315919 315922 315923 315933 315934 315936 315939 315940 316631 316634 316642 316643 317075
317098 317099 317100 317101 317102 319738 320512 320733 320734 321310 323607 324464 324546 324584 325349 325350 325351
325352 325462 328009 328814 328878 330035 330136 330601 332362 333632 333692 334070 335930 335931 335932 335935 335936
335937 335939 335940 335941 335942 335943 335944 335946 335947 335948 335951 335954 335955 335956 335983 337772 338777
340962 342551 342552 343932 343933 343939 345288 345304 345305 345999 346663 346903 347117 347118 347119 347120 347123
348577 349903 350419 350423 350426 350430 350431 350604 351062 351882 353207 353252 353440 353442 353700 353853 353928
360460 360465 360717 361212 361227 361544 361823 362257 363150 364707 365632 366086 366088 366090 367125 367493 367533
368176 369292 369394 369427 369673 369676 370664 371302 371303 371342 371344 371348 371360 371562 371897 371919 376316
377774 377776 377777 378831 378843 387931 389590 390459 390460 390583 391100 393769 394288 395197 395199 395393 395829
395830 395831 396283 396285 396326 396327 397657 399577 399581 399583 399584 401257 403063 404142 404368 405459 405460
405468 405469 405481 405487 405492 405500 408162 409129 410427 410814 411299 412841 413355 413412 413480 413485 413657
413861 413881 413892 413896 413898 414050 414827 415193 416300 416702 416908 417686 417688 417689 418997 419201 419219
419220 419221 419222 419491 420670 421168 421589 421723 422147 422539 423006 424501 424502 425971 427417 427458 427465
427718 428504 429967 430815 431453 431588 432286 432346 432366 432641 433611 434085 435242 435243 435244 435245 435246
435247 435248 435249 435250 435251 435252 435253 435255 435256 435257 435258 435259 435260 435262 435263 435264 435267
435269 435270 435272 435274 435276 435278 435279 435280 435282 435285 435286 435287 435288
""".split())
CHUNK = 1 << 20
ATTEMPTS = 6                       # tries in a row without a byte of progress before a download stops
RELEASE_GONE = (404, 410)          # what a release answers for a file it no longer holds (RFC 9110)
EMAIL_NAME = re.compile(r'[^@\s]+@[^@\s]+\.[^@\s]+')     # a Freesound name can be someone's email address
NO_ADDRESS = 'a Freesound user (see the page)'

# Screening thresholds: this skill's own choices from measuring the 4,914 CC0 evaluation clips (research notes).
CLIP_LEVEL = 0.999                 # a sample at or above this fraction of full scale ...
CLIP_RUN = 3                       # ... for this many samples in a row counts as clipping
FULL_BAND_RATE = 44100             # below this rate a file has no top band to read: the screen says nothing about it
LOSSY_TOP_DB = -30.0               # energy above 16 kHz this far under 8-16 kHz: the recording was once lossy
QUIET_PEAK_DB = -20.0
NOISY_RANGE_DB = 20.0              # 95th minus 10th percentile of 50 ms RMS frames, a signal-to-noise proxy
# FSD50K classes whose sound goes on without a gap (this skill's reading of the class names): the range between a
# recording's body and its quietest frames says nothing about noise when the whole recording is the sound
STEADY = frozenset((
    'Rain', 'Thunderstorm', 'Wind', 'Stream', 'Ocean', 'Waves_and_surf', 'Water_tap_and_faucet',
    'Sink_(filling_or_washing)', 'Bathtub_(filling_or_washing)', 'Pour', 'Trickle_and_dribble', 'Fill_(with_liquid)',
    'Boiling', 'Frying_(food)', 'Gurgling', 'Fire', 'Engine', 'Idling', 'Accelerating_and_revving_and_vroom',
    'Traffic_noise_and_roadway_noise', 'Train', 'Rail_transport', 'Subway_and_metro_and_underground', 'Bus', 'Aircraft',
    'Fixed-wing_aircraft_and_airplane', 'Boat_and_Water_vehicle', 'Motorcycle', 'Truck', 'Mechanical_fan',
    'Microwave_oven', 'Printer', 'Power_tool', 'Drill', 'Purr', 'Buzz', 'Hiss', 'Crowd', 'Chatter', 'Applause', 'Cheering',
    'Cricket', 'Insect'))
ADULT_WORDS = re.compile(r'\b(?:sex|sexy|sexual|orgasm\w*|porn\w*|erotic\w*|masturbat\w*|intercourse|blowjob|fellatio|cunnilingus|'
                         r'cumming|horny|penis|vagina|dildo|fuck\w*)\b', re.IGNORECASE)
SPEECH_LABELS = frozenset(('Speech', 'Male_speech_and_man_speaking', 'Female_speech_and_woman_speaking',
                           'Child_speech_and_kid_speaking', 'Conversation', 'Speech_synthesizer', 'Singing', 'Male_singing',
                           'Female_singing'))
VOICE_LABELS = frozenset(('Human_voice', 'Shout', 'Yell', 'Chatter', 'Whispering'))         # a voice that may hold words,
WORDLESS_LABELS = frozenset(('Laughter', 'Giggle', 'Screaming', 'Crying_and_sobbing', 'Breathing', 'Cough', 'Sneeze',
                             'Burping_and_eructation'))                                     # unless it is one of these
SILENCE_DB = -120.0                # a 50 ms frame under this is digital silence: padding, not a noise floor
PREDOMINANT = 0.75                 # FSD50K's ratings: 1 present and predominant, 0.5 present but not predominant
NOTE_RE = re.compile(r'credit|attribut|commercial|let me know|notify me|tell me|my name|link (back|to me)|'
                     r'ask (me )?(first|for permission)|permission|don.t (say|claim)|not claim|send me|message me|'
                     r'contact me|e-?mail me', re.IGNORECASE)
NOTE_CHARS = 400
NOTE_WAIVES = re.compile(r"\bno attribution\b|\bwithout (?:any )?(?:attribution|credit)\b|"
                         r"\b(?:attribution|credit)s? (?:is |are )?(?:not|n.t) (?:required|needed|necessary)\b|"
                         r"\bno (?:need (?:to|for) )?credits?\b|\b(?:do not|don.t) need to (?:credit|attribute)\b",
                         re.IGNORECASE)
NOTE_GIVEN = re.compile(r"\b(?:give|grant)s? (?:you )?permission\b", re.IGNORECASE)
NOTE_HOLDS = re.compile(r"\b(?:not|never|must|please|ask|but|unless|except|only|let me|tell me|notify|link|send|message|"
                        r"contact|e-?mail|appreciat\w*|love to|would (?:like|love))\b|n.t\b|.d (?:like|love)\b",
                        re.IGNORECASE)


class FSD50KError(Exception):
    """The dataset's files are missing, changed or unreadable."""


def files_for(which):
    """The record's files a set needs: the metadata, the ground truth and its audio parts."""
    s = SETS[which]
    parts = [f"{s['audio']}.z{k:02d}" for k in range(1, s['parts'] + 1)] + [f"{s['audio']}.zip"]
    return [META_ZIP, TRUTH_ZIP] + parts


def md5_of(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(CHUNK), b''):
            h.update(block)
    return h.hexdigest()


def credited_author(name):
    """The name a recording of the dataset is credited by: one that is an email address is not repeated, and the
    recording's page credits them."""
    return NO_ADDRESS if EMAIL_NAME.search(str(name)) else name


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(CHUNK), b''):
            h.update(block)
    return h.hexdigest()


def withdrawn(library, file):
    """True for a recording of the dataset's two packs that this project no longer offers (WITHDRAWN)."""
    return str(library) in ('fsd50k-eval', 'fsd50k-dev') and str(file) in WITHDRAWN


# ---------------------------------------------------------------- download

def download(folder, which='eval', opener=None, sleep=time.sleep, log=print):
    """Fetch the files a set needs into `folder` from Zenodo's files API, skipping those already there with the known
    MD5; a partly downloaded file (NAME.part) is resumed when the server honours the range, also after a dropped
    connection or a restart of the machine; a damaged one is set aside as NAME.bad-N. Returns the file names.
    `opener(url, headers)` returns a response with .status, .headers and .read(n) (tests pass their own)."""
    opener = opener or _urlopen
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    wanted = files_for(which)
    todo = [n for n in wanted if not ((folder / n).is_file() and md5_of(folder / n) == KNOWN_MD5[n])]
    if not todo:
        return wanted
    listing = json.loads(_read_all(opener(FILES_API, {})).decode('utf-8'))
    entries = {e['key']: e for e in listing.get('entries', [])}
    for name in todo:
        e = entries.get(name)
        if e is None:
            raise FSD50KError(f'{name} is not in the Zenodo record any more; the dataset has changed')
        if e.get('checksum') != f'md5:{KNOWN_MD5[name]}':
            raise FSD50KError(f'{name} on Zenodo has another checksum than the one known here; the dataset has changed')
        sleep(CRAWL_DELAY_S)
        _fetch(opener, e['links']['content'], folder, name, int(e.get('size', 0)), log, sleep)
    return wanted


def _urlopen(url, headers):
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)


def _read_all(resp):
    out = bytearray()
    for block in iter(lambda: resp.read(CHUNK), b''):
        out += block
    return bytes(out)


def _set_aside(part, name, log):
    """Move a damaged partial file out of the way (never deleted: the person can clear it)."""
    k = 1
    while (part.parent / f'{name}.bad-{k}').exists():
        k += 1
    part.replace(part.parent / f'{name}.bad-{k}')
    log(f'{name}: the partial file was damaged; kept as {name}.bad-{k}, fetching it again')


def _range_start(resp):
    """The first byte a partial response says it holds, or None when it does not say (RFC 9110, Content-Range)."""
    m = re.match(r'bytes (\d+)-', str(resp.headers.get('Content-Range', '')))
    return int(m.group(1)) if m else None


def _fetch(opener, url, folder, name, size, log, sleep=time.sleep, good=None):
    """Fetch one file into NAME.part and move it to NAME once its sum is right (`good(path)`; by default the MD5 known
    here). A connection that drops is resumed
    from the bytes already there, waiting longer each time nothing arrived; after ATTEMPTS tries in a row without a
    byte of progress it stops, keeping the part for the next run."""
    part = folder / f'{name}.part'
    if is_link(part):
        raise FSD50KError(f'{part} is a link, and a download does not write through one: move it away and run this again')
    idle = 0
    last = ''
    while idle < ATTEMPTS:
        have = part.stat().st_size if part.is_file() else 0
        if size and have > size:
            _set_aside(part, name, log)
            have = 0
        if not size or have < size:
            try:
                resp = opener(url, {'Range': f'bytes={have}-'} if have else {})
                resume = bool(have) and resp.status == 206 and _range_start(resp) in (None, have)
                log(f'{name}: {"resuming at " + str(have) if resume else "downloading"} ({size} bytes)')
                with open(part, 'ab' if resume else 'wb') as f:
                    for block in iter(lambda: resp.read(CHUNK), b''):
                        f.write(block)
            except (OSError, http.client.HTTPException) as error:
                last = str(error)
            now = part.stat().st_size if part.is_file() else 0
            if size and now < size or last:
                idle = 0 if now > have else idle + 1
                last = ''
                if idle < ATTEMPTS:
                    log(f'{name}: the connection dropped at byte {now}; trying again')
                    sleep(CRAWL_DELAY_S * 2 ** min(idle, 4))
                continue
        if (good(part) if good else md5_of(part) == KNOWN_MD5[name]):
            part.replace(folder / name)
            return
        _set_aside(part, name, log)
        idle += 1
        sleep(CRAWL_DELAY_S)
    raise FSD50KError(f'{name}: the download keeps failing; run the download again later (it resumes from what it has)')


# ---------------------------------------------------------------- split archives

class Parts:
    """The parts of an Info-ZIP split archive read as one stream: NAME.z01 ... NAME.zNN, then NAME.zip."""

    def __init__(self, last):
        last = Path(last)
        self.paths = sorted(last.parent.glob(last.stem + '.z[0-9][0-9]')) + [last]
        self.sizes = [p.stat().st_size for p in self.paths]
        self.starts = [sum(self.sizes[:i]) for i in range(len(self.paths))]
        self.files = [open(p, 'rb') for p in self.paths]

    def close(self):
        for f in self.files:
            f.close()

    def read(self, pos, n):
        out = bytearray()
        while n > 0:
            i = max(k for k in range(len(self.starts)) if self.starts[k] <= pos)
            f = self.files[i]
            f.seek(pos - self.starts[i])
            chunk = f.read(min(n, self.sizes[i] - (pos - self.starts[i])))
            if not chunk:
                raise FSD50KError(f'the archive ends early (at byte {pos}); a part is missing or cut short')
            out += chunk
            pos += len(chunk)
            n -= len(chunk)
        return bytes(out)


class Member:
    __slots__ = ('name', 'method', 'crc', 'csize', 'usize', 'disk', 'offset')


def members(parts):
    """The archive's members from its central directory (ZIP64 end records and extra fields understood)."""
    last = len(parts.paths) - 1
    tail_len = min(parts.sizes[last], 1 << 16)
    tail = parts.read(parts.starts[last] + parts.sizes[last] - tail_len, tail_len)
    e = tail.rfind(b'PK\x05\x06')
    if e < 0:
        raise FSD50KError('no end of central directory: not a zip archive, or its last part is missing')
    (_, _, cd_disk, _, n_total, cd_size, cd_offset, _) = struct.unpack('<IHHHHIIH', tail[e:e + 22])
    if 0xFFFF in (cd_disk, n_total) or 0xFFFFFFFF in (cd_size, cd_offset):
        loc = tail.rfind(b'PK\x06\x07', 0, e)
        if loc < 0:
            raise FSD50KError('a ZIP64 archive without its ZIP64 locator')
        _, z64_disk, z64_off, _ = struct.unpack('<IIQI', tail[loc:loc + 20])
        rec = parts.read(parts.starts[z64_disk] + z64_off, 56)
        (sig, _, _, _, _, cd_disk, _, n_total, cd_size, cd_offset) = struct.unpack('<IQHHIIQQQQ', rec)
        if sig != 0x06064b50:
            raise FSD50KError('bad ZIP64 end of central directory')
    if cd_disk >= len(parts.paths):
        raise FSD50KError('the central directory lies in a part that is missing')
    cd = parts.read(parts.starts[cd_disk] + cd_offset, cd_size)
    out, p = [], 0
    for _ in range(n_total):
        (sig, _, _, _, method, _, _, crc, csize, usize, nlen, xlen, clen, disk, _, _,
         offset) = struct.unpack('<IHHHHHHIIIHHHHHII', cd[p:p + 46])
        if sig != 0x02014b50:
            raise FSD50KError('bad central directory entry')
        name = cd[p + 46:p + 46 + nlen].decode('utf-8', 'replace')
        extra = cd[p + 46 + nlen:p + 46 + nlen + xlen]
        q = 0
        while q + 4 <= len(extra):
            hid, hlen = struct.unpack('<HH', extra[q:q + 4])
            data = extra[q + 4:q + 4 + hlen]
            if hid == 0x0001:
                k = 0
                if usize == 0xFFFFFFFF:
                    usize = struct.unpack('<Q', data[k:k + 8])[0]
                    k += 8
                if csize == 0xFFFFFFFF:
                    csize = struct.unpack('<Q', data[k:k + 8])[0]
                    k += 8
                if offset == 0xFFFFFFFF:
                    offset = struct.unpack('<Q', data[k:k + 8])[0]
                    k += 8
                if disk == 0xFFFF:
                    disk = struct.unpack('<I', data[k:k + 4])[0]
            q += 4 + hlen
        m = Member()
        m.name, m.method, m.crc, m.csize, m.usize, m.disk, m.offset = name, method, crc, csize, usize, disk, offset
        out.append(m)
        p += 46 + nlen + xlen + clen
    return out


def read_member(parts, m, max_bytes=64 * 2 ** 20):
    """A member's bytes, inflated and checked against its size and CRC-32."""
    if m.usize > max_bytes or m.csize > max_bytes:
        raise FSD50KError(f'{m.name} is too large ({m.usize} bytes)')
    if m.disk >= len(parts.paths):
        raise FSD50KError(f'{m.name} lies in a part that is missing')
    pos = parts.starts[m.disk] + m.offset
    head = parts.read(pos, 30)
    sig, _, _, _, _, _, _, _, _, nlen, xlen = struct.unpack('<IHHHHHIIIHH', head)
    if sig != 0x04034b50:
        raise FSD50KError(f'bad local header for {m.name}')
    raw = parts.read(pos + 30 + nlen + xlen, m.csize)
    if m.method == 0:
        data = raw
    elif m.method == 8:
        d = zlib.decompressobj(-15)
        data = d.decompress(raw, max_bytes + 1)
    else:
        raise FSD50KError(f'{m.name}: compression method {m.method} is not supported')
    if len(data) != m.usize or (zlib.crc32(data) & 0xFFFFFFFF) != m.crc:
        raise FSD50KError(f'{m.name}: size or CRC-32 does not match; the archive is damaged')
    return data


# ---------------------------------------------------------------- measuring

def measure(x, rate):
    """This skill's screening measures of a recording (mono or the mean of its channels)."""
    channels = np.asarray(x, dtype=np.float64).reshape(len(x), -1)
    x = channels.mean(axis=1)
    n = len(x)
    peak = float(np.max(np.abs(channels))) if n else 0.0     # a channel can clip while their mean does not
    runs = 0
    if n >= CLIP_RUN:
        for channel in channels.T:
            hot = np.abs(channel) >= CLIP_LEVEL
            full = np.convolve(hot.astype(int), np.ones(CLIP_RUN, dtype=int), 'valid') == CLIP_RUN
            starts = full & ~np.concatenate([[False], hot[:len(full) - 1]])
            runs += int(np.sum(starts))
    hop = max(1, int(0.05 * rate))
    frames = n // hop
    if frames < 4 and n >= 8:          # a short effect: enough frames of its own to tell a floor from a body
        hop = n // 8
        frames = n // hop
    if frames >= 4:
        rms = np.sqrt(np.mean(x[:frames * hop].reshape(frames, hop) ** 2, axis=1) + 1e-20)
        db = 20 * np.log10(rms)
        sounding = db[db > SILENCE_DB]
        db = sounding if len(sounding) >= 4 else db
        floor, body = float(np.percentile(db, 10)), float(np.percentile(db, 95))
    else:
        floor = body = float(20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-10)) if n else -200.0
    spec = np.abs(np.fft.rfft(x)) ** 2 if n else np.zeros(1)
    freqs = np.fft.rfftfreq(n, 1 / rate) if n else np.zeros(1)
    mid = float(np.sum(spec[(freqs >= 8000) & (freqs < 16000)])) or 1e-30
    top = float(np.sum(spec[freqs >= 16000])) or 1e-30
    return {'peak_db': round(20 * np.log10(peak + 1e-12), 1), 'clipped_runs': runs, 'floor_db': round(floor, 1),
            'range_db': round(body - floor, 1),
            'top_db': round(10 * np.log10(top / mid), 1) if rate >= FULL_BAND_RATE else None}


def _only_waives_credit(text):
    """True when all the description says on credit or permission is that no credit is needed ('No attribution
    required'), and no request, condition or wish appears anywhere else in it."""
    rest = NOTE_GIVEN.sub(' ', NOTE_WAIVES.sub(' ', text))
    return bool(NOTE_WAIVES.search(text)) and not NOTE_RE.search(rest) and not NOTE_HOLDS.search(rest)


def author_note(description):
    """The description (whitespace joined, at most NOTE_CHARS) when it asks for credit, permission or notice or
    speaks of commercial use, unless all it says is that no credit is needed; else None."""
    text = ' '.join(str(description or '').split())
    if not NOTE_RE.search(text) or _only_waives_credit(text):
        return None
    if len(text) <= NOTE_CHARS:
        return text
    asking = ' ... '.join(s for s in re.split(r'(?<=[.!?;])\s+', text) if NOTE_RE.search(s))  # what the reader must see
    return asking if len(asking) <= NOTE_CHARS else asking[:NOTE_CHARS - 3] + '...'


def is_steady(rated):
    """True when what a recording is mainly rated as is steady: each class rated predominant ({label: mean rating}) is
    in STEADY. A knock with wind behind it is an event."""
    top = [label for label, r in rated.items() if r >= PREDOMINANT]
    return bool(top) and all(label in STEADY for label in top)


def content_reasons(title, tags, labels):
    """What keeps a recording out of a library an agent draws from without hearing it, however clean it is: adult
    content, by the words of its title and tags, and speech or singing, by its labels, which also covers a voice that may
    hold words unless it is labelled as a sound without them, such as laughter, a scream or breathing (this is a library
    of sound effects)."""
    words = re.sub(r'[_-]+', ' ', ' '.join([str(title), *map(str, tags)]))
    reasons = ['adult content'] if ADULT_WORDS.search(words) else []
    labels = set(labels)
    if SPEECH_LABELS & labels or (VOICE_LABELS & labels and not WORDLESS_LABELS & labels):
        reasons.append('speech or singing')
    return reasons


def screen(measures, predominant, note=False, steady=False, content=()):
    """The reasons a recording is less clean or needs a look first, by this skill's thresholds (an empty list: it
    passes). `predominant` None (no ratings, as for a person's own sounds) is not a reason. A `steady` recording (rain,
    wind, an engine: see is_steady) is not called noisy for having no quiet part. `content`: content_reasons."""
    reasons = list(content)
    if measures['clipped_runs']:
        reasons.append('clipped')
    if measures['top_db'] is not None and measures['top_db'] < LOSSY_TOP_DB:
        reasons.append('lossy original')
    if measures['peak_db'] < QUIET_PEAK_DB:
        reasons.append('quiet')
    if measures['range_db'] < NOISY_RANGE_DB and not steady:
        reasons.append('noisy')
    if predominant is False:
        reasons.append('not predominant')
    if note:
        reasons.append('author note')
    return reasons


# ---------------------------------------------------------------- import

def _zip_member(path, suffix):
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if info.filename.replace('\\', '/').endswith('/' + suffix):
                return z.read(info)
    raise FSD50KError(f'{Path(path).name} holds no {suffix}')


def _check_md5(folder, names, check):
    for name in names:
        path = Path(folder) / name
        if not path.is_file():
            raise FSD50KError(f'{name} is missing from {folder}; sfx.py samples --download-fsd50k fetches it')
        if check and md5_of(path) != KNOWN_MD5[name]:
            raise FSD50KError(f'{name} does not match its known MD5 sum; download it again')


def metadata(folder, which):
    """{clip number: {'title', 'tags', 'uploader', 'license', 'labels', 'predominant'}} for a set's clips."""
    s = SETS[which]
    info = json.loads(_zip_member(Path(folder) / META_ZIP, s['info']).decode('utf-8'))
    ratings = json.loads(_zip_member(Path(folder) / META_ZIP, 'pp_pnp_ratings_FSD50K.json').decode('utf-8'))
    truth = _zip_member(Path(folder) / TRUTH_ZIP, s['truth']).decode('utf-8')
    rows = list(csv.DictReader(io.StringIO(truth)))
    labels = {r['fname']: r['labels'].split(',') for r in rows}
    names = {r['fname']: dict(zip(r['mids'].split(','), r['labels'].split(','))) for r in rows}
    out = {}
    for fname, v in info.items():
        r = ratings.get(fname, {})
        rated = {names.get(str(fname), {}).get(mid, mid): sum(x) / len(x) for mid, x in r.items() if x}
        out[str(fname)] = {'title': v.get('title', ''), 'tags': list(v.get('tags', [])), 'uploader': v.get('uploader', ''),
                           'license': v.get('license', ''), 'labels': labels.get(str(fname), []),
                           'description': v.get('description', ''),
                           'predominant': any(sum(x) / len(x) >= PREDOMINANT for x in r.values() if x),
                           'steady': is_steady(rated)}
    return out


def _describe(row, v):
    """Set a row's metadata fields (title, author, page, tags, labels, ratings, author note) and its screens."""
    row.update({'title': v['title'], 'author': v['uploader'], 'page': f"https://freesound.org/s/{row['file']}/",
                'tags': v['tags'], 'labels': v['labels'], 'predominant': v['predominant']})
    note = author_note(v.get('description'))
    if note:
        row['author_note'] = note
    else:
        row.pop('author_note', None)
    row['screen'] = screen(row['measures'], v['predominant'], bool(note), v['steady'],
                           content_reasons(v['title'], v['tags'], v['labels']))


def add(folder, library, which='eval', check_md5=True, log=print):
    """Import a set's CC0 recordings from the dataset's files in `folder` into the library folder (the pack
    'fsd50k-<set>'); returns {'pack', 'status', 'files', 'passing', 'folder'}. Recordings already written by an
    interrupted import are kept when they match the archive's CRC-32; nothing is replaced or removed. A set already in
    the library has only its metadata fields and screens refreshed (from the metadata and ground-truth files)."""
    from . import samples                     # samples imports this module's names lazily too
    if which not in SETS:
        raise FSD50KError(f"no set '{which}' (sets: {', '.join(SETS)})")
    with samples.locked(library):
        return _add_locked(folder, Path(library), which, check_md5, log)


def _add_locked(folder, library, which, check_md5, log):
    from . import samples
    names = files_for(which)
    pack_id = f'fsd50k-{which}'
    index = samples.load_index(library) if (library / samples.INDEX).is_file() else samples.new_index()
    if pack_id in index['packs'] and index['packs'][pack_id].get('status') != PACK_STATUS:
        _check_md5(folder, [META_ZIP, TRUTH_ZIP], check_md5)
        meta = metadata(folder, which)
        rows = [r for r in index['files'] if r['library'] == pack_id]
        for row in rows:
            if row['file'] in meta:
                _describe(row, meta[row['file']])
        there = index['packs'][pack_id]
        there['passing'] = sum(1 for r in rows if not r['screen'])
        samples._write_index(library, index)
        return {'pack': pack_id, 'status': 'already imported, metadata refreshed', 'files': there['files'],
                'passing': there['passing'], 'folder': str(library / pack_id)}
    _check_md5(folder, names, check_md5)
    meta = metadata(folder, which)
    cc0 = {f for f, v in meta.items() if CC0_URL in v['license'] and f not in WITHDRAWN}
    out = samples.inside(library, library / pack_id)
    out.mkdir(parents=True, exist_ok=True)
    parts = Parts(Path(folder) / f"{SETS[which]['audio']}.zip")
    rows, started = [], time.time()
    try:
        for m in members(parts):
            stem, ext = Path(m.name.replace('\\', '/')).stem, Path(m.name).suffix.lower()
            if ext != '.wav' or stem not in cc0:
                continue
            if samples._parts(m.name) is None or not re.fullmatch(r'[0-9]{1,12}', stem):
                raise FSD50KError(f'unsafe or unexpected member name {m.name!r}')
            path = out / f'{stem}.wav'
            if path.is_file():
                blob = path.read_bytes()
                if len(blob) != m.usize or (zlib.crc32(blob) & 0xFFFFFFFF) != m.crc:
                    raise FSD50KError(f'{path} differs from the archive; import into a new library folder')
            else:
                blob = read_member(parts, m)
                samples._write_new(path, blob)
            x, info = wav.read(path)
            row = {'library': pack_id, 'file': stem, 'path': f'{pack_id}/{stem}.wav',
                   'sha256': hashlib.sha256(blob).hexdigest(), 'seconds': round(len(x) / info['rate'], 6),
                   'channels': int(np.asarray(x).reshape(len(x), -1).shape[1]), 'rate': int(info['rate']),
                   'licence': 'CC0-1.0', 'measures': measure(x, info['rate'])}
            _describe(row, meta[stem])
            rows.append(row)
            if len(rows) % 500 == 0:
                log(f'{pack_id}: {len(rows)} of {len(cc0)} recordings ({time.time() - started:.0f} s)')
    finally:
        parts.close()
    passing = sum(1 for r in rows if not r['screen'])
    index['packs'][pack_id] = {
        'title': f'FSD50K {"evaluation" if which == "eval" else "development"} set, CC0 recordings',
        'author': 'Freesound authors (each file names its own); compiled by Fonseca et al. (FSD50K, CC BY 4.0)',
        'licence': 'CC0-1.0', 'page': PAGE, 'archive_sha256': None,
        'status': 'verified' if check_md5 else 'unchecked', 'files': len(rows), 'passing': passing}
    others = [r for r in index['files'] if r['library'] != pack_id]         # a release pack's entries are among the new
    index['files'] = sorted(others + rows, key=lambda r: (r['library'], r['file']))
    samples._write_index(library, index)
    return {'pack': pack_id, 'status': index['packs'][pack_id]['status'], 'files': len(rows), 'passing': passing,
            'folder': str(out)}


# ---------------------------------------------------------------- a release's packs

def read_pack_index(folder, expected=None):
    """The index of a folder of packs, after checking that it is the one this version knows (when it knows one), its
    schema, and that its part names are plain file names and its entries CC0 recordings at their own paths."""
    path = Path(folder) / PACK_INDEX
    if not path.is_file():
        raise FSD50KError(f'{folder} holds no {PACK_INDEX}')
    expected = RELEASE_INDEX_SHA256 if expected is None else expected
    if expected and sha256_of(path) != expected:
        raise FSD50KError(f'{PACK_INDEX} is not the one this version of the skill expects')
    try:
        index = json.loads(path.read_text(encoding='utf-8'))
    except ValueError:
        raise FSD50KError(f'{PACK_INDEX} is damaged') from None
    if not isinstance(index, dict) or index.get('schema') != PACK_SCHEMA:
        raise FSD50KError(f'{PACK_INDEX} is not an index of FSD50K packs')
    for part in index.get('parts', []):
        if not PACK_PART_RE.fullmatch(str(part.get('name'))):
            raise FSD50KError(f"unsafe part name in {PACK_INDEX}: {part.get('name')!r}")
    for row in index.get('files', []):
        name = f"{row.get('library')}/{row.get('file')}.wav"
        if (not PACK_MEMBER_RE.fullmatch(name) or row.get('path') != name or row.get('licence') != 'CC0-1.0'
                or not re.fullmatch(r'[0-9a-f]{64}', str(row.get('sha256')))):
            raise FSD50KError(f'a wrong entry in {PACK_INDEX}: {name}')
    return index


def _from_release(opener):
    """The opener for a release's own files: a file the release no longer holds (RELEASE_GONE) stops the download and
    says what to do; every other failure is handled as before (a part is retried and resumed)."""
    def opened(url, headers):
        try:
            return opener(url, headers)
        except urllib.error.HTTPError as error:
            if error.code in RELEASE_GONE:
                raise FSD50KError(f"{url.rsplit('/', 1)[-1]} is unavailable on this release: update the skill to its latest "
                                  'version, which names the current packs, or fetch the original dataset with --source '
                                  'zenodo') from None
            raise
    return opened


def download_packs(folder, url=None, index_sha256=None, opener=None, sleep=time.sleep, log=print):
    """Fetch a release's packs into `folder`: the index (checked against the sum this version knows) and every part
    (checked against the index, resumed like the dataset's files). Returns the index."""
    url = (RELEASE_URL if url is None else url).rstrip('/')
    if not url:
        raise FSD50KError('this copy of the skill names no release with packs: use --source zenodo')
    expected = RELEASE_INDEX_SHA256 if index_sha256 is None else index_sha256
    opener = _from_release(opener or _urlopen)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / PACK_INDEX
    if not (expected and path.is_file() and sha256_of(path) == expected):          # without a known sum, always fresh
        blob = _read_all(opener(f'{url}/{PACK_INDEX}', {}))
        if expected and hashlib.sha256(blob).hexdigest() != expected:
            raise FSD50KError(f'{PACK_INDEX} is not the one this version of the skill expects')
        tmp = folder / f'{PACK_INDEX}.part'
        if is_link(tmp):
            raise FSD50KError(f'{tmp} is a link, and a download does not write through one: move it away and run this again')
        tmp.write_bytes(blob)
        tmp.replace(path)
    index = read_pack_index(folder, expected)
    for part in index['parts']:
        name, digest = part['name'], part['sha256']
        if (folder / name).is_file() and sha256_of(folder / name) == digest:
            continue
        _fetch(opener, f'{url}/{name}', folder, name, int(part['bytes']), log, sleep,
               good=lambda p, d=digest: sha256_of(p) == d)
    return index


def add_packs(folder, library, log=print):
    """Import a folder of packs into the library; returns {'added', 'files', 'packs', 'folder'}. Before anything is
    written, every part is checked against the index and the parts must hold exactly the recordings it names; every
    recording is checked against its own SHA-256, and a file already there with other bytes is an error (nothing is
    replaced)."""
    from . import samples
    folder, library = Path(folder), Path(library)
    index = read_pack_index(folder)
    wanted = {r['path'] for r in index['files']}
    held = set()
    for part in index['parts']:
        path = folder / part['name']
        if not path.is_file() or sha256_of(path) != part['sha256']:
            raise FSD50KError(f"{part['name']} is missing or damaged; download it again")
        with zipfile.ZipFile(path) as z:
            names = {i.filename for i in z.infolist() if not i.is_dir()}
        strays = sorted(names - wanted)
        if strays:
            raise FSD50KError(f"{part['name']} holds {strays[0]!r}, which the index does not name")
        held |= names
    missing = sorted(wanted - held)
    if missing:
        raise FSD50KError(f'no part holds {missing[0]}; the index and the parts do not belong together')
    with samples.locked(library):
        return _add_packs_locked(folder, library, index, log)


def _add_packs_locked(folder, library, index, log):
    from . import samples
    held = samples.load_index(library) if (library / samples.INDEX).is_file() else samples.new_index()
    rows = {(r['library'], r['file']): r for r in index['files']}
    have = {(r['library'], r['file']): r for r in held['files']}
    for key, row in rows.items():
        if key in have and have[key].get('sha256') != row['sha256']:
            raise FSD50KError(f"{row['path']} differs from the pack; import into a new library folder")
    written = 0
    for part in index['parts']:
        with zipfile.ZipFile(folder / part['name']) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            for info in infos:
                m = PACK_MEMBER_RE.fullmatch(info.filename)
                row = rows[(m.group(1), m.group(2))]
                out = samples.inside(library, library / row['library'])
                dest = out / f"{row['file']}.wav"
                if dest.is_file() and sha256_of(dest) == row['sha256']:
                    continue
                if dest.exists() or dest.is_symlink():
                    raise FSD50KError(f'{dest} differs from the pack; import into a new library folder')
                blob = samples._read_member(z, info)
                if hashlib.sha256(blob).hexdigest() != row['sha256']:
                    raise FSD50KError(f"{info.filename} in {part['name']} is damaged")
                out.mkdir(parents=True, exist_ok=True)
                samples._write_new(dest, blob)
                written += 1
                if written % 500 == 0:
                    log(f'{written} recordings written')
    added = sum(1 for key in rows if key not in have)
    have.update({key: dict(have.get(key, {}), **row) for key, row in rows.items()})   # the index's entry is the newer
    held['files'] = sorted(have.values(), key=lambda r: (r['library'], r['file']))
    for pack_id, pack in index['packs'].items():
        mine = [r for r in held['files'] if r['library'] == pack_id]
        there = held['packs'].get(pack_id)
        status = there['status'] if there and there.get('status') != PACK_STATUS else PACK_STATUS
        held['packs'][pack_id] = dict(pack, status=status, files=len(mine),
                                      passing=sum(1 for r in mine if not r.get('screen')))
    samples._write_index(library, held)
    return {'added': added, 'files': len(rows), 'packs': sorted(index['packs']), 'folder': str(library)}
