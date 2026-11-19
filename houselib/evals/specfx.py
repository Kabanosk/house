"""Content of the `spec_huge` fixture (K5 `long-input`): a synthetic ~500 000 character contest rulebook with an API
reference, the `validate.py` stub the agent implements to it, its hidden tests and their reference solution.

The rulebook is generated from a fixed seed (`SEED`), so two builds are byte-identical; nothing large is committed to
the house repo. `fixtures.spec_huge` writes `contest_files()` into the contest, `document()` ONLY as fixture material
(`<tmp>/material/rules.md`: the `pasted` arm pastes it into the prompt, the `sources` arm copies it into
`.house/sources/`) and `hidden_tests()` under `<tmp>/hidden/spec/`.

Rules an agent should know:
- `FACTS` are the ten questions of the case, each with the value that answers it. A planted value (`planted`) occurs
  exactly once in the document (digit-bounded, `occurrences`); a value computed from two sections (`absent`) occurs
  nowhere. The filler never emits any of them: every generated block is re-drawn until `RESERVED` finds nothing in it.
- The traps: Q1's rule (the final submission deadline, Section 2.3) is changed by erratum E-03 in Appendix C near the
  end of the document; Q2 has a near-duplicate line in another section (the warm-up quota 72 next to the final-phase
  quota 27); Q3, Q4 and Q8 need two sections each. `validate.py`'s rules (Section 4.3, codes SUB-01..SUB-08) are
  amended by erratum E-02 too (two decimal places, not three) and need the forecast window of Section 5.4.
- `contest_files(reference=True)` replaces the stub with `REFERENCE_VALIDATE`, the repo-authored reference solution.
  It exists for the unit tests that prove the hidden tests fail on the stub and pass on the reference; a case never
  uses it (the case loader and the sandbox refuse `reference`).
"""
import functools
import random
import re
import textwrap

SEED = 20261119
TARGET_CHARS = 500_000
MATERIAL = "rules.md"  # the document, `<tmp>/material/rules.md`
SOURCES_INDEX_MATERIAL = "sources-index.json"  # `.house/sources/index.json` as `house sources add` wrote it
SOURCES_GITIGNORE_MATERIAL = "sources-gitignore"  # `.house/sources/.gitignore` as `house sources add` wrote it
HIDDEN = "spec"  # `<tmp>/hidden/spec/`
HIDDEN_FILE = "test_validate.py"
CHALLENGE = "Halcyon Grid Forecasting Challenge 2027"


def _d(text: str) -> str:
    """Dedent a block and strip the leading newline."""
    return textwrap.dedent(text).lstrip("\n")


def _num(value: str) -> str:
    """A regex for a number as written in prose: digit-bounded, a thousands separator allowed after the first group."""
    digits = value.replace(" ", "")
    head = len(digits) % 3 or 3
    groups = [digits[:head]] + [digits[i:i + 3] for i in range(head, len(digits), 3)]
    return r"(?<![\d])" + r"[ ,.  ]?".join(groups) + r"(?![\d])"


# --------------------------------------------------------------------------------------------------------------------
# The ten facts
# --------------------------------------------------------------------------------------------------------------------

def _token_header() -> str:
    """The API token header, drawn from its own seed so that it cannot be guessed from the challenge's names."""
    rng = random.Random(f"{SEED}-token-header")
    first = rng.choice(["Lumen", "Tessel", "Varro", "Quillon", "Morrow", "Brackett"])
    second = rng.choice(["Pass", "Seal", "Ticket", "Badge", "Warrant"])
    return f"X-{first}{rng.randint(2, 9)}-{second}"


TOKEN_HEADER = _token_header()

FACTS = [
    {"label": "fact-01", "q": 1, "kind": "erratum",
     "question": "What is the final submission deadline? Give the date as YYYY-MM-DD.",
     "answer": "2027-03-21", "value": r"2027-03-21(?:[ T]23:59(?::00)?(?: ?UTC|Z)?)?",
     "planted": [r"2027-03-21"], "decoys": ["2027-03-14"], "absent": []},
    {"label": "fact-02", "q": 2, "kind": "near-duplicate",
     "question": "During the final phase, how many submission files may a team upload per calendar week?",
     "answer": "27", "value": r"27(?: (?:submission )?(?:files|submissions|uploads))?(?: (?:per|a|/) ?(?:calendar )?week)?",
     "planted": [_num("27")], "decoys": ["72"], "absent": []},
    {"label": "fact-03", "q": 3, "kind": "combined",
     "question": "How much prize money in euros, in total, does a team receive that wins first place in the Solar "
                 "track and also the Open Methods Award?",
     "answer": "22750", "value": r"(?:€ ?|EUR ?)?22[ ,  ]?750(?:\.00)?(?: ?(?:€|EUR|euros?))?",
     "planted": [_num("18 500"), _num("4 250")], "decoys": ["18500"], "absent": [_num("22 750")]},
    {"label": "fact-04", "q": 4, "kind": "combined",
     "question": "Which API error code does an upload get when the only rule its file breaks is having two rows "
                 "for the same site and timestamp?",
     "answer": "E-2207", "value": r"E-2207",
     "planted": [_num("2207")], "decoys": ["E-2311"], "absent": []},
    {"label": "fact-05", "q": 5, "kind": "lookup",
     "question": "How many GPU-hours may a team's final evaluation run use at most?",
     "answer": "340", "value": r"340(?: ?GPU[- ]hours?)?",
     "planted": [_num("340")], "decoys": ["48"], "absent": []},
    {"label": "fact-06", "q": 6, "kind": "lookup",
     "question": "What is the largest value the `limit` query parameter of `GET /v2/submissions` accepts?",
     "answer": "175", "value": r"175",
     "planted": [_num("175")], "decoys": ["500"], "absent": []},
    {"label": "fact-07", "q": 7, "kind": "lookup",
     "question": "Which HTTP header carries the team's API token?",
     "answer": TOKEN_HEADER, "value": re.escape(TOKEN_HEADER) + ":?",
     "planted": [re.escape(TOKEN_HEADER)], "decoys": ["Authorization"], "absent": []},
    {"label": "fact-08", "q": 8, "kind": "combined",
     "question": "How many test sites does the private leaderboard use?",
     "answer": "434", "value": r"434(?: (?:test )?sites)?",
     "planted": [_num("1 240"), _num("35")], "decoys": ["1240"], "absent": [_num("434")]},
    {"label": "fact-09", "q": 9, "kind": "lookup",
     "question": "What is the largest submission file, in MiB, that `POST /v2/submissions` accepts?",
     "answer": "37", "value": r"37(?: ?MiB)?",
     "planted": [_num("37")], "decoys": ["8"], "absent": []},
    {"label": "fact-10", "q": 10, "kind": "lookup",
     "question": "To which e-mail address are appeals sent?",
     "answer": "appeals-q7k@halcyon-grid.example", "value": r"<?(?:mailto:)?appeals-q7k@halcyon-grid\.example>?",
     "planted": [r"appeals-q7k@halcyon-grid\.example"], "decoys": ["rules-desk@halcyon-grid.example"], "absent": []},
]

# Every value the filler must never produce: the planted values, the decoys and the computed answers.
_RESERVED_PATTERNS = [p for f in FACTS for p in f["planted"] + f["absent"]] + [
    _num("72"), r"2027-03-14", r"E-2311", r"Open Methods Award", r"appeals-q7k", r"SUB-0\d",
    r"errat", r"2027-04-(?:0[5-9]|1[01])", r"\b2500\b", r"5 000"]
RESERVED = re.compile("|".join(f"(?:{p})" for p in _RESERVED_PATTERNS), re.IGNORECASE)


def answer_pattern(fact: dict) -> str:
    """The `final_answer` regex of a fact: the line `Q<n>: <value>`, markdown emphasis allowed, nothing else.

    The case gives it with `key: "Q<n>"`, so the grader applies it to the last `Q<n>:` line only
    (`grade.final_answer_holds`).
    """
    return (rf"(?im)^[\s>*_`-]*Q{fact['q']}[\s*_`]*:[\s*_`]*(?:{fact['value']})[\s*_`]*\.?[\s*_`]*$")


def occurrences(pattern: str, text: str) -> int:
    """How often a fact pattern occurs in a text (case-insensitive, as a grep would find it)."""
    return len(re.findall(pattern, text, re.IGNORECASE))


# --------------------------------------------------------------------------------------------------------------------
# Filler prose
# --------------------------------------------------------------------------------------------------------------------

_PARTIES = ["the Organiser", "the Jury", "the Technical Committee", "the Team Captain", "a Participant", "a Team",
            "the Host Platform", "the Data Steward", "the Compliance Officer", "the Evaluation Team"]
_DOCS = ["the Registration Form", "the Team Agreement", "the Solution Description", "the Model Card",
         "the Data Use Agreement", "the Code Escrow Notice", "the Prize Claim Form", "the Final Report",
         "the Declaration of Originality", "the Conflict of Interest Statement"]
_VERBS = ["submit", "sign and return", "update", "confirm", "keep a copy of", "make available", "countersign",
          "send a scanned copy of"]
_MODALS = ["must", "may", "shall", "is expected to"]
_NUMS = [2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 15, 20, 21, 28, 30, 45, 60, 90]
_TIMINGS = ["within {n} days of registration", "before the start of the final phase",
            "no later than {n} business days after a request", "at least {n} hours before the relevant deadline",
            "once per phase", "on request", "within {n} days of a change", "before any prize is paid"]
_CONDITIONS = ["a Team changes its members", "a dataset is withdrawn", "a deadline falls on a public holiday in "
               "the Organiser's country", "the Host Platform is unavailable for more than {n} hours",
               "two Teams upload files that are identical", "a Participant withdraws", "a ruling of the Jury is "
               "challenged", "a meter reports values outside its physical range", "a sponsor withdraws its support",
               "a Team cannot be reached for {n} days", "the evaluation cluster is under maintenance"]
_REMEDIES = ["extend the relevant deadline by up to {n} days", "ask for a written explanation",
             "suspend the affected uploads until the matter is settled", "re-run the evaluation on the last valid "
             "file", "publish a clarification on the forum", "invite the Team Captain to a call",
             "reduce the affected period from the scoring", "postpone the announcement by {n} days"]
_FAULTS = ["is received after its deadline", "is incomplete", "names the wrong Team", "is not signed by the Team "
           "Captain", "cannot be verified", "refers to a withdrawn dataset", "was sent to the wrong address"]
_CONSEQUENCES = ["treated as not received", "returned with a request to correct it within {n} days",
                 "set aside until the Technical Committee has reviewed it", "recorded but not taken into account",
                 "forwarded to the Jury for a decision"]
_RETENTIONS = ["{n} months after the end of the challenge", "the duration of the challenge", "{n} years",
               "as long as the law of the Organiser's country requires"]

_TEMPLATES = [
    "{Party} {modal} {verb} {doc} {timing}.",
    "Where {condition}, {party} may {remedy}.",
    "Notices under this clause are published on the challenge forum and take effect {n} days after publication.",
    "A {topic} that {fault} is {consequence}.",
    "{Party} keeps a record of every {topic} for {retention}.",
    "Requests concerning a {topic} are answered within {n} business days; a request that has not been answered is "
    "not deemed accepted.",
    "Nothing in this clause limits the rights of {party} under Section {ref}.",
    "For the purpose of this clause a {topic} counts from the moment the Host Platform records it, not from the "
    "moment it is sent.",
    "{Party} must not use a {topic} for any purpose other than the challenge without the written consent of "
    "{party2}.",
    "If {party} cannot meet a deadline for a {topic} for reasons beyond its control, it informs {party2} {timing} "
    "and proposes a new date.",
    "The decision of {party} on a {topic} is final unless an appeal is lodged under Section 12.",
    "Each {topic} is identified by the Team's registered name and the time at which the Host Platform recorded it.",
    "{Party} may delegate the handling of a {topic} to {party2}, who then acts under the same obligations.",
    "Copies of {doc} are kept by {party} and shown to {party2} on request.",
    "A {topic} sent by e-mail instead of through the Host Platform is {consequence}.",
    "When this clause refers to days, calendar days are meant; a period that ends on a weekend ends on the next "
    "working day.",
    "{Party} answers questions about a {topic} on the forum, so that every Team receives the same information.",
    "In case of doubt about a {topic}, the version recorded by the Host Platform prevails over any local copy.",
    "{Party} may publish anonymised statistics about each {topic} after the challenge has ended.",
    "The obligations of this clause survive the end of the challenge for {retention}.",
    "Where {condition}, {party} informs every Team affected {timing}.",
    "A {topic} may be corrected by {party} once; a second correction needs the approval of {party2}.",
]

_TOPICS = {
    1: ["notice", "clarification", "change to the rules", "forum announcement", "translation"],
    2: ["milestone", "phase", "deadline", "registration period", "announcement"],
    3: ["registration", "team change", "membership request", "captaincy transfer", "eligibility check"],
    4: ["upload", "solution description", "reproducibility package", "final selection", "file name"],
    5: ["dataset", "data request", "data correction", "weather extract", "meter export"],
    6: ["evaluation run", "container image", "compute request", "cluster reservation", "hardware report"],
    7: ["prize claim", "payment", "tax certificate", "award nomination", "bank detail"],
    8: ["score", "leaderboard entry", "tie-break", "re-scoring request", "metric report"],
    9: ["code freeze", "verification", "interview", "upload window", "finalist report"],
    10: ["complaint", "warning", "forum post", "sanction", "conduct report"],
    11: ["licence", "publicity request", "open-source notice", "ownership claim", "citation"],
    12: ["appeal", "dispute", "hearing", "written statement", "ruling"],
    0: ["request", "record", "notice", "change", "report"],
}


class _Prose:
    """Seeded filler sentences and clauses; every block is re-drawn until it contains no reserved value."""

    def __init__(self, rng: random.Random):
        self.rng = rng

    def clean(self, make, *args) -> str:
        for _ in range(200):
            text = make(*args)
            if not RESERVED.search(text):
                return text
        raise RuntimeError("spec_huge: filler keeps producing a reserved value")

    def _sentence(self, sec: int, template: str) -> str:
        r = self.rng
        party, party2 = r.sample(_PARTIES, 2)
        ref_sec, _, ref_subs = r.choice(_RULES)
        values = {
            "party": party, "Party": party[0].upper() + party[1:], "party2": party2, "modal": r.choice(_MODALS),
            "verb": r.choice(_VERBS), "doc": r.choice(_DOCS), "topic": r.choice(_TOPICS.get(sec, _TOPICS[0])),
            "condition": r.choice(_CONDITIONS), "remedy": r.choice(_REMEDIES), "fault": r.choice(_FAULTS),
            "consequence": r.choice(_CONSEQUENCES), "retention": r.choice(_RETENTIONS),
            "timing": r.choice(_TIMINGS), "ref": f"{ref_sec}.{r.randint(1, len(ref_subs))}",
        }
        text = template
        for _ in range(2):  # slots may contain `{n}` themselves
            text = text.format_map(_Slots(values, r))
        return _articles(text)

    def clause(self, sec: int) -> str:
        """Two to four different sentences about a section's topics."""
        return self.clean(lambda: " ".join(self._sentence(sec, t)
                                           for t in self.rng.sample(_TEMPLATES, self.rng.randint(2, 4))))


def _articles(text: str) -> str:
    """`a` before a vowel becomes `an` (`a eligibility check` -> `an eligibility check`)."""
    return re.sub(r"\b([Aa]) (?=[aeiouAEIOU])", lambda m: m.group(1) + "n ", text)


class _Slots(dict):
    """`str.format_map` mapping: named slots from `values`, a fresh number for every `{n}`."""

    def __init__(self, values: dict, rng: random.Random):
        super().__init__(values)
        self.rng = rng

    def __missing__(self, key: str) -> str:
        if key == "n":
            return str(self.rng.choice(_NUMS))
        raise KeyError(key)


# --------------------------------------------------------------------------------------------------------------------
# Hand-written passages (the facts, the validator rules, the API tables)
# --------------------------------------------------------------------------------------------------------------------

_PREAMBLE = _d(f"""
    # {CHALLENGE} — Official Rules and Submission API Reference

    Version 3.2. This document is the complete rulebook of the {CHALLENGE} ("the challenge"): Part I holds the
    official rules, Part II the reference of the submission API, and the appendices a glossary, the change log, the
    errata and the contact details. Section numbers are stable across versions; a clause is cited as section,
    subsection and clause number, for example 4.3.2.

    Errata to this version are listed in Appendix C. Where an erratum and the body of the rules differ, the erratum
    applies.
    """)

_RULES = [  # (section, title, [subsection titles])
    (1, "About the challenge", ["Organiser", "Purpose of the challenge", "How to read these rules",
                                "Definitions used in these rules", "Language of the rules", "Changes to the rules"]),
    (2, "Timeline", ["Phases at a glance", "Warm-up phase", "Final phase", "Evaluation and announcement",
                     "Public holidays and outages"]),
    (3, "Eligibility and teams", ["Who may take part", "Excluded persons", "Forming a team", "Team registration",
                                  "Changes to a team", "Team captains", "Merging teams"]),
    (4, "Submissions", ["What a submission is", "Naming of submission files", "Submission file format (final phase)",
                        "Selecting final submissions", "Late and partial submissions",
                        "Submission quotas in the warm-up phase", "Solution descriptions",
                        "Reproducibility package"]),
    (5, "Data", ["Sites", "Meter readings", "Weather inputs", "Forecast windows", "Data use agreement",
                 "External data", "Data errors and corrections"]),
    (6, "Compute and hardware", ["Evaluation cluster", "Compute budgets", "Container images",
                                 "Network access during evaluation", "Hardware failures"]),
    (7, "Prizes", ["Track prizes", "Payment of prizes", "Taxes", "Special awards", "Unclaimed prizes"]),
    (8, "Scoring and leaderboards", ["Metric", "Public and private leaderboards", "Tie-breaking",
                                     "Leaderboard freezes", "Re-scoring"]),
    (9, "Final phase operations", ["Opening of the final phase", "Upload quotas in the final phase", "Code freeze",
                                   "Verification of winning solutions", "Interviews with finalists"]),
    (10, "Conduct", ["Fair play", "Sharing code and data", "Multiple accounts", "Communication on the forum",
                     "Sanctions"]),
    (11, "Intellectual property and licensing", ["Ownership of solutions", "Licence granted to the Organiser",
                                                 "Open-source components", "Publicity"]),
    (12, "Appeals and disputes", ["Grounds for appeal", "Time limits", "How to appeal", "Decision of the Jury",
                                  "Governing law"]),
]

# Hand-written clauses, inserted at a position among the filler clauses of a subsection: (section, sub) -> [(i, text)]
_PLANTED = {
    (1, 3): [(0, "These rules use \"must\" for an obligation and \"may\" for a permission. Examples are "
                 "illustrations; where an example and a rule differ, the rule applies. Errata (Appendix C) take "
                 "precedence over the body of the rules.")],
    (2, 1): [(0, "The challenge has a warm-up phase and a final phase. The warm-up phase is for trying the data, "
                 "the upload service and the API; only the final phase counts for the prizes. All times in these "
                 "rules are UTC.")],
    (2, 2): [(1, "The warm-up phase opens on 2026-12-01 00:00 UTC and closes on 2027-01-31 23:59 UTC. Warm-up "
                 "scores are shown on the warm-up leaderboard only and are deleted when the final phase opens.")],
    (2, 3): [(1, "The final phase opens on 2027-02-15 00:00 UTC. Final submission deadline: 2027-03-14 23:59 UTC. "
                 "A file recorded by the Host Platform after the final submission deadline is not scored."),
             (3, "Forecasts made in the final phase cover the forecast window of Section 5.4, which lies after the "
                 "final submission deadline; they are scored once the metered values of the whole window are in.")],
    (4, 6): [(1, "In the warm-up phase each team may upload at most 72 submission files per calendar week "
                 "(Monday 00:00 UTC to Sunday 23:59 UTC). Files rejected by the upload service do not count "
                 "against this quota.")],
    (5, 1): [(1, "The test set consists of 1 240 sites: solar parks, wind farms and metered industrial loads in "
                 "three countries. Each site has an identifier of the form HG-nnnn and a rated capacity, given in "
                 "the site list of the API (Section 17).")],
    (5, 4): [(1, "The forecast window of the final phase runs from 2027-04-05T00:00:00Z to 2027-04-11T23:45:00Z; "
                 "both ends belong to the window. Forecasts are made for every quarter of an hour of the window."),
             (2, "The warm-up phase used the forecast window from 2026-12-14T00:00:00Z to 2026-12-20T23:45:00Z. "
                 "That window is closed and no longer accepted by the upload service.")],
    (6, 2): [(0, "During the warm-up phase an evaluation run may use at most 48 GPU-hours."),
             (2, "A team's final evaluation run may use at most 340 GPU-hours on the evaluation cluster; a run "
                 "that needs more is stopped and scored on the output it has written by then.")],
    (7, 1): [(0, "Prizes are awarded per track. The amounts below are gross amounts in euros per winning team:\n\n"
                 "| Track | First place | Second place | Third place |\n"
                 "|---|---|---|---|\n"
                 "| Solar | €18 500 | €9 000 | €4 500 |\n"
                 "| Wind | €16 000 | €8 000 | €4 000 |\n"
                 "| Load | €14 000 | €7 000 | €3 500 |\n\n"
                 "A team that wins a place in more than one track receives every prize it has won.")],
    (7, 4): [(1, "Besides the track prizes the Jury grants three special awards, each to one team of any track: "
                 "the Open Methods Award of €4 250 for the best solution published in full under an open-source "
                 "licence, the Student Team Award of €3 000 and the Jury Prize of €2 000. A special award is paid "
                 "in addition to any track prize the same team has won.")],
    (8, 2): [(1, "The private leaderboard is computed on 35% of the test sites, drawn at random before the final "
                 "phase opens and not disclosed; the public leaderboard is computed on the other 65%. Prizes are "
                 "decided on the private leaderboard only.")],
    (9, 2): [(1, "In the final phase each team may upload at most 27 submission files per calendar week "
                 "(Monday 00:00 UTC to Sunday 23:59 UTC). Files rejected by the upload service do not count "
                 "against this quota.")],
    (12, 3): [(1, "Appeals are sent by e-mail to appeals-q7k@halcyon-grid.example, with the team's registered "
                  "name in the subject line. Questions about these rules that are not appeals go to "
                  "rules-desk@halcyon-grid.example and are answered on the forum.")],
}

_FORMAT = _d("""
    4.3.1 A submission file is a UTF-8 text file in CSV form. Lines end with a line feed (LF) or with a carriage
    return and a line feed (CR LF); a line break after the last row is optional. Fields are separated by a single
    comma and are never quoted; no field contains a comma. The rules of this subsection have the codes SUB-01 to
    SUB-08, and the upload service reports the rules a file breaks by these codes (see also Section 16.3).

    4.3.2 SUB-01 (header). The first line is exactly `site_id,timestamp,forecast_kw`: no spaces, no byte order
    mark, nothing else. A file whose first line is anything else, and an empty file, breaks SUB-01. When a file
    breaks SUB-01 no other rule is checked, and SUB-01 is the only code reported for it.

    4.3.3 SUB-07 (no data). Every line after the header is a data row; only the empty string after a final line
    break is not a row. A file with a correct header and no data row breaks SUB-07, and SUB-07 is then the only
    code reported for it.

    4.3.4 SUB-08 (row shape). A data row that does not consist of exactly three fields breaks SUB-08; an empty line
    between rows is such a row. The fields of a row that breaks SUB-08 are not checked against SUB-02 to SUB-05.

    4.3.5 SUB-02 (site). `site_id` is `HG-` followed by exactly four digits, for example `HG-0412`, in upper case.
    `HG-0000` is not a site.

    4.3.6 SUB-03 (timestamp). `timestamp` is a UTC time written `YYYY-MM-DDTHH:MM:00Z`: a valid calendar date and
    time of day, the letter `T`, the seconds always `00` and the suffix `Z`. The minutes are `00`, `15`, `30` or
    `45`, and the time lies inside the forecast window of the final phase (Section 5.4).

    4.3.7 SUB-04 (forecast). `forecast_kw` is a plain decimal number: one or more digits, optionally followed by a
    dot and one to three digits. No sign, no exponent, no spaces and no thousands separator. Its value lies between
    0 and 2500 inclusive.

    4.3.8 SUB-05 (duplicates). No two data rows have the same `site_id` and the same `timestamp`, compared as
    written. Rows that break SUB-08 are not compared.

    4.3.9 SUB-06 (size). A file holds at most 5 000 data rows. A file with more rows breaks SUB-06; the other rules
    still apply to all of its rows.

    4.3.10 A file breaks a rule once, however many of its rows break it. The upload service reports every rule a
    file breaks, each code once, in ascending order of the codes.
    """)

_API = [  # (section, title, [subsection titles])
    (13, "API overview", ["Base URLs", "Versioning", "Conventions", "Pagination", "Time and number formats"]),
    (14, "Authentication", ["API tokens", "Token rotation", "Webhook signatures", "Service accounts"]),
    (15, "Rate limits", ["Limits per token", "Limit headers", "Bursts and back-off"]),
    (16, "Errors", ["Error body", "Transport errors", "Upload error codes", "Retrying"]),
    (17, "Sites endpoints", ["GET /v2/sites", "GET /v2/sites/{site_id}", "GET /v2/sites/{site_id}/readings",
                             "GET /v2/sites/{site_id}/weather"]),
    (18, "Submissions endpoints", ["POST /v2/submissions", "GET /v2/submissions", "GET /v2/submissions/{id}",
                                   "POST /v2/submissions/{id}/select", "DELETE /v2/submissions/{id}"]),
    (19, "Leaderboard endpoints", ["GET /v2/leaderboard", "GET /v2/leaderboard/history",
                                   "GET /v2/teams/{team_id}/scores"]),
    (20, "Reports endpoints", ["POST /v2/reports", "GET /v2/reports", "GET /v2/reports/{id}"]),
    (21, "Webhooks", ["Subscribing", "Event types", "Delivery and retries"]),
    (22, "Sandbox", ["Purpose of the sandbox", "Differences from production", "Resetting the sandbox"]),
    (23, "Client libraries and examples", ["Python client", "Command-line client", "Worked example"]),
    (24, "Legacy v1 API (retired)", ["Status of v1", "Differences from v2", "Migrating from v1"]),
]

_ERRORS = _d("""
    Every upload error has a code of the form E-nnnn and an HTTP status. The codes of the upload service are:

    | Code | HTTP status | Meaning |
    |---|---|---|
    | E-2201 | 400 | The request is not `multipart/form-data` or has no `file` part. |
    | E-2203 | 422 | The file breaks rule SUB-01 of the rules (Section 4.3). |
    | E-2205 | 422 | The file breaks rule SUB-07 of the rules (Section 4.3). |
    | E-2207 | 422 | The file breaks rule SUB-05 of the rules (Section 4.3). |
    | E-2209 | 422 | The file breaks rule SUB-03 of the rules (Section 4.3). |
    | E-2212 | 422 | The file breaks rule SUB-08 of the rules (Section 4.3). |
    | E-2214 | 422 | The file breaks rule SUB-02 of the rules (Section 4.3). |
    | E-2218 | 422 | The file breaks rule SUB-04 of the rules (Section 4.3). |
    | E-2221 | 422 | The file breaks rule SUB-06 of the rules (Section 4.3). |
    | E-2230 | 413 | The request is larger than the endpoint accepts. |
    | E-2311 | 409 | The team already uploaded a file with the same SHA-256 checksum (a repeated upload). |
    | E-2312 | 429 | The team's weekly upload quota is used up. |
    | E-2318 | 403 | The phase named in the request is not open. |

    A file that breaks several rules gets the error of the rule with the lowest code in the `code` field and every
    broken rule in the `details` list.
    """)

_API_PLANTED = {  # (section, sub) -> [(i, text)] like `_PLANTED`
    (13, 1): [(0, "The production API is served at `https://api.halcyon-grid.example/v2` and the sandbox at "
                  "`https://sandbox.halcyon-grid.example/v2` (Section 22). Both accept HTTPS only.")],
    (13, 3): [(0, "Request and response bodies are JSON unless stated otherwise. Authentication headers are left "
                  "out of the examples in this part; every request carries them as Section 14.1 describes.")],
    (14, 1): [(1, f"Every request carries the team's API token in the `{TOKEN_HEADER}` header. A request without "
                  "it, or with a token that has been revoked, is answered with HTTP 401 and no body. Responses carry "
                  "an `X-HG-Request-Id` header, which support asks for when a request is investigated.")],
    (14, 3): [(0, "Webhook deliveries (Section 21) carry an `X-HG-Signature` header: the hex HMAC-SHA256 of the raw "
                  "body, keyed with the webhook secret shown once when the subscription is made.")],
    (16, 3): [(0, _ERRORS)],
    (18, 1): [(0, "Uploads a submission file for the open phase. The body is `multipart/form-data` with the parts "
                  "`file` (the CSV of Section 4.3), `phase` (`warmup` or `final`) and an optional `note` of at most "
                  "200 characters. The file part may be at most 37 MiB; a larger request is refused with HTTP 413 "
                  "and the error E-2230. The upload service checks the file against the rules of Section 4.3 before "
                  "it is stored and answers with the errors of Section 16.3.")],
    (18, 2): [(0, "Lists the team's uploads, newest first. Query parameters: `phase` (optional, `warmup` or "
                  "`final`), `cursor` (optional, from the previous page) and `limit`, the page size: an integer "
                  "from 1 to 175, default 50. A larger `limit` is refused with HTTP 400.")],
    (17, 1): [(0, "Lists the sites of the test set with their identifiers, names, regions and rated capacities. "
                  "Query parameters: `region` (optional), `cursor` (optional) and `limit`, the page size: an "
                  "integer from 1 to 500, default 100.")],
    (19, 1): [(0, "Returns the public leaderboard of the open phase. Query parameters: `track` (`solar`, `wind` or "
                  "`load`), `cursor` (optional) and `limit`, the page size: an integer from 1 to 200, default 25.")],
    (20, 1): [(0, "Uploads a report (a PDF or Markdown file) for the Jury. The body is `multipart/form-data` with "
                  "the parts `file` and `kind`; the file may be at most 12 MiB.")],
    (22, 2): [(0, "The sandbox accepts files of at most 8 MiB, applies no weekly quota and never scores a file: it "
                  "only runs the checks of Section 4.3 and answers with the same errors as production.")],
    (24, 2): [(0, "Version 1 of the API accepted files with the header `site,ts,value`, values with up to four "
                  "decimal places and no forecast window; it authenticated with an `Authorization: Bearer` header. "
                  "None of this applies to version 2.")],
}

_ERRATA = _d("""
    | Erratum | Where | Correction |
    |---|---|---|
    | E-01 | 3.4.2 | The form is called the Registration Form, not the Entry Form. |
    | E-02 | 4.3.7 | `forecast_kw` has at most two decimal places, not three: one or more digits, optionally followed by a dot and one or two digits. A value with three decimal places breaks SUB-04. |
    | E-03 | 2.3 | The final submission deadline is 2027-03-21 23:59 UTC, one week later than first published. The other dates of Section 2 are unchanged. |
    | E-04 | 19.2 | The leaderboard history is kept for the whole challenge, not for the last 30 days only. |
    | E-05 | 7.1 | The third prize of the Load track is €3 750, not €3 500. |
    """)

_GLOSSARY = ["Applicable law", "Captain", "Capacity", "Code freeze", "Cursor", "Data Steward", "Evaluation cluster",
             "Final phase", "Forecast", "Forum", "Host Platform", "Jury", "Leaderboard", "Meter", "Organiser",
             "Participant", "Phase", "Private leaderboard", "Public leaderboard", "Quarter hour", "Rated capacity",
             "Reading", "Registration", "Reproducibility package", "Sandbox", "Site", "Solution", "Solution "
             "Description", "Submission file", "Team", "Technical Committee", "Test set", "Token", "Track",
             "Upload", "Upload service", "Warm-up phase", "Webhook", "Window", "Working day"]

_CHANGELOG = [("3.2", "2026-11-02", "API reference: cursors replace page numbers in every list endpoint."),
              ("3.1", "2026-10-12", "Prizes: the special awards were added to Section 7."),
              ("3.0", "2026-09-21", "Part II rewritten for version 2 of the API; version 1 is retired."),
              ("2.4", "2026-08-03", "Conduct: the rules on multiple accounts were made stricter."),
              ("2.3", "2026-07-06", "Data: the weather inputs moved to a separate endpoint."),
              ("2.2", "2026-06-15", "Eligibility: employees of sponsors may take part outside the prize ranking."),
              ("2.1", "2026-05-18", "Timeline: the warm-up phase was extended."),
              ("2.0", "2026-04-20", "First public version.")]


# --------------------------------------------------------------------------------------------------------------------
# API filler
# --------------------------------------------------------------------------------------------------------------------

_FIELDS = {
    "sites": [("site_id", "string", "Site identifier, `HG-` and four digits."), ("name", "string", "Display name."),
              ("region", "string", "Region code."), ("kind", "string", "`solar`, `wind` or `load`."),
              ("capacity_kw", "number", "Rated capacity in kW."), ("timezone", "string", "IANA time zone."),
              ("commissioned", "date", "Date of commissioning."), ("latitude", "number", "WGS 84 latitude."),
              ("longitude", "number", "WGS 84 longitude."), ("meters", "integer", "Number of meters.")],
    "submissions": [("id", "string", "Upload identifier."), ("uploaded_at", "time", "When it was recorded."),
                    ("phase", "string", "`warmup` or `final`."), ("rows", "integer", "Number of data rows."),
                    ("sha256", "string", "Checksum of the file."), ("status", "string",
                                                                   "`accepted`, `rejected` or `scored`."),
                    ("score_public", "number", "Public leaderboard score, once scored."),
                    ("selected", "boolean", "Whether the file is selected for the final ranking."),
                    ("note", "string", "The note given at upload.")],
    "leaderboard": [("rank", "integer", "Position on the leaderboard."), ("team", "string", "Registered name."),
                    ("score", "number", "Score of the best selected file."), ("uploads", "integer",
                                                                              "Files uploaded in the phase."),
                    ("last_upload", "time", "Time of the latest upload."), ("track", "string", "Track name.")],
    "reports": [("id", "string", "Report identifier."), ("kind", "string", "`solution` or `incident`."),
                ("created_at", "time", "When it was uploaded."), ("bytes", "integer", "Size of the file."),
                ("status", "string", "`received` or `reviewed`."), ("reviewer", "string", "Jury member, if any.")],
}
_VALUES = {  # example values of the string fields that are not identifiers
    "name": ["Kestrel Ridge PV", "Marsh Lane Wind", "Old Quarry Solar", "Harbour Cold Store", "Fenwick Downs Wind",
             "Copper Vale PV", "Northgate Foundry", "Saltmarsh Array"],
    "region": ["NW-1", "NW-2", "SE-1", "SE-3", "CE-2", "CE-4"],
    "kind": ["solar", "wind", "load"], "track": ["solar", "wind", "load"],
    "timezone": ["Europe/Lisbon", "Europe/Madrid", "Europe/Dublin"],
    "phase": ["warmup", "final"],
    "status": ["accepted", "rejected", "scored", "received", "reviewed"],
    "note": ["retrained with the new weather extract", "ensemble of three models", "fixed the night-time clipping",
             "baseline", "quantile blend"],
    "team": ["Gridlock", "Watt Else", "Low Pressure", "Quarter Hourly", "The Inverters", "Cloud Cover"],
    "reviewer": ["jury-a", "jury-b", "jury-c"],
}
_API_TOPICS = ["request", "response", "token", "page", "upload", "webhook", "retry", "field", "cursor", "header"]
_API_SENTENCES = [
    "Clients should treat unknown fields in a response as optional and ignore them.",
    "A field that has no value is left out of the response rather than sent as null.",
    "Times are written in ISO 8601 with the suffix Z, for example 2026-12-03T09:15:00Z.",
    "The {topic} is cached for {n} seconds; a client that needs a fresh value waits that long.",
    "Requests are idempotent unless stated otherwise, so a client may repeat a {topic} after a network error.",
    "A {topic} that is malformed is answered with HTTP 400 and an error body as described in Section 16.1.",
    "The order of the fields in a JSON object carries no meaning.",
    "Clients identify themselves with a `User-Agent` header naming the client and its version.",
    "Numbers are sent as JSON numbers, never as strings; identifiers are always strings.",
    "A {topic} older than {n} days is no longer returned by this endpoint.",
    "Support answers questions about a {topic} on the forum within {n} working days.",
    "When the API is under maintenance it answers HTTP 503 with a `Retry-After` header in seconds.",
    "Clients must follow the `next` cursor of a page until it is absent to read every {topic}.",
    "The examples use the production base URL; the sandbox accepts the same requests.",
    "A {topic} is never changed after it has been recorded; corrections are new records.",
    "Every list is sorted as described for the endpoint; a client must not rely on any other order.",
]


class _Api:
    """Seeded API reference blocks: descriptions, field tables and example responses."""

    def __init__(self, prose: _Prose):
        self.prose = prose
        self.rng = prose.rng

    def _sentences(self, count: int) -> str:
        r = self.rng

        def make():
            return _articles(" ".join(t.format_map(_Slots({"topic": r.choice(_API_TOPICS)}, r))
                                      for t in r.sample(_API_SENTENCES, count)))

        return self.prose.clean(make)

    def paragraph(self) -> str:
        return self._sentences(self.rng.randint(3, 5))

    def table(self, group: str) -> str:
        def make():
            fields = self.rng.sample(_FIELDS[group], self.rng.randint(4, len(_FIELDS[group])))
            rows = [f"| `{name}` | {kind} | {desc} |" for name, kind, desc in fields]
            return "| Field | Type | Description |\n|---|---|---|\n" + "\n".join(rows)
        return self.prose.clean(make)

    def _value(self, name: str, kind: str):
        r = self.rng
        if name in _VALUES:
            return r.choice(_VALUES[name])
        if name == "site_id":
            return f"HG-{r.randint(1, 1239):04d}"
        if kind == "integer":
            return r.randint(1, 4000)
        if kind == "number":
            return round(r.uniform(0, 900), 1)
        if kind == "boolean":
            return r.choice([True, False])
        if kind == "date":
            return f"20{r.randint(10, 25)}-0{r.randint(1, 9)}-1{r.randint(0, 9)}"
        if kind == "time":
            return f"2026-12-{r.randint(10, 26)}T{r.randint(0, 23):02d}:{r.choice(['00', '15', '30', '45'])}:00Z"
        return "".join(r.choice("0123456789abcdef") for _ in range(64 if name == "sha256" else 16))

    def example(self, group: str) -> str:
        def make():
            items = []
            for _ in range(self.rng.randint(2, 4)):
                fields = self.rng.sample(_FIELDS[group], self.rng.randint(4, len(_FIELDS[group])))
                body = ",\n".join(f'      "{name}": {_json(self._value(name, kind))}' for name, kind, _ in fields)
                items.append("    {\n" + body + "\n    }")
            return '```json\n{\n  "items": [\n' + ",\n".join(items) + '\n  ],\n  "next": null\n}\n```'
        return self.prose.clean(make)


def _json(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return f'"{value}"'


_GROUP = {17: "sites", 18: "submissions", 19: "leaderboard", 20: "reports", 13: "sites", 14: "submissions",
          15: "submissions", 16: "submissions", 21: "submissions", 22: "submissions", 23: "sites", 24: "sites"}


# --------------------------------------------------------------------------------------------------------------------
# The document
# --------------------------------------------------------------------------------------------------------------------

_RULE_SUB_CHARS = 3_950  # filler budget of a rules subsection (planted clauses come on top)
_API_SUB_CHARS = 4_250  # filler budget of an API subsection


def _rules_subsection(prose: _Prose, sec: int, sub: int) -> list[str]:
    """The numbered clauses of one rules subsection (filler up to the budget, planted clauses at their places)."""
    if (sec, sub) == (4, 3):
        return [_FORMAT.rstrip("\n")]
    planted = _PLANTED.get((sec, sub), [])
    clauses: list[str] = []
    while sum(len(c) for c in clauses) < _RULE_SUB_CHARS:
        clauses.append(prose.clause(sec))
    for i, text in sorted(planted):
        clauses.insert(min(i, len(clauses)), text)
    if len(clauses) > 20:
        raise RuntimeError(f"spec_huge: subsection {sec}.{sub} has more than 20 clauses")
    return [f"{sec}.{sub}.{k} {text}" for k, text in enumerate(clauses, 1)]


def _api_subsection(api: _Api, sec: int, sub: int, title: str) -> list[str]:
    """Paragraphs, a field table and (for endpoints) an example response of one API subsection."""
    planted = [text for _, text in sorted(_API_PLANTED.get((sec, sub), []))]
    group = _GROUP[sec]
    blocks = list(planted)
    endpoint = title.split(" ", 1)[0] in ("GET", "POST", "DELETE")
    if endpoint:
        blocks += [api.paragraph(), "Response fields:", api.table(group), "Example response:", api.example(group)]
    while sum(len(b) for b in blocks) < _API_SUB_CHARS:
        blocks.append(api.paragraph())
    return blocks


@functools.lru_cache(maxsize=1)
def document() -> str:
    """The rulebook (Markdown, ~500 000 characters), identical on every call and every machine."""
    rng = random.Random(SEED)
    prose = _Prose(rng)
    api = _Api(prose)
    out = [_PREAMBLE, "## Contents", ""]
    out += [f"- Part I, Section {sec}: {title}" for sec, title, _ in _RULES]
    out += [f"- Part II, Section {sec}: {title}" for sec, title, _ in _API]
    out += ["- Appendix A: Glossary", "- Appendix B: Change log", "- Appendix C: Errata",
            "- Appendix D: Contact details", "", "# Part I — Official Rules", ""]
    for sec, title, subs in _RULES:
        out += [f"## {sec}. {title}", ""]
        for sub, sub_title in enumerate(subs, 1):
            out += [f"### {sec}.{sub} {sub_title}", ""]
            for clause in _rules_subsection(prose, sec, sub):
                out += [clause, ""]
    out += ["# Part II — Submission API Reference", ""]
    for sec, title, subs in _API:
        out += [f"## {sec}. {title}", ""]
        for sub, sub_title in enumerate(subs, 1):
            out += [f"### {sec}.{sub} {sub_title}", ""]
            for block in _api_subsection(api, sec, sub, sub_title):
                out += [block, ""]
    out += ["# Appendices", "", "## Appendix A — Glossary", ""]
    for term in _GLOSSARY:
        out += [f"**{term}.** " + prose.clause(0), ""]
    out += ["## Appendix B — Change log", "", "| Version | Date | Change |", "|---|---|---|"]
    out += [f"| {v} | {d} | {c} |" for v, d, c in _CHANGELOG]
    out += ["", "## Appendix C — Errata", "",
            "The following corrections to version 3.2 were published after it. Each takes precedence over the "
            "clause it names.", "", _ERRATA.rstrip("\n"), "",
            "## Appendix D — Contact details", "",
            "The Organiser answers questions about the API at api-support@halcyon-grid.example and questions about "
            "prize payments at prizes@halcyon-grid.example. Appeals follow Section 12.3. The forum is the place "
            "for every question whose answer concerns all teams.", ""]
    return "\n".join(out)


# --------------------------------------------------------------------------------------------------------------------
# The contest: validate.py stub, a sample, a smoke test; the hidden tests and the reference
# --------------------------------------------------------------------------------------------------------------------

VALIDATE_STUB = _d('''
    """Check a submission file of the Halcyon Grid Forecasting Challenge before it is uploaded."""


    def validate(text: str) -> list[str]:
        """Return the sorted codes of the submission-file rules that `text` breaks; [] for a valid file."""
        raise NotImplementedError
    ''')

REFERENCE_VALIDATE = _d('''
    """Check a submission file of the Halcyon Grid Forecasting Challenge before it is uploaded (rules 4.3, E-02)."""
    import re
    from datetime import datetime, timezone
    from decimal import Decimal

    HEADER = "site_id,timestamp,forecast_kw"
    WINDOW = (datetime(2027, 4, 5, tzinfo=timezone.utc), datetime(2027, 4, 11, 23, 45, tzinfo=timezone.utc))
    SITE = re.compile(r"HG-\\d{4}")
    STAMP = re.compile(r"\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:00Z")
    NUMBER = re.compile(r"\\d+(?:\\.\\d{1,2})?")


    def _timestamp_ok(text: str) -> bool:
        if not STAMP.fullmatch(text):
            return False
        try:
            when = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        return when.minute in (0, 15, 30, 45) and WINDOW[0] <= when <= WINDOW[1]


    def validate(text: str) -> list[str]:
        """Return the sorted codes of the submission-file rules that `text` breaks; [] for a valid file."""
        lines = [line[:-1] if line.endswith("\\r") else line for line in text.split("\\n")]
        if lines and lines[-1] == "":
            lines.pop()
        if not lines or lines[0] != HEADER:
            return ["SUB-01"]
        rows = lines[1:]
        if not rows:
            return ["SUB-07"]
        codes = set()
        if len(rows) > 5000:
            codes.add("SUB-06")
        seen = set()
        for row in rows:
            fields = row.split(",")
            if len(fields) != 3:
                codes.add("SUB-08")
                continue
            site, stamp, value = fields
            if not SITE.fullmatch(site) or site == "HG-0000":
                codes.add("SUB-02")
            if not _timestamp_ok(stamp):
                codes.add("SUB-03")
            if not NUMBER.fullmatch(value) or Decimal(value) > 2500:
                codes.add("SUB-04")
            if (site, stamp) in seen:
                codes.add("SUB-05")
            seen.add((site, stamp))
        return sorted(codes)
    ''')

SAMPLE = _d("""
    site_id,timestamp,forecast_kw
    HG-0412,2027-04-05T00:00:00Z,0
    HG-0412,2027-04-05T00:15:00Z,12.5
    HG-0412,2027-04-05T00:30:00Z,118
    HG-1093,2027-04-05T00:00:00Z,903.4
    """)

SMOKE_TEST = _d('''
    import unittest
    from pathlib import Path

    import validate


    class SampleTest(unittest.TestCase):
        def test_the_sample_is_valid(self):
            text = (Path(__file__).resolve().parents[1] / "samples/submission-example.csv").read_text()
            self.assertEqual(validate.validate(text), [])


    if __name__ == "__main__":
        unittest.main()
    ''')

README = _d(f"""
    # Halcyon submission checker

    Our entry for the {CHALLENGE}. `validate.py` checks a submission file before we upload it, so a broken file
    never costs us one of the week's uploads. `samples/` has a small example file; `python3 -m unittest discover -s
    tests -t .` runs the tests.
    """)

HIDDEN_TESTS = _d('''
    """Acceptance tests of validate.py against the rulebook (Section 4.3 with erratum E-02, window of Section 5.4)."""
    import os
    import sys
    import unittest

    sys.path.insert(0, os.getcwd())

    import validate  # noqa: E402

    HEADER = "site_id,timestamp,forecast_kw"


    def csv(*rows, header=HEADER, end="\\n"):
        return end.join([header, *rows]) + end


    def slots(count):
        """`count` distinct valid (site, timestamp) rows inside the final-phase window."""
        out = []
        for i in range(count):
            day, quarter = divmod(i % 672, 96)
            out.append(f"HG-{1 + i // 672:04d},2027-04-{5 + day:02d}T{quarter // 4:02d}:{quarter % 4 * 15:02d}:00Z,1")
        return out


    class ValidateTest(unittest.TestCase):
        def check(self, text, codes):
            self.assertEqual(validate.validate(text), codes, repr(text[:200]))

        def test_a_valid_file_has_no_codes(self):
            self.check(csv("HG-0412,2027-04-05T00:00:00Z,0", "HG-0412,2027-04-05T00:15:00Z,12.5",
                           "HG-1093,2027-04-11T23:45:00Z,2500", "HG-9999,2027-04-08T13:30:00Z,903.47"), [])

        def test_crlf_and_no_final_line_break_are_fine(self):
            self.check(csv("HG-0001,2027-04-05T00:00:00Z,1", end="\\r\\n"), [])
            self.check(HEADER + "\\nHG-0001,2027-04-05T00:00:00Z,1", [])

        def test_wrong_or_missing_header_reports_only_sub01(self):
            self.check("", ["SUB-01"])
            self.check("\\n", ["SUB-01"])
            self.check(csv("HG-0001,2027-04-05T00:00:00Z,1", header="site_id, timestamp, forecast_kw"), ["SUB-01"])
            self.check(csv("bad,row", header="\\ufeff" + HEADER), ["SUB-01"])
            self.check(csv("HG-1,x,-1", header="site,ts,value"), ["SUB-01"])

        def test_header_only_reports_only_sub07(self):
            self.check(HEADER + "\\n", ["SUB-07"])
            self.check(HEADER, ["SUB-07"])

        def test_site(self):
            for site in ("HG-412", "hg-0412", "HG-04120", "HG-0000", "HX-0412", " HG-0412"):
                self.check(csv(f"{site},2027-04-05T00:00:00Z,1"), ["SUB-02"])

        def test_timestamp_format_and_quarter_hours(self):
            for stamp in ("2027-04-05T00:10:00Z", "2027-04-05T00:15:30Z", "2027-04-05 00:15:00Z",
                          "2027-04-05T00:15:00", "2027-04-05T00:15:00+00:00", "2027-04-05T24:00:00Z",
                          "2027-4-05T00:15:00Z"):
                self.check(csv(f"HG-0001,{stamp},1"), ["SUB-03"])

        def test_timestamp_inside_the_final_window_only(self):
            for stamp in ("2027-04-04T23:45:00Z", "2027-04-12T00:00:00Z", "2026-12-14T00:00:00Z",
                          "2027-04-31T00:00:00Z"):
                self.check(csv(f"HG-0001,{stamp},1"), ["SUB-03"])
            self.check(csv("HG-0001,2027-04-05T00:00:00Z,1", "HG-0001,2027-04-11T23:45:00Z,1"), [])

        def test_forecast_has_at_most_two_decimals(self):
            self.check(csv("HG-0001,2027-04-05T00:00:00Z,12.34"), [])
            for value in ("12.345", "0.125"):
                self.check(csv(f"HG-0001,2027-04-05T00:00:00Z,{value}"), ["SUB-04"])

        def test_forecast_form_and_range(self):
            for value in ("-1", "+5", "1e3", "1.", ".5", "1 000", "1,5", "", "2500.01", "2501", "abc"):
                row = f"HG-0001,2027-04-05T00:00:00Z,{value}"
                codes = ["SUB-08"] if "," in value else ["SUB-04"]
                self.check(csv(row), codes)
            self.check(csv("HG-0001,2027-04-05T00:00:00Z,2500.00"), [])

        def test_duplicates(self):
            self.check(csv("HG-0001,2027-04-05T00:00:00Z,1", "HG-0001,2027-04-05T00:00:00Z,2"), ["SUB-05"])
            self.check(csv("HG-0001,2027-04-05T00:00:00Z,1", "HG-0002,2027-04-05T00:00:00Z,1"), [])

        def test_duplicates_among_rows_with_bad_fields(self):
            self.check(csv("hg-1,2027-04-05T00:00:00Z,1", "hg-1,2027-04-05T00:00:00Z,2"), ["SUB-02", "SUB-05"])
            self.check(csv("HG-0001,2027-04-05T00:10:00Z,1", "HG-0001,2027-04-05T00:10:00Z,1"), ["SUB-03", "SUB-05"])
            self.check(csv("HG-0001,2027-04-05T00:00:00Z,-1", "HG-0001,2027-04-05T00:00:00Z,x"), ["SUB-04", "SUB-05"])

        def test_row_shape(self):
            self.check(csv("HG-0001,2027-04-05T00:00:00Z"), ["SUB-08"])
            self.check(csv("HG-0001,2027-04-05T00:00:00Z,1,extra"), ["SUB-08"])
            self.check(csv("HG-0001,2027-04-05T00:00:00Z,1", "", "HG-0002,2027-04-05T00:00:00Z,1"), ["SUB-08"])
            self.check(csv("HG-0001,2027-04-05T00:00:00Z", "HG-0001,2027-04-05T00:00:00Z,1"), ["SUB-08"])

        def test_size(self):
            self.check(csv(*slots(5000)), [])
            self.check(csv(*slots(5001)), ["SUB-06"])
            self.check(csv(*slots(5000), "HG-0001,2027-04-05T00:00:00Z,1"), ["SUB-05", "SUB-06"])

        def test_several_codes_each_once_in_order(self):
            self.check(csv("HG-0001,2027-04-05T00:00:00Z,12.345", "hg-1,2027-04-05T00:00:00Z,1",
                           "HG-0002,2027-04-05T00:05:00Z,1", "HG-0001,2027-04-05T00:00:00Z,7", "short",
                           "HG-0003,2027-04-05T00:00:00Z,99999"),
                       ["SUB-02", "SUB-03", "SUB-04", "SUB-05", "SUB-08"])


    if __name__ == "__main__":
        unittest.main()
    ''')
HIDDEN_TEST_COUNT = HIDDEN_TESTS.count("    def test_")


def contest_files(reference: bool = False) -> dict[str, str]:
    """`{contest-relative path: text}` of the contest (no document: that is material only).

    Args:
        reference: Test-only: `validate.py` is the reference solution instead of the stub.
    """
    return {"validate.py": REFERENCE_VALIDATE if reference else VALIDATE_STUB,
            "samples/submission-example.csv": SAMPLE, "tests/__init__.py": "", "tests/test_validate.py": SMOKE_TEST,
            "README.md": README}


def hidden_tests() -> dict[str, str]:
    """The hidden test file of the case, `{file name: text}`."""
    return {HIDDEN_FILE: HIDDEN_TESTS}


# --------------------------------------------------------------------------------------------------------------------
# The prompt
# --------------------------------------------------------------------------------------------------------------------

PASTED_LEAD = (f"Here is the rulebook of the {CHALLENGE}, its official rules and the submission API reference:\n\n"
               "<rulebook>\n{material:" + MATERIAL + "}\n</rulebook>")
SOURCES_LEAD = (f"The rulebook of the {CHALLENGE}, its official rules and the submission API reference, is in this "
                f"contest's sources: `.house/sources/{MATERIAL}`.")


def prompt_body() -> str:
    """The task text both arms share, after the paragraph that says where the rulebook is."""
    questions = "\n".join(f"Q{f['q']}. {f['question']}" for f in FACTS)
    return (
        "I need two things from you. Please do them yourself here in this session and edit the code in this repo "
        "directly on main: no tasks, branches or sub-agents needed.\n\n"
        "1. Implement `validate.py` to the rulebook's submission file format for the final phase. It must define "
        "`validate(text: str) -> list[str]`: `text` is the whole content of a submission file, and the function "
        "returns the sorted list of the distinct codes of the submission-file rules (as the rulebook names them) "
        "that the file breaks, or `[]` for a valid file. Follow the rulebook exactly, including what it says about "
        "which codes are reported together. Standard library only. I have acceptance tests for it that I will run "
        "afterwards; the smoke test in tests/ must pass too.\n\n"
        "2. Answer these ten questions from the rulebook:\n" + questions + "\n\n"
        "End your last message with the ten answers, one per line, exactly in the form `Q1: <answer>` up to "
        "`Q10: <answer>`. Give the answer only: a number as plain digits, a date as YYYY-MM-DD, and a code, a "
        "header or an address as the rulebook writes it. Put nothing else on those lines.")


def prompt(arm: str) -> str:
    """The first turn of an arm: `pasted` (the rulebook in the prompt, as `{material:rules.md}`) or `sources`."""
    lead = {"pasted": PASTED_LEAD, "sources": SOURCES_LEAD}[arm]
    return lead + "\n\n" + prompt_body()
