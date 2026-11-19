"""Content of the `chain_big` fixture (K5 `long-chain`): a synthetic `ledger` payments package, its logs and data,
the hidden tests of the twelve sub-tasks (and of decisions D-007 and D-009) and their reference solution.

Everything is generated from a fixed seed (`SEED`), so two builds are byte-identical; nothing large is committed to
the house repo. `fixtures.chain_big` writes `contest_files()` into the contest and `hidden_tests()` under
`<tmp>/hidden/chain/`.

Rules an agent should know:
- The planted facts occur once and are found only by correlating several places: `ROOT_CAUSE_CODE` in one status
  report of the settlement log (reached from the customer the human names), and `LEGACY_HOLD_MINUTES`, which is
  written nowhere (production's interface in `profiles.py`, its calendar in `bankfile.py`, the lag in `bankdays.py`);
  `tests/test_eval_long_chain.py` checks it.
- `contest_files(reference=True)` applies `REFERENCE`, the repo-authored reference solution of every sub-task (one
  exact text replacement each, checked to apply once). It exists for the unit tests that prove each hidden test fails
  on the untouched fixture and passes on the reference; a case never uses it.
- The filler code (records, validators, code tables, registries) is plain stdlib Python that compiles and imports;
  it makes the modules as long as real ones (300-600 lines), so the needed information has to be found by reading.
"""
import functools
import json
import random
import re
import subprocess
import sys
import tempfile
import textwrap
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 20261118
ROOT_CAUSE_CODE = "E2E-0917"  # the bank's reason code, in one line of the settlement log (sub-task 1)
LEGACY_HOLD_MINUTES = 5040  # the hold production really used, in one line of ledger/bankfile.py (sub-task 3)
CONTRACT_HOLD_MINUTES = 2880
HIDDEN_FILES = {  # predicate label -> (hidden test file, number of tests), in the order of the sub-tasks
    "s1-tests": ("test_s1_refs.py", 6),
    "s2-tests": ("test_s2_import_v2.py", 7),
    "s3-tests": ("test_s3_settings.py", 5),
    "s4-tests": ("test_s4_reconcile.py", 5),
    "s5-tests": ("test_s5_fx.py", 5),
    "s6-tests": ("test_s6_console.py", 4),
    "s7-tests": ("test_s7_limits.py", 5),
    "s8-tests": ("test_s8_accounts.py", 5),
    "s9-tests": ("test_s9_statement.py", 4),
    "d009-refunds-apart": ("test_d009_refunds_apart.py", 3),
    "s10-tests": ("test_s10_release.py", 5),
    "s11-tests": ("test_s11_flex.py", 6),
    "d007-integers": ("test_d007_integers.py", 4),
    "s12-tests": ("test_s12_speed.py", 3),
}
_FORBIDDEN_IN_FILLER = (ROOT_CAUSE_CODE, "0917", str(LEGACY_HOLD_MINUTES), "fastcsv")


def _d(text: str) -> str:
    """Dedent a source block and strip the leading newline."""
    return textwrap.dedent(text).lstrip("\n")


# --------------------------------------------------------------------------------------------------------------------
# Filler: realistic, importable helper code generated per module
# --------------------------------------------------------------------------------------------------------------------

_FIELDS = [("merchant_id", "str"), ("amount", "int"), ("currency", "str"), ("status", "str"),
           ("created_at", "str"), ("reference", "str"), ("channel", "str"), ("region", "str"),
           ("batch_id", "str"), ("note", "str"), ("account", "str"), ("operator", "str"), ("attempts", "int")]
_PHRASES = ["rejected by the clearing house", "awaiting merchant confirmation", "returned by the bank",
            "held for manual review", "submitted twice by the client", "account closed at the receiving bank",
            "daily limit exceeded", "currency not supported on this account", "cut-off time missed",
            "technical error at the bank, safe to retry", "beneficiary name does not match", "blocked by compliance",
            "waiting for the next business day", "partially processed", "cancelled by the operator",
            "expired before execution", "settled outside the batch", "missing remittance information",
            "routed to the fallback channel", "accepted with a warning"]
_PATTERNS = [(r"^[A-Z]{2}\d{4}$", "upper", "AB1234", "AB12"), (r"^[A-Z]{3}-\d{3}$", "upper", "KRK-104", "KRK104"),
             (r"^\d{2}-\d{3}$", "strip", "00-950", "00950"), (r"^[A-Z0-9]{8}$", "upper", "PKOPPLPW", "PKO"),
             (r"^[a-z][a-z0-9_]{2,31}$", "lower", "ops_team", "9ops"), (r"^[A-Z]{2}\d{2}[A-Z0-9]{4}$", "upper",
                                                                        "PL61ABCD", "PL6")]


def _camel(noun: str) -> str:
    return "".join(part.capitalize() for part in noun.split("_"))


class _Filler:
    """Generates top-level helper blocks for one module until it reaches a target length.

    Templates are dedented first and filled afterwards (`_fill`), so inserted multi-line text keeps its own indent.
    """

    def __init__(self, rng: random.Random, uses_money: bool):
        self.rng = rng
        self.uses_money = uses_money
        self.validators: list[tuple[str, str, str]] = []  # (function, valid, invalid) for the generated tests
        self.records: dict[str, list[tuple[str, str]]] = {}

    def _fields(self, noun: str) -> list[tuple[str, str]]:
        if noun not in self.records:
            extra = self.rng.sample([f for f in _FIELDS if f[0] != "amount"], self.rng.randint(3, 5))
            self.records[noun] = [(f"{noun}_id", "str"), ("amount", "int"), *extra]
        return self.records[noun]

    def code_table(self, noun: str) -> str:
        rng = self.rng
        prefix = "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXZ") for _ in range(2))
        numbers = rng.sample(range(10, 99), rng.randint(6, 11))
        entries = "\n".join(f'    "{prefix}{n:02d}": "{rng.choice(_PHRASES)}",' for n in sorted(numbers))
        return _fill(_d('''
            _@NAME@_CODES = {
            @ENTRIES@
            }


            def describe_@noun@_code(code: str) -> str:
                """Return the operator-facing text of a @words@ status code.

                Unknown codes come back as ``"unknown @noun@ code <code>"``, so a log line stays readable even when
                the bank introduces a new code before we add it here.
                """
                key = (code or "").strip().upper()
                return _@NAME@_CODES.get(key, f"unknown @noun@ code {code}")


            def is_final_@noun@_code(code: str) -> bool:
                """Tell whether a @words@ code ends the processing (no retry is scheduled)."""
                text = describe_@noun@_code(code)
                return not any(word in text for word in ("retry", "waiting", "awaiting", "partially"))
            '''), noun, ENTRIES=entries)

    def record(self, noun: str) -> str:
        fields = self._fields(noun)
        decl = "\n".join(f"    {f}: {t}" for f, t in fields)
        to_row = ", ".join(f'"{f}": self.{f}' for f, _ in fields)
        from_row = "\n".join(f'            {f}=int(row["{f}"]),' if t == "int" else f'            {f}=str(row["{f}"]),'
                             for f, t in fields)
        return _fill(_d('''
            @dataclass(frozen=True)
            class @Cls@Record:
                """One @words@ as stored in the ``@noun@s`` table; ``amount`` is in grosze."""

            @DECL@

                def to_row(self) -> dict:
                    """Return the record as a plain dict, the shape the export writers expect."""
                    return {@TOROW@}

                @classmethod
                def from_row(cls, row: dict) -> "@Cls@Record":
                    """Build a record from a dict made by :meth:`to_row` (values are coerced to their types)."""
                    return cls(
            @FROMROW@
                    )
            '''), noun, DECL=decl, TOROW=to_row, FROMROW=from_row)

    def validator(self, noun: str) -> str:
        pattern, mode, valid, invalid = self.rng.choice(_PATTERNS)
        norm = {"upper": ".strip().upper()", "lower": ".strip().lower()", "strip": ".strip()"}[mode]
        self.validators.append((f"validate_{noun}_code", valid, invalid))
        return _fill(_d('''
            _@NAME@_CODE_RE = re.compile(r"@PATTERN@")


            def validate_@noun@_code(value: str) -> str:
                """Check a @words@ code and return it normalised.

                Raises:
                    ValidationError: The value does not have the expected shape.
                """
                text = (value or "")@NORM@
                if not _@NAME@_CODE_RE.match(text):
                    raise ValidationError(f"invalid @noun@ code: {value!r}")
                return text
            '''), noun, PATTERN=pattern, NORM=norm)

    def aggregates(self, noun: str) -> str:
        fields = self._fields(noun)
        key = self.rng.choice([f for f, t in fields if t == "str" and f != f"{noun}_id"] or [f"{noun}_id"])
        return _fill(_d('''
            def total_@noun@_amount(items: Iterable[@Cls@Record]) -> int:
                """Sum the ``amount`` of the records, in grosze (an empty input gives 0)."""
                return sum(item.amount for item in items)


            def group_@noun@s_by_@KEY@(items: Iterable[@Cls@Record]) -> dict[str, list[@Cls@Record]]:
                """Group records by ``@KEY@``, keeping the input order inside every group."""
                groups: dict[str, list[@Cls@Record]] = {}
                for item in items:
                    groups.setdefault(item.@KEY@, []).append(item)
                return groups


            def largest_@noun@s(items: Iterable[@Cls@Record], limit: int = 10) -> list[@Cls@Record]:
                """The ``limit`` records with the largest amount, largest first (ties keep the input order)."""
                ordered = sorted(enumerate(items), key=lambda pair: (-pair[1].amount, pair[0]))
                return [item for _, item in ordered[:limit]]
            '''), noun, KEY=key)

    def formatter(self, noun: str) -> str:
        shown = [f for f, t in self._fields(noun) if t == "str"][:3]
        cols = " ".join(f"{{item.{f}:<14}}" for f in shown)
        amount = "format_amount(item.amount)" if self.uses_money else "_grosze_text(item.amount)"
        return _fill(_d('''
            def format_@noun@_line(item: @Cls@Record) -> str:
                """One line for the operator console: @SHOWN@ and the amount in złoty."""
                return f"@COLS@ {@AMOUNT@:>14}"


            def format_@noun@_table(items: Iterable[@Cls@Record]) -> str:
                """The console lines of several records, one per line, under a header line."""
                header = "@HEADER@"
                return "\\n".join([header, *(format_@noun@_line(item) for item in items)])
            '''), noun, SHOWN=", ".join(shown), COLS=cols, AMOUNT=amount,
            HEADER="  ".join(f.upper() for f in shown))

    def registry(self, noun: str) -> str:
        has_status = "status" in dict(self._fields(noun))
        extra = _d('''

                def open_items(self) -> list[@Cls@Record]:
                    """The records whose ``status`` is not final (``open`` or ``pending``)."""
                    return [item for item in self if item.status in ("open", "pending")]
            ''') if has_status else ""
        return _fill(_d('''
            class @Cls@Registry:
                """In-memory registry of @words@s keyed by id; used by the batch jobs and the tests."""

                def __init__(self, items: Iterable[@Cls@Record] = ()) -> None:
                    self._items: dict[str, @Cls@Record] = {}
                    for item in items:
                        self.add(item)

                def add(self, item: @Cls@Record) -> None:
                    """Add a record, replacing an older one with the same id."""
                    self._items[item.@noun@_id] = item

                def get(self, key: str) -> @Cls@Record:
                    """Return the record with id ``key``; raises ``KeyError`` when there is none."""
                    return self._items[key]

                def remove(self, key: str) -> None:
                    """Forget a record; removing an unknown id is not an error."""
                    self._items.pop(key, None)

                def __len__(self) -> int:
                    return len(self._items)

                def __iter__(self) -> Iterator[@Cls@Record]:
                    return iter(sorted(self._items.values(), key=lambda item: item.@noun@_id))
            ''') + ("\n" + _indent(extra, 4) if extra else ""), noun)

    def blocks_for(self, noun: str) -> list[str]:
        """All blocks of one noun: the record first (the others use it), the rest shuffled."""
        rest = [self.aggregates, self.formatter, self.registry, self.code_table, self.validator]
        self.rng.shuffle(rest)
        return [kind(noun) for kind in [self.record, *rest]]


def _indent(text: str, n: int) -> str:
    """Indent every non-blank line by `n` spaces."""
    return "".join((" " * n + line) if line.strip() else line for line in text.splitlines(keepends=True))


def _fill(template: str, noun: str, **values: str) -> str:
    """Replace `@noun@`, `@NAME@`, `@Cls@`, `@words@` and the given `@KEY@` placeholders in a dedented template."""
    values = {"noun": noun, "NAME": noun.upper(), "Cls": _camel(noun), "words": noun.replace("_", " "), **values}
    for key, value in values.items():
        template = template.replace(f"@{key}@", value)
    rest = re.sub(r"@(dataclass|classmethod|property)\b", "", template)
    leftover = re.search(r"@[A-Za-z]+@", rest)
    if leftover:
        raise AssertionError(f"unfilled placeholder {leftover.group(0)}")
    return template


_GROSZE_TEXT = _d('''
    def _grosze_text(grosze: int) -> str:
        """``1234`` -> ``"12.34"`` (local copy, so this module does not import ``money``)."""
        sign = "-" if grosze < 0 else ""
        whole, part = divmod(abs(grosze), 100)
        return f"{sign}{whole}.{part:02d}"
    ''')
_FILLER_IMPORTS = ["import re", "from collections.abc import Iterable, Iterator", "from dataclasses import dataclass"]


_SPARE_NOUNS = ["work_item", "export_batch", "status_note", "sync_job", "review_case", "hold_note", "cost_line",
                "channel_rule", "retry_plan", "archive_entry", "alert_rule", "quota_note", "access_note", "data_fix"]
MODULE_LINES = 560  # target length of every module but api_v1 (stable and smaller) and __init__


def _assemble(name: str, doc: str, imports: list[str], core: str, nouns: list[str], target: int,
              rng: random.Random, generated: dict) -> str:
    """One module: docstring, imports, the hand-written core, then filler blocks until about `target` lines."""
    uses_money = name not in ("money", "errors")
    filler = _Filler(rng, uses_money)
    std = sorted({i for i in imports + _FILLER_IMPORTS if not i.startswith("from .")},
                 key=lambda i: (i.startswith("from "), i))
    wanted = [i for i in imports if i.startswith("from .")]
    if name != "errors":
        wanted.append("from .errors import ValidationError")
    if uses_money:
        wanted.append("from .money import format_amount")
    names: dict[str, set[str]] = {}
    for line in wanted:  # one `from .x import a, b` line per module
        module, _, items = line[len("from "):].partition(" import ")
        names.setdefault(module, set()).update(item.strip() for item in items.split(","))
    local = [f"from {module} import {', '.join(sorted(items))}" for module, items in sorted(names.items())]
    head = f'"""{doc.strip()}"""\n' + "\n".join(std) + "\n\n" + ("\n".join(local) + "\n" if local else "")
    parts = [core.rstrip() + "\n"]
    if not uses_money:
        parts.append(_GROSZE_TEXT)
    lines = (head + "\n\n".join(parts)).count("\n")
    spare = [n for n in _SPARE_NOUNS if n not in nouns]
    rng.shuffle(spare)
    queue = [block for noun in nouns for block in filler.blocks_for(noun)]
    while lines < target:
        if not queue:
            queue = filler.blocks_for(spare.pop())
        block = queue.pop(0)
        if any(bad in block for bad in _FORBIDDEN_IN_FILLER):
            continue
        if lines + block.count("\n") + 2 > target + 25:
            continue
        parts.append(block)
        lines += block.count("\n") + 2
    text = "".join(parts)
    emitted = set(re.findall(r"^def (validate_\w+_code)\(", text, re.MULTILINE))
    generated[name] = [v for v in filler.validators if v[0] in emitted]
    classes = set(re.findall(r"^class (\w+)(?:Record|Registry):", text, re.MULTILINE))
    tables = set(re.findall(r"^def describe_(\w+)_code\(", text, re.MULTILINE))
    generated.setdefault("_records", {})[name] = [
        (noun, fields, f"class {_camel(noun)}Registry:" in text, noun in tables, f"def total_{noun}_amount(" in text)
        for noun, fields in filler.records.items() if _camel(noun) in classes and f"class {_camel(noun)}Record:" in text]
    return head + "\n\n" + "\n\n".join(p.rstrip() + "\n" for p in parts)


# --------------------------------------------------------------------------------------------------------------------
# Hand-written cores (the code the sub-tasks are about)
# --------------------------------------------------------------------------------------------------------------------

ERRORS = _d('''
    class LedgerError(Exception):
        """Base class of every error the ledger package raises on purpose."""


    class ValidationError(LedgerError):
        """Input that does not have the expected shape (an amount, a code, an IBAN, a date)."""


    class StatementError(LedgerError):
        """A bank statement file that cannot be imported (format, control record, currency)."""


    class SettlementError(LedgerError):
        """A settlement batch that cannot be built or sent."""


    class ConfigError(LedgerError):
        """Unknown environment or a setting with a value of the wrong type."""


    class FeeError(LedgerError):
        """Unknown fee plan or a fee that cannot be computed."""


    class ReconcileError(LedgerError):
        """The reconciliation inputs are inconsistent (for example two entries with one id)."""
    ''')

MONEY = _d('''
    GROSZE_PER_PLN = 100
    _DECIMAL_RE = re.compile(r"\\d+(\\.\\d{1,2})?")


    def parse_amount(text: str) -> int:
        """Parse a decimal amount such as ``"12.34"``, ``"12,34"``, ``"-0,5"`` or ``"7"`` into grosze.

        A comma and a dot are both accepted as the decimal separator; at most two decimals. Thousands separators
        are not accepted (``"1 234,56"`` is an error): callers that read such text must remove them first.

        Raises:
            ValidationError: The text is not a decimal amount.
        """
        raw = (text or "").strip()
        sign = -1 if raw.startswith("-") else 1
        raw = raw.lstrip("+-").replace(",", ".")
        if not _DECIMAL_RE.fullmatch(raw):
            raise ValidationError(f"not an amount: {text!r}")
        whole, _, frac = raw.partition(".")
        return sign * (int(whole) * GROSZE_PER_PLN + int((frac + "00")[:2]))


    def format_amount(grosze: int) -> str:
        """``123456`` -> ``"1234.56"``, ``-5`` -> ``"-0.05"`` (dot, no thousands separator)."""
        sign = "-" if grosze < 0 else ""
        whole, part = divmod(abs(grosze), GROSZE_PER_PLN)
        return f"{sign}{whole}.{part:02d}"


    def format_amount_pl(grosze: int) -> str:
        """Polish notation for letters and statements: ``123456`` -> ``"1 234,56"`` (space as thousands separator)."""
        sign = "-" if grosze < 0 else ""
        whole, part = divmod(abs(grosze), GROSZE_PER_PLN)
        return f"{sign}{whole:,}".replace(",", " ") + f",{part:02d}"


    def round_half_up(numerator: int, denominator: int) -> int:
        """Divide and round half away from zero.

        ``round_half_up(5, 2) == 3``, ``round_half_up(-5, 2) == -3``, ``round_half_up(4, 3) == 1``.
        Every fee and every conversion in the ledger rounds through this function.

        Raises:
            ValueError: ``denominator`` is not positive.
        """
        if denominator <= 0:
            raise ValueError("denominator must be positive")
        quotient, remainder = divmod(abs(numerator), denominator)
        if 2 * remainder >= denominator:
            quotient += 1
        return quotient if numerator >= 0 else -quotient


    def percent_of(amount: int, bps: int) -> int:
        """``bps`` basis points (1 bp = 0.01 %) of ``amount`` grosze, rounded half up to the grosz."""
        return round_half_up(amount * bps, 10_000)


    def split_evenly(amount: int, parts: int) -> list[int]:
        """Split ``amount`` into ``parts`` integers that differ by at most one grosz and sum to ``amount``.

        The first ``amount % parts`` shares get the extra grosz: ``split_evenly(100, 3) == [34, 33, 33]``.
        """
        if parts <= 0:
            raise ValueError("parts must be positive")
        base, extra = divmod(amount, parts)
        return [base + (1 if i < extra else 0) for i in range(parts)]
    ''')

_CONFIG_EXTRA = [("notification_retry_limit", 3), ("notification_batch_size", 200), ("audit_retention_days", 400),
                 ("fx_rate_max_age_minutes", 90), ("limits_daily_payout_count", 40), ("refund_window_days", 60),
                 ("chargeback_window_days", 120), ("report_page_size", 500), ("api_timeout_seconds", 15),
                 ("sftp_connect_timeout_seconds", 20), ("sftp_retry_attempts", 5), ("sftp_retry_delay_seconds", 30),
                 ("db_pool_size", 12), ("db_slow_query_ms", 1500), ("iban_check_enabled", True),
                 ("statement_archive_days", 730), ("merchant_cache_seconds", 300), ("export_chunk_rows", 2000)]
_CONFIG_DOCS = {"notification_retry_limit": "how many times a failed merchant e-mail is retried",
                "notification_batch_size": "e-mails sent per worker cycle",
                "audit_retention_days": "how long audit rows are kept",
                "fx_rate_max_age_minutes": "older rate tables are refused",
                "limits_daily_payout_count": "payouts per merchant and day before the velocity check trips",
                "refund_window_days": "refunds accepted up to this many days after the payment",
                "chargeback_window_days": "chargebacks accepted up to this many days after the payment",
                "report_page_size": "rows per page of the operator reports",
                "api_timeout_seconds": "timeout of calls to partner APIs",
                "sftp_connect_timeout_seconds": "bank SFTP connect timeout",
                "sftp_retry_attempts": "upload attempts before a batch is parked",
                "sftp_retry_delay_seconds": "pause between upload attempts",
                "db_pool_size": "database connections per worker", "db_slow_query_ms": "queries above are logged",
                "iban_check_enabled": "verify the IBAN checksum of new merchants",
                "statement_archive_days": "how long imported statement files are kept",
                "merchant_cache_seconds": "cache lifetime of merchant records",
                "export_chunk_rows": "rows per chunk of the CSV exports"}


def _config_core() -> str:
    base = [("environment", '"development"', "name of the profile that was loaded"),
            ("settlement_cutoff_utc", '"01:00"', "nightly settlement starts at this UTC time"),
            ("settlement_batch_max_payouts", "500", "payouts per bank file"),
            ("payout_min_amount", "1000", "smaller balances wait for the next run (grosze)"),
            ("reserve_hold_minutes", "1440", "how long a payout is held before it may be released"),
            ("e2e_prefix", '"SETL"', "first part of every end-to-end reference"),
            ("bank_file_encoding", '"utf-8"', "encoding of the payout files we send"),
            ("bank_file_line_ending", '"\\n"', "line ending of the payout files"),
            ("legacy_bank_mode", "False", "talk to the bank through the old clearing interface"),
            ("bank_interface", '"api"', "which bank interface the payout files go through"),
            ("reconcile_date_tolerance_days", "2", "booking dates may differ by this many days"),
            ("statement_currency", '"PLN"', "the only currency on our collection accounts"),
            ("fee_default_plan", '"standard"', "plan of merchants without an explicit plan")]
    lines = []
    for key, value, doc in base:
        lines.append(f'    "{key}": {value},  # {doc}')
    for key, value in _CONFIG_EXTRA:
        lines.append(f'    "{key}": {value!r},  # {_CONFIG_DOCS[key]}')
    keys = "\n".join(lines) + "\n"
    return _d('''
        DEFAULTS: dict = {
        ''') + keys + _d('''
        }
        """The defaults of every setting. Profiles (``profiles.py``) override some of them per environment and
        ``settings.load`` combines everything; never read ``DEFAULTS`` directly in business code."""


        def default(key: str):
            """The default value of one setting.

            Raises:
                ConfigError: The key is not a known setting.
            """
            try:
                return DEFAULTS[key]
            except KeyError:
                raise ConfigError(f"unknown setting {key!r}") from None


        def known_keys() -> list[str]:
            """All setting names, sorted."""
            return sorted(DEFAULTS)


        def coerce(key: str, text: str):
            """Turn the text of an environment variable into the type of the setting's default.

            Booleans accept ``1/0``, ``true/false``, ``yes/no`` (any case).

            Raises:
                ConfigError: Unknown key, or text that is not a value of the right type.
            """
            current = default(key)
            if isinstance(current, bool):
                lowered = text.strip().lower()
                if lowered in ("1", "true", "yes"):
                    return True
                if lowered in ("0", "false", "no"):
                    return False
                raise ConfigError(f"{key}: not a boolean: {text!r}")
            if isinstance(current, int):
                try:
                    return int(text.strip())
                except ValueError:
                    raise ConfigError(f"{key}: not an integer: {text!r}") from None
            return text
        ''')


PROFILES = _d('''
    PROFILES: dict[str, dict] = {
        "development": {
            "environment": "development",
            "settlement_batch_max_payouts": 50,
            "sftp_retry_attempts": 1,
            "db_pool_size": 2,
        },
        "sandbox": {
            "environment": "sandbox",
            "reserve_hold_minutes": 60,  # partners test the whole flow within an hour
            "payout_min_amount": 100,
            "notification_batch_size": 20,
        },
        "preprod": {
            "environment": "preprod",
            "legacy_bank_mode": True,
            "bank_interface": "sorbnet",  # the bank's test gateway only offers SORBNET
            "db_pool_size": 8,
        },
        "staging": {
            "environment": "staging",
            "settlement_batch_max_payouts": 200,
            "db_slow_query_ms": 800,
        },
        "production": {
            "environment": "production",
            "legacy_bank_mode": True,  # the bank moves us to the new interface in Q1 2027
            "bank_interface": "elixir",
            "reserve_hold_minutes": 2880,  # 48 hours, merchant contract v3
            "bank_file_encoding": "utf-8",
            "settlement_batch_max_payouts": 500,
            "db_pool_size": 24,
            "sftp_retry_attempts": 5,
        },
    }
    """Per-environment overrides of ``config.DEFAULTS``. ``settings.load`` applies them after the defaults."""


    def profile_names() -> list[str]:
        """The environments we have a profile for, sorted."""
        return sorted(PROFILES)


    def profile(name: str) -> dict:
        """A copy of one profile's overrides.

        Raises:
            ConfigError: There is no such environment.
        """
        try:
            return dict(PROFILES[name])
        except KeyError:
            raise ConfigError(f"unknown environment {name!r}; known: {', '.join(profile_names())}") from None
    ''')

SETTINGS = _d('''
    ENV_PREFIX = "LEDGER_"


    def _from_environment(env: Mapping[str, str]) -> dict:
        """Settings given as ``LEDGER_<KEY>`` environment variables (unknown keys are ignored)."""
        out = {}
        for name, text in env.items():
            if not name.startswith(ENV_PREFIX):
                continue
            key = name[len(ENV_PREFIX):].lower()
            if key in config.DEFAULTS:
                out[key] = config.coerce(key, text)
        return out


    def load(environment: str, env: Mapping[str, str] | None = None) -> dict:
        """The effective settings of an environment.

        Layers, later ones win:
        1. ``config.DEFAULTS``;
        2. the environment's profile (``profiles.PROFILES``);
        3. while ``legacy_bank_mode`` is on: ``bankfile.legacy_overrides``, the values the old clearing
           interface needs;
        4. ``LEDGER_<KEY>`` environment variables (``env``, default ``os.environ``).

        Raises:
            ConfigError: Unknown environment or a malformed environment variable.
        """
        merged = dict(config.DEFAULTS)
        merged.update(profiles.profile(environment))
        if merged.get("legacy_bank_mode"):
            merged.update(bankfile.legacy_overrides(merged))
        merged.update(_from_environment(os.environ if env is None else env))
        return merged


    def describe(environment: str) -> str:
        """A short text of the effective settings, one ``key = value`` per line, sorted by key."""
        values = load(environment, env={})
        return "\\n".join(f"{key} = {values[key]!r}" for key in sorted(values))


    def differences(left: str, right: str) -> dict[str, tuple]:
        """Settings whose effective values differ between two environments: ``{key: (left, right)}``."""
        a, b = load(left, env={}), load(right, env={})
        return {key: (a.get(key), b.get(key)) for key in sorted(set(a) | set(b)) if a.get(key) != b.get(key)}
    ''')

BANKFILE = _d('''
    LEGACY_BANK_OVERRIDES: dict = {
        "bank_file_encoding": "cp1250",  # the old interface rejects UTF-8
        "bank_file_line_ending": "\\r\\n",
        "settlement_cutoff_utc": "00:30",  # files must reach the old interface before 01:00
        "settlement_batch_max_payouts": 25,  # the old interface refuses bigger files
    }
    """Values the old clearing interface needs; ``legacy_overrides`` adds the payout hold of its calendar."""

    _INTERFACE_CALENDARS = {"api": "standard", "elixir": "legacy-elixir", "sorbnet": "legacy-sorbnet"}


    def legacy_overrides(values: dict) -> dict:
        """What the old clearing interface needs, for settings ``values`` that have ``legacy_bank_mode`` on.

        ``LEGACY_BANK_OVERRIDES`` plus the payout hold: a payout must not be released before the bank has settled
        the funds, so the hold follows the clearing calendar of the interface (``bankdays.CLEARING_CALENDARS``).
        """
        calendar = _INTERFACE_CALENDARS[values["bank_interface"]]
        return {**LEGACY_BANK_OVERRIDES, "reserve_hold_minutes": bankdays.clearing_minutes(calendar)}


    HEADER = "BATCH;PAYOUT;MERCHANT;IBAN;AMOUNT;E2E"


    @dataclass(frozen=True)
    class BankFile:
        """A rendered payout file: its name and the bytes to upload."""

        name: str
        content: bytes


    def control_sum(amounts: Iterable[int]) -> int:
        """The control sum the bank checks: the sum of all amounts in grosze."""
        return sum(amounts)


    def render_batch(batch, ibans: dict[str, str], settings: dict) -> BankFile:
        """Render a settlement batch as the bank's semicolon file.

        Args:
            batch: A ``settlement.Batch``.
            ibans: Merchant id -> IBAN.
            settings: Effective settings (``settings.load``): encoding and line ending of the file.

        Raises:
            SettlementError: A merchant has no IBAN.
        """
        rows = [HEADER]
        for line in batch.lines:
            iban = ibans.get(line.merchant_id)
            if not iban:
                raise SettlementError(f"merchant {line.merchant_id} has no IBAN")
            rows.append(";".join([batch.batch_id, line.payout_id, line.merchant_id, iban,
                                  format_amount(line.amount), line.e2e_reference]))
        rows.append(f"#CONTROL;{len(batch.lines)};{format_amount(control_sum(l.amount for l in batch.lines))}")
        ending = settings["bank_file_line_ending"]
        text = ending.join(rows) + ending
        return BankFile(f"{batch.batch_id}.csv", text.encode(settings["bank_file_encoding"]))
    ''')

REFS = _d('''
    E2E_MAX_LENGTH = 35  # SEPA limit of EndToEndId
    _SEPA_TEXT_RE = re.compile(r"^[A-Za-z0-9/\\-?:().,'+ ]*$")


    def batch_id(day: date, number: int) -> str:
        """The id of the ``number``-th settlement batch of a day: ``B20261112-07``."""
        return f"B{day:%Y%m%d}-{number:02d}"


    def payout_id(day: date, number: int) -> str:
        """The id of a payout: ``P`` + date + six-digit running number, e.g. ``P20261112-000412``."""
        return f"P{day:%Y%m%d}-{number:06d}"


    def e2e_reference(merchant_id: str, batch: str, seq: int) -> str:
        """The end-to-end reference of a payout, which the bank returns on the statement.

        Format ``SETL/<merchant id>/<batch id>/<seq>``, ``seq`` being the payout's number within the merchant's
        payouts in that batch (four digits), e.g. ``SETL/M10417/B20261112-07/0012``. The reconciliation matches
        statement lines on it, so the format of existing references must not change. SEPA allows at most 35
        characters, so the reference is cut to that length.
        """
        reference = f"SETL/{merchant_id}/{batch}/{seq:04d}"
        return reference[:E2E_MAX_LENGTH]


    def refund_reference(merchant_id: str, number: int) -> str:
        """The reference of a refund: ``RFND/<merchant id>/<six digits>``."""
        return f"RFND/{merchant_id}/{number:06d}"


    def is_sepa_text(text: str) -> bool:
        """Tell whether ``text`` uses only the SEPA character set (letters, digits, ``/-?:().,'+`` and space)."""
        return bool(_SEPA_TEXT_RE.match(text))


    def parse_e2e_reference(reference: str) -> tuple[str, str, int] | None:
        """Split ``SETL/<merchant>/<batch>/<seq>`` into its parts; None for anything else."""
        parts = reference.strip().split("/")
        if len(parts) != 4 or parts[0] != "SETL" or not parts[3].isdigit():
            return None
        return parts[1], parts[2], int(parts[3])
    ''')

SETTLEMENT = _d('''
    @dataclass(frozen=True)
    class BatchLine:
        """One payout inside a batch, with the reference the bank will report back."""

        payout_id: str
        merchant_id: str
        amount: int
        e2e_reference: str


    @dataclass(frozen=True)
    class Batch:
        """A settlement batch: what goes into one bank file."""

        batch_id: str
        lines: tuple[BatchLine, ...]

        @property
        def control_sum(self) -> int:
            """Sum of the line amounts in grosze."""
            return sum(line.amount for line in self.lines)


    def build_batch(batch: str, items: Iterable[Payout]) -> Batch:
        """Turn payouts into a batch; each merchant's payouts are numbered 1, 2, ... in input order.

        Raises:
            SettlementError: A payout has a non-positive amount or the batch is empty.
        """
        counters: dict[str, int] = {}
        lines = []
        for payout in items:
            if payout.amount <= 0:
                raise SettlementError(f"payout {payout.payout_id}: amount must be positive")
            counters[payout.merchant_id] = counters.get(payout.merchant_id, 0) + 1
            ref = refs.e2e_reference(payout.merchant_id, batch, counters[payout.merchant_id])
            lines.append(BatchLine(payout.payout_id, payout.merchant_id, payout.amount, ref))
        if not lines:
            raise SettlementError(f"batch {batch} is empty")
        return Batch(batch, tuple(lines))


    def split_into_batches(day: date, items: list[Payout], max_payouts: int) -> list[Batch]:
        """Split a day's payouts into batches of at most ``max_payouts``; marketplace sub-merchants (ids starting
        with ``MP``) get batches of their own, after the ordinary merchants."""
        ordinary = [p for p in items if not p.merchant_id.startswith("MP")]
        marketplace = [p for p in items if p.merchant_id.startswith("MP")]
        batches, number = [], 0
        for group in (ordinary, marketplace):
            for start in range(0, len(group), max_payouts):
                number += 1
                batches.append(build_batch(refs.batch_id(day, number), group[start:start + max_payouts]))
        return batches


    def validate_batch(batch: Batch) -> None:
        """Check what the bank checks before it accepts a file: positive amounts, reference length and charset.

        Raises:
            SettlementError: The first problem found.
        """
        for line in batch.lines:
            if line.amount <= 0:
                raise SettlementError(f"{line.payout_id}: non-positive amount")
            if len(line.e2e_reference) > refs.E2E_MAX_LENGTH:
                raise SettlementError(f"{line.payout_id}: reference longer than {refs.E2E_MAX_LENGTH}")
            if not refs.is_sepa_text(line.e2e_reference):
                raise SettlementError(f"{line.payout_id}: reference has characters outside the SEPA set")
    ''')

PAYOUTS = _d('''
    @dataclass(frozen=True)
    class Payout:
        """Money we owe a merchant, created when its balance is settled."""

        payout_id: str
        merchant_id: str
        amount: int
        created_at: datetime


    def release_at(created_at: datetime, settings: dict) -> datetime:
        """When a payout may be released to the bank: ``reserve_hold_minutes`` after it was created."""
        return created_at + timedelta(minutes=settings["reserve_hold_minutes"])


    def releasable(items: Iterable[Payout], now: datetime, settings: dict) -> list[Payout]:
        """The payouts whose hold is over at ``now`` and whose amount reaches ``payout_min_amount``."""
        minimum = settings["payout_min_amount"]
        return [p for p in items if release_at(p.created_at, settings) <= now and p.amount >= minimum]


    def hold_hours(settings: dict) -> float:
        """The hold period in hours, for messages to merchants."""
        return settings["reserve_hold_minutes"] / 60
    ''')

STATEMENTS = _d('''
    @dataclass(frozen=True)
    class StatementLine:
        """One line of a bank statement of our collection account.

        ``amount`` is signed grosze: money in is positive, money out (refunds, bank fees) negative.
        """

        booking_date: date
        value_date: date
        amount: int
        currency: str
        counterparty: str
        iban: str
        title: str
        reference: str
        txn_id: str


    def bank_reference(text: str) -> str:
        """A statement reference in the form our references are compared in: no surrounding spaces, upper case."""
        return text.strip().upper()


    def normalise_iban(text: str) -> str:
        """``"pl61 1090 1014"`` -> ``"PL6110901014"``: no spaces, upper case."""
        return "".join(text.split()).upper()


    def total(lines: Iterable[StatementLine]) -> int:
        """Sum of the signed amounts, in grosze."""
        return sum(line.amount for line in lines)


    def by_day(lines: Iterable[StatementLine]) -> dict[date, list[StatementLine]]:
        """Group lines by booking date, keeping the file order inside a day."""
        out: dict[date, list[StatementLine]] = {}
        for line in lines:
            out.setdefault(line.booking_date, []).append(line)
        return out


    def dedupe(lines: Iterable[StatementLine]) -> list[StatementLine]:
        """Drop lines whose ``txn_id`` was seen before (the bank repeats a line when a statement is re-sent)."""
        seen: set[str] = set()
        out = []
        for line in lines:
            if line.txn_id not in seen:
                seen.add(line.txn_id)
                out.append(line)
        return out
    ''')

IMPORTER = _d('''
    log = logging.getLogger(__name__)
    V1_COLUMNS = ["booking_date", "value_date", "amount_grosze", "currency", "counterparty", "iban", "title",
                  "reference", "txn_id"]


    def _v1_line(row: dict, number: int) -> StatementLine:
        try:
            return StatementLine(
                booking_date=date.fromisoformat(row["booking_date"]),
                value_date=date.fromisoformat(row["value_date"]),
                amount=int(row["amount_grosze"]),
                currency=row["currency"].strip().upper(),
                counterparty=row["counterparty"].strip(),
                iban=normalise_iban(row["iban"]),
                title=row["title"].strip(),
                reference=row["reference"].strip(),
                txn_id=row["txn_id"].strip(),
            )
        except (KeyError, ValueError) as exc:
            raise StatementError(f"line {number}: {exc}") from None


    def import_statement(path) -> list[StatementLine]:
        """Read a statement in the bank's format v1 (comma separated, ISO dates, amounts already in grosze).

        The first line is the header ``V1_COLUMNS``. Lines are returned in file order, repeated lines
        (same ``txn_id``) dropped.

        Raises:
            StatementError: Wrong header or a malformed line.
        """
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != V1_COLUMNS:
                raise StatementError(f"{path}: not a v1 statement (header {reader.fieldnames})")
            lines = [_v1_line(row, n) for n, row in enumerate(reader, start=2)]
        kept = dedupe(lines)
        if len(kept) < len(lines):
            log.warning("%s: dropped %d repeated statement line(s)", path, len(lines) - len(kept))
        return kept


    def statement_summary(lines: list[StatementLine]) -> dict:
        """Counts and sums of a statement for the import log: lines, credits, debits, net (grosze)."""
        credits = sum(line.amount for line in lines if line.amount > 0)
        debits = sum(line.amount for line in lines if line.amount < 0)
        return {"lines": len(lines), "credits": credits, "debits": debits, "net": credits + debits}
    ''')

JOURNAL = _d('''
    JOURNAL_COLUMNS = ["entry_id", "merchant_id", "reference", "amount", "direction", "booked_on", "kind"]


    @dataclass(frozen=True)
    class LedgerEntry:
        """A posting of our journal that the bank should confirm on the statement.

        ``amount`` is always positive; ``direction`` says which way the money went: ``C`` money came in (a
        customer payment), ``D`` money went out (a refund, a payout).
        """

        entry_id: str
        merchant_id: str
        reference: str
        amount: int
        direction: str
        booked_on: date
        kind: str = "payment"

        @property
        def signed_amount(self) -> int:
            """The amount as it shows on the bank statement: positive for ``C``, negative for ``D``."""
            return self.amount if self.direction == "C" else -self.amount


    def load_journal(path) -> list[LedgerEntry]:
        """Read the journal export (CSV with ``JOURNAL_COLUMNS``).

        Raises:
            ReconcileError: Wrong header, a bad value or a repeated ``entry_id``.
        """
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != JOURNAL_COLUMNS:
                raise ReconcileError(f"{path}: unexpected header {reader.fieldnames}")
            out, seen = [], set()
            for number, row in enumerate(reader, start=2):
                try:
                    entry = LedgerEntry(row["entry_id"], row["merchant_id"], row["reference"], int(row["amount"]),
                                        row["direction"], date.fromisoformat(row["booked_on"]), row["kind"])
                except ValueError as exc:
                    raise ReconcileError(f"{path}:{number}: {exc}") from None
                if entry.direction not in ("C", "D") or entry.amount <= 0:
                    raise ReconcileError(f"{path}:{number}: bad direction or amount")
                if entry.entry_id in seen:
                    raise ReconcileError(f"{path}:{number}: entry {entry.entry_id} repeated")
                seen.add(entry.entry_id)
                out.append(entry)
        return out


    def balance(entries: Iterable[LedgerEntry]) -> int:
        """Net signed amount of the entries, in grosze."""
        return sum(entry.signed_amount for entry in entries)
    ''')

MATCHING_RULES = _d('''
    DATE_TOLERANCE_DAYS = 2  # the bank books a payment up to two days after we do


    def normalise_reference(text: str) -> str:
        """Our references are compared without surrounding spaces and case-insensitively (the statement side goes
        through ``statements.bank_reference``)."""
        return text.strip().upper()


    def same_reference(entry: LedgerEntry, line: StatementLine) -> bool:
        """The statement line carries the entry's reference."""
        return normalise_reference(entry.reference) == bank_reference(line.reference)


    def same_amount(entry: LedgerEntry, line: StatementLine) -> bool:
        """The statement line is for the entry's amount."""
        return entry.amount == line.amount


    def close_dates(entry: LedgerEntry, line: StatementLine) -> bool:
        """The bank booked the line at most ``DATE_TOLERANCE_DAYS`` days from the entry's date (either side)."""
        return abs((line.booking_date - entry.booked_on).days) <= DATE_TOLERANCE_DAYS


    DEFAULT_RULES = (same_reference, same_amount, close_dates)
    """Every rule must hold for a line to match an entry; ``reconcile.match_lines`` applies them in this order."""
    ''')

RECONCILE = _d('''
    @dataclass
    class MatchResult:
        """What ``match_lines`` found.

        ``matched`` pairs are in statement order; ``unmatched_lines`` in statement order; ``unmatched_entries`` in
        journal order.
        """

        matched: list[tuple[LedgerEntry, StatementLine]] = field(default_factory=list)
        unmatched_lines: list[StatementLine] = field(default_factory=list)
        unmatched_entries: list[LedgerEntry] = field(default_factory=list)


    log = logging.getLogger(__name__)


    def match_lines(entries: list[LedgerEntry], lines: list[StatementLine], rules=DEFAULT_RULES) -> MatchResult:
        """Pair statement lines with journal entries.

        Lines are taken in statement order; each is matched with the first entry (in journal order) that is not
        matched yet and satisfies every rule. An entry matches at most one line.
        """
        remaining = list(entries)
        result = MatchResult()
        for line in lines:
            for entry in remaining:
                if all(rule(entry, line) for rule in rules):
                    result.matched.append((entry, line))
                    remaining.remove(entry)
                    break
            else:
                result.unmatched_lines.append(line)
        result.unmatched_entries = remaining
        _warn_unmatched(result)
        return result


    def _warn_unmatched(result: MatchResult) -> None:
        """One warning per unmatched line and entry, the lines the nightly job's log is made of."""
        for line in result.unmatched_lines:
            log.warning("unmatched bank line %s ref=%s amount=%s booked=%s", line.txn_id, line.reference, line.amount,
                        line.booking_date)
        for entry in result.unmatched_entries:
            log.warning("unmatched entry %s ref=%s kind=%s direction=%s amount=%s booked_on=%s", entry.entry_id,
                        entry.reference, entry.kind, entry.direction, entry.amount, entry.booked_on)


    def reconcile_files(statement_path, journal_path) -> MatchResult:
        """Match a v1 statement file against a journal export (the nightly ``reconcile`` job)."""
        return match_lines(journal.load_journal(journal_path), importer.import_statement(statement_path))


    def summary(result: MatchResult) -> dict:
        """Counts for the job log: matched, unmatched lines and unmatched entries."""
        return {"matched": len(result.matched), "unmatched_lines": len(result.unmatched_lines),
                "unmatched_entries": len(result.unmatched_entries)}
    ''')

FEES = _d('''
    @dataclass(frozen=True)
    class FeePlan:
        """A merchant fee plan: ``percent_bps`` basis points of the amount plus ``fixed`` grosze, never less than
        ``minimum`` grosze; the fee is rounded half up to the grosz."""

        name: str
        percent_bps: int
        fixed: int
        minimum: int


    PLANS: dict[str, FeePlan] = {
        "standard": FeePlan("standard", percent_bps=190, fixed=30, minimum=50),
        "volume": FeePlan("volume", percent_bps=140, fixed=20, minimum=30),
        "nonprofit": FeePlan("nonprofit", percent_bps=90, fixed=0, minimum=0),
    }


    def plan(name: str) -> FeePlan:
        """The plan called ``name``.

        Raises:
            FeeError: No such plan.
        """
        try:
            return PLANS[name]
        except KeyError:
            raise FeeError(f"unknown fee plan {name!r}") from None


    def quote(amount: int, plan_name: str) -> int:
        """The fee in grosze for a payment of ``amount`` grosze on a plan.

        Raises:
            FeeError: Unknown plan or a negative amount.
        """
        if amount < 0:
            raise FeeError("amount must not be negative")
        p = plan(plan_name)
        return max(percent_of(amount, p.percent_bps) + p.fixed, p.minimum)


    def net_amount(amount: int, plan_name: str) -> int:
        """What the merchant receives for a payment: amount minus the fee, in grosze."""
        return amount - quote(amount, plan_name)
    ''')

API_V1 = _d('''
    API_VERSION = "1.4"


    def quote_fee(amount: int, merchant_id: str) -> int:
        """The fee in grosze a merchant pays for a payment of ``amount`` grosze (on the merchant's plan)."""
        return fees.quote(amount, merchants.plan_for(merchant_id))


    def net_amount(amount: int, merchant_id: str) -> int:
        """What the merchant receives for a payment of ``amount`` grosze, after the fee."""
        return fees.net_amount(amount, merchants.plan_for(merchant_id))


    def merchant_plan(merchant_id: str) -> str:
        """The name of the merchant's fee plan."""
        return merchants.plan_for(merchant_id)


    def list_plans() -> list[str]:
        """Names of the fee plans partners can see, sorted."""
        return sorted(fees.PLANS)


    def get_merchant(merchant_id: str) -> dict:
        """Public data of a merchant: id, name and plan.

        Raises:
            KeyError: Unknown merchant.
        """
        m = merchants.get(merchant_id)
        return {"merchant_id": m.merchant_id, "name": m.name, "plan": m.plan}


    def payout_release_time(created_at: str, environment: str = "production") -> str:
        """When a payout created at ``created_at`` (ISO 8601) is released, as ISO 8601."""
        created = datetime.fromisoformat(created_at)
        return payouts.release_at(created, settings.load(environment)).isoformat()


    def format_fee(amount: int, merchant_id: str) -> str:
        """The fee for a payment as text in złoty, e.g. ``"2.20"``."""
        return format_amount(quote_fee(amount, merchant_id))


    def reference_parts(reference: str) -> dict | None:
        """Merchant, batch and sequence number of an end-to-end reference; None when it is not one of ours."""
        parts = refs.parse_e2e_reference(reference)
        if parts is None:
            return None
        return {"merchant_id": parts[0], "batch_id": parts[1], "seq": parts[2]}
    ''')

API_V2 = _d('''
    API_VERSION = "2.0-beta"


    def merchant_summary(merchant_id: str) -> dict:
        """Merchant data for the partner dashboard: id, name, plan and city."""
        m = merchants.get(merchant_id)
        return {"merchant_id": m.merchant_id, "name": m.name, "plan": m.plan, "city": m.city}


    def fee_breakdown(amount: int, merchant_id: str) -> dict:
        """The fee of a payment split into the percentage part and the fixed part (grosze); the total respects
        the plan's minimum."""
        p = fees.plan(merchants.plan_for(merchant_id))
        percent = percent_of(amount, p.percent_bps)
        return {"plan": p.name, "percent_part": percent, "fixed_part": p.fixed,
                "total": fees.quote(amount, p.name)}
    ''')


def _merchants_core(rng: random.Random) -> str:
    rows = []
    cities = ["Warszawa", "Kraków", "Gdańsk", "Poznań", "Wrocław", "Łódź", "Lublin", "Szczecin", "Rzeszów", "Toruń"]
    shops = ["Hurtownia Owoców", "Księgarnia", "Apteka", "Piekarnia", "Sklep Rowerowy", "Kwiaciarnia", "Drogeria",
             "Pracownia Ceramiki", "Sklep Zoologiczny", "Salon Optyczny", "Antykwariat", "Delikatesy", "Kawiarnia",
             "Szkoła Językowa", "Warsztat Samochodowy", "Studio Jogi"]
    surnames = ["Kowalski", "Nowak", "Wiśniewska", "Wójcik", "Kamińska", "Lewandowski", "Zielińska", "Szymański",
                "Woźniak", "Dąbrowska", "Kozłowski", "Jankowska", "Mazur", "Krawczyk", "Piotrowska", "Grabowski"]
    ids = sorted(rng.sample(range(10_000, 29_999), 58)) + [30117, 30118, 30119]
    for n in ids:
        plan = rng.choice(["standard"] * 5 + ["volume"] * 2 + ["nonprofit"])
        name = f"{rng.choice(shops)} {rng.choice(surnames)}"
        rows.append(f'    Merchant("M{n:05d}", "{name}", "{plan}", "{rng.choice(cities)}"),')
    for sub in (317, 318, 342, 355, 401, 433):
        rows.append(f'    Merchant("MP0042-S{sub:05d}", "Marketplace Bazarek seller {sub}", "volume", "Warszawa"),')
    table = "\n".join(rows) + "\n"
    return _d('''
        @dataclass(frozen=True)
        class Merchant:
            """A merchant we collect payments for. ``plan`` is a key of ``fees.PLANS``."""

            merchant_id: str
            name: str
            plan: str
            city: str


        MERCHANTS: list[Merchant] = [
        ''') + table + _d('''
        ]
        """Merchant master data (synced nightly from the CRM). Marketplace sub-merchants have ids ``MPnnnn-Snnnnn``."""

        _BY_ID = {m.merchant_id: m for m in MERCHANTS}


        def get(merchant_id: str) -> Merchant:
            """The merchant with this id; raises ``KeyError`` for an unknown one."""
            return _BY_ID[merchant_id]


        def plan_for(merchant_id: str) -> str:
            """The fee plan of a merchant (``config`` ``fee_default_plan`` for a merchant not in the table)."""
            m = _BY_ID.get(merchant_id)
            return m.plan if m else config.DEFAULTS["fee_default_plan"]


        def is_marketplace(merchant_id: str) -> bool:
            """Marketplace sub-merchants have ids starting with ``MP``."""
            return merchant_id.startswith("MP")


        def by_plan(plan: str) -> list[Merchant]:
            """Merchants on a plan, in table order."""
            return [m for m in MERCHANTS if m.plan == plan]
        ''')


SMALL_CORES = {
    "reports": _d('''
        def daily_totals(lines: Iterable[StatementLine]) -> dict[str, int]:
            """Net amount per booking day (ISO date -> grosze), days in ascending order."""
            out: dict[str, int] = {}
            for line in sorted(lines, key=lambda l: l.booking_date):
                key = line.booking_date.isoformat()
                out[key] = out.get(key, 0) + line.amount
            return out


        def render_totals(totals: dict[str, int]) -> str:
            """The daily totals as text, one ``YYYY-MM-DD  amount`` line per day."""
            return "\\n".join(f"{day}  {format_amount(amount):>14}" for day, amount in totals.items())
        '''),
    "fx": _d('''
        RATE_SCALE = 10_000  # a rate is stored as PLN per unit of the currency times 10 000


        @dataclass(frozen=True)
        class Rate:
            """An exchange rate from the daily table: ``scaled`` = PLN per unit times ``RATE_SCALE``."""

            currency: str
            scaled: int
            table: str


        def parse_rate(text: str) -> int:
            """A rate as the NBP table prints it (``"4,2731"``: PLN per unit, decimal comma, four decimals), scaled by
            ``RATE_SCALE``."""
            return int(float(text.strip().replace(",", ".")) * RATE_SCALE)


        def load_rates(path) -> dict[tuple[date, str], Rate]:
            """Read the daily NBP tables (``date;table;currency;rate`` per line after a header), keyed by
            ``(date, currency)``."""
            out = {}
            with open(path, encoding="utf-8") as f:
                next(f)
                for line in f:
                    day, table, currency, rate = line.rstrip("\\n").split(";")
                    out[(date.fromisoformat(day), currency)] = Rate(currency, parse_rate(rate), table)
            return out


        def rate_for(rates: dict, day: date, currency: str) -> Rate:
            """The rate of the last table published on or before ``day`` (there is no table on weekends and holidays).

            Raises:
                KeyError: No table for the currency in the 10 days up to ``day``.
            """
            for back in range(0, 11):
                found = rates.get((day - timedelta(days=back), currency))
                if found:
                    return found
            raise KeyError(f"no {currency} rate on or before {day}")


        def to_pln(amount_minor: int, rate: Rate) -> int:
            """Convert minor units of a currency (cents) to grosze, rounded half up."""
            return round_half_up(amount_minor * rate.scaled, RATE_SCALE)
        '''),
    "bankdays": _d('''
        HOLIDAYS_2026 = {date(2026, 1, 1), date(2026, 1, 6), date(2026, 4, 5), date(2026, 4, 6), date(2026, 5, 1),
                         date(2026, 5, 3), date(2026, 5, 24), date(2026, 6, 4), date(2026, 8, 15), date(2026, 11, 1),
                         date(2026, 11, 11), date(2026, 12, 24), date(2026, 12, 25), date(2026, 12, 26)}


        def is_business_day(day: date) -> bool:
            """Monday to Friday and not a Polish bank holiday."""
            return day.weekday() < 5 and day not in HOLIDAYS_2026


        CLEARING_CALENDARS = {
            "standard": {"days": 1, "hours": 0},
            "express": {"days": 0, "hours": 4},
            "legacy-sorbnet": {"days": 2, "hours": 6},
            "legacy-elixir": {"days": 3, "hours": 12},
        }
        """How long after a payout file the bank has settled the money, per clearing calendar."""


        def clearing_minutes(calendar: str) -> int:
            """The settlement lag of a clearing calendar, in minutes."""
            lag = CLEARING_CALENDARS[calendar]
            return (lag["days"] * 24 + lag["hours"]) * 60


        def next_business_day(day: date) -> date:
            """The first business day strictly after ``day``."""
            day += timedelta(days=1)
            while not is_business_day(day):
                day += timedelta(days=1)
            return day
        '''),
    "iban": _d('''
        def iban_checksum_ok(iban: str) -> bool:
            """The ISO 13616 mod-97 check of an IBAN (spaces ignored)."""
            text = "".join(iban.split()).upper()
            if len(text) < 15 or not text[:2].isalpha():
                return False
            moved = text[4:] + text[:4]
            digits = "".join(str(int(ch, 36)) for ch in moved)
            return int(digits) % 97 == 1


        def bank_code(iban: str) -> str:
            """The 8-digit bank and branch number of a Polish IBAN."""
            text = "".join(iban.split()).upper()
            return text[4:12]
        '''),
    "audit": _d('''
        @dataclass(frozen=True)
        class AuditEvent:
            """Who did what to which object, for the audit trail."""

            at: datetime
            actor: str
            action: str
            target: str


        def audit_line(event: AuditEvent) -> str:
            """One line of the audit log: ISO time, actor, action, target."""
            return f"{event.at.isoformat()} {event.actor} {event.action} {event.target}"
        '''),
    "notifications": _d('''
        PAYOUT_SENT = "Wypłata {amount} zł została wysłana na rachunek {iban}."
        PAYOUT_HELD = "Wypłata {amount} zł zostanie zwolniona {when}."


        def payout_sent_text(amount: int, iban: str) -> str:
            """The e-mail text telling a merchant a payout was sent."""
            return PAYOUT_SENT.format(amount=format_amount(amount), iban=iban[-4:].rjust(len(iban), "*"))
        '''),
    "limits": _d('''
        def limit_key(merchant_id: str) -> str:
            """The velocity counter a payout counts against: the account the money leaves under, i.e. the first six
            characters of the merchant id (``M10417``; a marketplace's sellers ``MP0042-S00317`` count as ``MP0042``)."""
            return merchant_id[:6]


        def daily_counts(items: Iterable[Payout]) -> dict[tuple[str, date], int]:
            """Payouts per velocity counter and day (the date of ``created_at``)."""
            counts: dict[tuple[str, date], int] = {}
            for p in items:
                key = (limit_key(p.merchant_id), p.created_at.date())
                counts[key] = counts.get(key, 0) + 1
            return counts


        def held_by_velocity(items: Iterable[Payout], settings: dict) -> list[Payout]:
            """The payouts the daily velocity limit holds (``limits_daily_payout_count``), in input order: the first
            ``limit`` payouts of a counter on a day pass, every later one is held."""
            limit = settings["limits_daily_payout_count"]
            seen: dict[tuple[str, date], int] = {}
            held = []
            for p in items:
                key = (limit_key(p.merchant_id), p.created_at.date())
                seen[key] = seen.get(key, 0) + 1
                if seen[key] > limit:
                    held.append(p)
            return held


        def over_daily_count(count_today: int, settings: dict) -> bool:
            """True when a counter already had ``limits_daily_payout_count`` payouts today."""
            return count_today >= settings["limits_daily_payout_count"]
        '''),
    "cards": _d('''
        @dataclass(frozen=True)
        class CardTransaction:
            """A card payment from the acquirer's settlement file; ``amount_minor`` in the card currency's minor units."""

            tx_id: str
            merchant_id: str
            day: date
            currency: str
            amount_minor: int


        def book(tx: CardTransaction, rates: dict) -> int:
            """The amount in grosze we book for a card payment: converted at the NBP rate of its day."""
            if tx.currency == "PLN":
                return tx.amount_minor
            return fx.to_pln(tx.amount_minor, fx.rate_for(rates, tx.day, tx.currency))


        def load_settlement_file(path) -> tuple[list[CardTransaction], dict[str, int]]:
            """The acquirer's settlement file (``tx_id;merchant_id;day;currency;amount_minor;pln`` after a header): the
            transactions and what the acquirer paid for each, ``{tx_id: grosze}``."""
            txs, paid = [], {}
            with open(path, encoding="utf-8") as f:
                next(f)
                for line in f:
                    tx_id, merchant_id, day, currency, amount, pln = line.rstrip("\\n").split(";")
                    txs.append(CardTransaction(tx_id, merchant_id, date.fromisoformat(day), currency, int(amount)))
                    paid[tx_id] = int(pln)
            return txs, paid


        def mismatches(txs: Iterable[CardTransaction], paid: dict[str, int], rates: dict) -> list[tuple[str, int, int]]:
            """``(tx_id, acquirer, booked)`` for every payment the acquirer paid differently from what we book."""
            out = []
            for tx in txs:
                booked = book(tx, rates)
                if paid[tx.tx_id] != booked:
                    out.append((tx.tx_id, paid[tx.tx_id], booked))
            return out
        '''),
    "refunds": _d('''
        def refund_allowed(paid_on: date, today: date, settings: dict) -> bool:
            """A refund is accepted up to ``refund_window_days`` days after the payment."""
            return (today - paid_on).days <= settings["refund_window_days"]
        '''),
}

# module name -> (docstring, imports, nouns for the filler, target lines)
_MODULES = {
    "errors": ("Exceptions of the ledger package and the error-code tables shown to operators.", [],
               ["ledger_error", "bank_error", "import_error", "api_error"], 300),
    "money": ("Money helpers: parsing, formatting and rounding of amounts in grosze (1 PLN = 100 grosze).",
              ["from .errors import ValidationError"], ["cash_count", "rounding_case", "coin_roll"], 330),
    "config": ("Default settings of the ledger services (see ``settings.load`` for the effective values).",
               ["from .errors import ConfigError"], ["config_change", "feature_flag", "secret_ref"], 360),
    "profiles": ("Environment profiles: the settings each environment overrides.",
                 ["from .errors import ConfigError"], ["deployment", "environment_note", "host_role"], 320),
    "settings": ("Effective settings: defaults, profile, legacy bank values and environment variables.",
                 ["import os", "from collections.abc import Mapping", "from . import bankfile, config, profiles"],
                 ["setting_override", "setting_audit", "env_var"], 340),
    "bankfile": ("Payout files for the bank (semicolon CSV) and the values of the old clearing interface.",
                 ["from . import bankdays", "from .errors import SettlementError"],
                 ["upload", "bank_ack", "sftp_session"], 380),
    "refs": ("Identifiers and references: batch ids, payout ids, end-to-end and refund references.",
             ["from datetime import date"], ["reference_audit", "id_block", "ref_alias"], 330),
    "settlement": ("Nightly settlement: payouts grouped into batches, one bank file per batch.",
                   ["from datetime import date", "from . import refs", "from .errors import SettlementError",
                    "from .payouts import Payout"], ["batch_run", "batch_note", "retry_slot"], 450),
    "payouts": ("Payouts to merchants and the hold period before they are released.",
                ["from datetime import datetime, timedelta"], ["payout_hold", "payout_request", "balance_snapshot"],
                380),
    "statements": ("Bank statement lines of our collection accounts.", ["from datetime import date"],
                   ["statement_file", "bank_account", "statement_gap"], 360),
    "importer": ("Import of bank statement files into ``StatementLine`` objects (format v1).",
                 ["import csv", "import logging", "from datetime import date", "from .errors import StatementError",
                  "from .statements import StatementLine, dedupe, normalise_iban"],
                 ["import_job", "import_warning", "file_fingerprint"], 520),
    "journal": ("The ledger journal: postings that bank statements confirm.",
                ["import csv", "from datetime import date", "from .errors import ReconcileError"],
                ["posting_batch", "journal_lock", "account_map"], 420),
    "matching_rules": ("Rules deciding whether a statement line confirms a journal entry.",
                       ["from .journal import LedgerEntry", "from .statements import StatementLine, bank_reference"],
                       ["rule_stat", "match_hint", "manual_match"], 330),
    "reconcile": ("Nightly reconciliation of bank statements against the journal.",
                  ["import logging", "from dataclasses import field", "from . import importer, journal",
                   "from .journal import LedgerEntry", "from .matching_rules import DEFAULT_RULES",
                   "from .statements import StatementLine"], ["reconcile_run", "exception_case", "match_review"],
                  480),
    "fees": ("Merchant fee plans and fee quotes (in grosze, rounded half up).",
             ["from .errors import FeeError", "from .money import percent_of"],
             ["fee_change", "fee_invoice", "promo_code"], 420),
    "merchants": ("Merchant master data and their fee plans.", ["from . import config"],
                  ["merchant_contact", "merchant_note", "kyc_check"], 430),
    "api_v1": ("Public API v1 used by partners (stable).",
               ["from datetime import datetime", "from . import fees, merchants, payouts, refs, settings"],
               ["partner_request", "api_error_case"], 300),
    "api_v2": ("Public API v2 (beta): new partner endpoints go here.",
               ["from . import fees, merchants", "from .money import percent_of"],
               ["partner_key", "webhook", "sandbox_token"], 340),
    "reports": ("Operator reports over statements and the journal.",
                ["from .statements import StatementLine"], ["report_job", "report_column", "export_file"], 400),
    "fx": ("Foreign exchange: rate tables and conversions to grosze.",
           ["from datetime import date, timedelta", "from .money import round_half_up"],
           ["rate_table", "fx_quote", "fx_margin"], 340),
    "bankdays": ("Business days and bank holidays in Poland.", ["from datetime import date, timedelta"],
                 ["cutoff_window", "holiday_note", "bank_session"], 320),
    "iban": ("IBAN helpers: checksum and bank codes.", [], ["iban_change", "bank_branch", "account_check"], 330),
    "audit": ("Audit trail of operator actions.", ["from datetime import datetime"],
              ["audit_entry", "operator_session", "access_grant"], 340),
    "notifications": ("Merchant notifications (e-mail texts in Polish).", [],
                      ["mail_job", "mail_template", "bounce"], 330),
    "limits": ("Velocity limits on payouts.", ["from datetime import date", "from .payouts import Payout"],
               ["limit_rule", "limit_breach", "limit_override"], 320),
    "cards": ("Card payments: foreign card payments booked in PLN and the check against the acquirer.",
              ["from datetime import date", "from . import fx"], ["card_batch", "dispute", "terminal"], 560),
    "refunds": ("Refund rules.", ["from datetime import date"], ["refund_request", "refund_batch", "refund_reason"],
                330),
}
_CORES = {"errors": ERRORS, "money": MONEY, "profiles": PROFILES, "settings": SETTINGS, "bankfile": BANKFILE,
          "refs": REFS, "settlement": SETTLEMENT, "payouts": PAYOUTS, "statements": STATEMENTS, "importer": IMPORTER,
          "journal": JOURNAL, "matching_rules": MATCHING_RULES, "reconcile": RECONCILE, "fees": FEES,
          "api_v1": API_V1, "api_v2": API_V2, **SMALL_CORES}


# --------------------------------------------------------------------------------------------------------------------
# Logs
# --------------------------------------------------------------------------------------------------------------------

def _rint(rng: random.Random, low: int, high: int) -> int:
    """`rng.randint`, but never `LEGACY_HOLD_MINUTES` (the planted value must stay unique in the contest)."""
    while True:
        value = rng.randint(low, high)
        if value != LEGACY_HOLD_MINUTES:
            return value


class _Log:
    """A log with a running clock: `add(level, logger, message)` appends one line `<ISO time>Z LEVEL logger msg`."""

    def __init__(self, rng: random.Random, start: datetime):
        self.rng, self.now, self.lines = rng, start, []

    def add(self, level: str, logger: str, message: str, gap_ms: tuple[int, int] = (2, 180)) -> None:
        self.now += timedelta(milliseconds=_rint(self.rng, *gap_ms))
        stamp = self.now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{self.now.microsecond // 1000:03d}Z"
        self.lines.append(f"{stamp} {level:<5} {logger:<20} {message}")

    def noise(self) -> None:
        rng = self.rng
        kind = rng.random()
        if kind < 0.55:
            self.add("DEBUG", "metrics", f"queue_depth={_rint(rng, 0, 40)} rss_mb={_rint(rng, 310, 380)} "
                     f"open_fds={_rint(rng, 40, 90)}")
        elif kind < 0.8:
            self.add("DEBUG", "db.pool", f"checkout conn={_rint(rng, 1, 24)} wait_ms={_rint(rng, 0, 12)}")
        elif kind < 0.95:
            self.add("INFO", "health", "heartbeat ok")
        else:
            self.add("WARN", "db.pool", f"slow query {_rint(rng, 1501, 2900)}ms: SELECT balance FROM merchant_balances "
                     f"WHERE merchant_id = $1 (rows=1)")


def _payout_plan(rng: random.Random, merchant_ids: list[str]) -> list[tuple[str, int]]:
    """The payouts of the night of 2026-11-12: (merchant id, amount), ordinary merchants first."""
    ordinary = [m for m in merchant_ids if not m.startswith("MP")]
    marketplace = [m for m in merchant_ids if m.startswith("MP")]
    items = [(rng.choice(ordinary), _rint(rng, 1_500, 2_400_000)) for _ in range(521)]
    for m in marketplace:
        items += [(m, _rint(rng, 1_200, 89_000)) for _ in range(_rint(rng, 13, 16))]
    return items


def settlement_log(rng: random.Random, merchant_ids: list[str]) -> str:
    """The production log of the settlement run of 2026-11-12 (sub-task 1); about 3 000 lines.

    The bank answers every uploaded file with a status report that names only the bank's file id (`PAYF-...`) and its
    own report id (`SR-...`), never a merchant or a batch. Several reports reject something, each with its own reason
    code (yesterday's re-sent batches, a partly rejected batch, a late file that is accepted after a re-upload). The
    batches of the marketplace sub-merchants (`MP0042-S...`, the customer the human names) are rejected for
    `ROOT_CAUSE_CODE`, which occurs in exactly one line; finding it means following merchant -> payout lines (whose
    references are cut to 35 characters) -> batch -> upload line (file id) -> status report.
    """
    log = _Log(rng, datetime(2026, 11, 12, 1, 0, 0))
    log.add("INFO", "settlement.run", "run=R20261112 starting nightly settlement (environment=production, "
            "legacy_bank_mode=on)")
    log.add("INFO", "settlement.run", "log level DEBUG enabled for settlement.batch since 2026-11-10 (ticket OPS-2291)")
    log.add("INFO", "settlement.run", "cutoff 00:30 UTC (legacy interface), batches of at most 25 payouts")
    for _ in range(6):
        log.noise()
    file_no = iter(range(470, 600))
    reports = iter(rng.sample(range(510_000, 590_000), 60))
    resent = {}
    log.add("INFO", "settlement.retry", "re-sending 2 batches from the retry queue of run R20261111")
    for batch in ("B20261111-03", "B20261111-17"):
        resent[batch] = f"PAYF-1111-{next(file_no):04d}"
        log.add("INFO", "bank.sftp", f"upload of {batch}.csv ok (attempt 1/5), bank file id {resent[batch]}")
    items = _payout_plan(rng, merchant_ids)
    balances = sorted({m for m, _ in items})
    log.add("INFO", "settlement.balances", f"loading balances of {len(balances)} merchants")
    for m in balances:
        log.add("DEBUG", "settlement.balances", f"merchant={m} available={_rint(rng, 10_000, 9_000_000)} "
                f"reserved={_rint(rng, 0, 400_000)}", (1, 20))
        if rng.random() < 0.1:
            log.noise()
    payout_ids = []
    log.add("INFO", "settlement.payouts", f"creating {len(items)} payouts")
    for n, (m, amount) in enumerate(items, start=1):
        pid = f"P20261112-{n:06d}"
        payout_ids.append(pid)
        log.add("DEBUG", "settlement.payouts", f"created payout {pid} merchant={m} amount={amount}", (1, 15))
        if rng.random() < 0.08:
            log.noise()
    ordinary = [(pid, m, a) for pid, (m, a) in zip(payout_ids, items) if not m.startswith("MP")]
    market = [(pid, m, a) for pid, (m, a) in zip(payout_ids, items) if m.startswith("MP")]
    batches = []
    for group in (ordinary, market):
        for start in range(0, len(group), 25):
            batches.append((f"B20261112-{len(batches) + 1:02d}", group[start:start + 25]))
    rejected = [b for b, lines in batches if lines[0][1].startswith("MP")]
    for batch, lines in batches:
        log.add("INFO", "settlement.batch", f"building batch {batch} ({len(lines)} payouts)")
        counters: dict[str, int] = {}
        for pid, m, amount in lines:
            counters[m] = counters.get(m, 0) + 1
            ref = f"SETL/{m}/{batch}/{counters[m]:04d}"[:35]
            log.add("DEBUG", "settlement.batch", f"line {pid} merchant={m} amount={amount} e2e={ref}", (1, 8))
        total = sum(a for _, _, a in lines)
        log.add("INFO", "settlement.batch", f"batch {batch} built: {len(lines)} payouts, control sum {total}")
        log.add("INFO", "settlement.batch", f"batch {batch} validated (amounts, reference length, charset)")
        log.add("INFO", "bankfile", f"wrote {batch}.csv ({len(lines) + 2} rows, cp1250, CRLF)")
        if rng.random() < 0.3:
            log.noise()
    log.add("INFO", "bank.sftp", "connecting to sftp.clearing.example-bank.pl:22 (legacy interface)")
    files = {}
    for batch, _ in batches:
        files[batch] = f"PAYF-1112-{next(file_no):04d}"
        if batch in ("B20261112-05", "B20261112-13"):
            log.add("ERROR", "bank.sftp", f"upload of {batch}.csv failed: Connection reset by peer (attempt 1/5), "
                    "retrying in 30s", (200, 900))
            log.add("INFO", "bank.sftp", f"upload of {batch}.csv ok (attempt 2/5), bank file id {files[batch]}",
                    (30_000, 31_000))
        else:
            log.add("INFO", "bank.sftp", f"upload of {batch}.csv ok (attempt 1/5), bank file id {files[batch]}",
                    (200, 900))
        log.noise()
    log.add("INFO", "settlement.run", f"{len(batches)} batches uploaded; waiting for status reports")
    for _ in range(40):
        log.noise()
    late = "B20261112-17"
    partial = "B20261112-09"
    first_rejected_report = None
    order = [("resent", b) for b in resent] + [("tonight", b) for b, _ in batches]
    rng.shuffle(order)
    for kind, batch in order:
        report = f"SR-{next(reports)}"
        for _ in range(_rint(rng, 1, 3)):
            log.add("DEBUG", "bank.ack", "polling status reports (legacy interface)", (5_000, 30_000))
        if kind == "resent":
            if batch == "B20261111-03":
                log.add("WARN", "bank.ack", f"status report {report} for {resent[batch]}: grpSts=RJCT rsn=E2E-0388 "
                        "\"Creditor account closed\"")
            else:
                log.add("WARN", "bank.ack", f"status report {report} for {resent[batch]}: grpSts=PART txs=25 "
                        "accepted=24")
                log.add("WARN", "bank.ack", f"status report {report} tx 7 RJCT rsn=E2E-0145 \"Creditor name does not "
                        "match the account\"")
            continue
        lines = dict(batches)[batch]
        file_id = files[batch]
        if batch in rejected:
            if first_rejected_report is None:
                first_rejected_report = report
                log.add("WARN", "bank.ack", f"status report {report} for {file_id}: grpSts=RJCT "
                        f"rsn={ROOT_CAUSE_CODE} \"EndToEndId not unique within file\"")
            else:
                log.add("WARN", "bank.ack", f"status report {report} for {file_id}: grpSts=RJCT rsn=see "
                        f"{first_rejected_report}")
        elif batch == partial:
            log.add("WARN", "bank.ack", f"status report {report} for {file_id}: grpSts=PART txs={len(lines)} "
                    f"accepted={len(lines) - 2}")
            for k in (4, 19):
                log.add("WARN", "bank.ack", f"status report {report} tx {k} RJCT rsn=E2E-0461 \"Amount exceeds the "
                        "creditor bank's limit\"")
        elif batch == late:
            log.add("WARN", "bank.ack", f"status report {report} for {file_id}: grpSts=RJCT rsn=E2E-0702 \"File "
                    "received after the cut-off\"")
            files[batch] = f"PAYF-1112-{next(file_no):04d}"
            log.add("INFO", "bank.sftp", f"re-upload of {batch}.csv ok (attempt 1/5), bank file id {files[batch]}",
                    (2_000, 9_000))
            log.add("INFO", "bank.ack", f"status report SR-{next(reports)} for {files[batch]}: grpSts=ACCP "
                    f"txs={len(lines)}", (20_000, 60_000))
        else:
            log.add("INFO", "bank.ack", f"status report {report} for {file_id}: grpSts=ACCP txs={len(lines)}")
        if rng.random() < 0.5:
            log.noise()
    lost = sum(len(dict(batches)[b]) for b in rejected)
    log.add("ERROR", "settlement.run", f"{len(rejected)} of {len(batches)} files rejected by the bank; {lost} "
            "payouts moved to the retry queue")
    for batch in rejected:
        for pid, m, amount in dict(batches)[batch]:
            log.add("INFO", "settlement.retry", f"queued {pid} amount={amount} for run R20261113", (1, 10))
    for batch, lines in batches:
        if batch in rejected:
            continue
        for pid, m, amount in lines:
            if rng.random() < 0.72:
                log.add("DEBUG", "notification.mail", f"queued payout-sent mail for {m} ({pid})", (1, 25))
            if rng.random() < 0.004:
                log.add("WARN", "notification.mail", f"SMTP 451 temporary failure for {m}, will retry")
    while len(log.lines) < 2995:
        log.noise()
    log.add("INFO", "settlement.run", "run=R20261112 finished with errors (exit 2)")
    return "\n".join(log.lines) + "\n"


def reconcile_log(rng: random.Random, merchant_ids: list[str]) -> str:
    """The log of the reconciliation of 2026-11-13 (sub-task 4); about 3 000 lines.

    Every refund in the journal (direction `D`) stays unmatched although the statement has a line for it: the bank
    shows refunds as negative amounts and prefixes their reference with `ZWROT/`. Three bank fees have no entry.
    """
    log = _Log(rng, datetime(2026, 11, 14, 3, 15, 0))
    ordinary = [m for m in merchant_ids if not m.startswith("MP")]
    entries = []
    for n in range(1, 1290):
        m = rng.choice(ordinary)
        if rng.random() < 0.028:
            entries.append((f"E20261113-{n:05d}", m, f"RFND/{m}/{_rint(rng, 100, 999_999):06d}",
                            _rint(rng, 900, 59_000), "D", "refund"))
        else:
            entries.append((f"E20261113-{n:05d}", m, f"PAY/{m}/{_rint(rng, 100, 999_999):06d}",
                            _rint(rng, 500, 450_000), "C", "payment"))
    log.add("INFO", "reconcile.run", "run=C20261114 reconciling account PL27 1140 2004 0000 3502 7741 9031 for "
            "2026-11-13")
    log.add("INFO", "reconcile.load", f"loaded {len(entries)} journal entries (journal export 2026-11-13)")
    log.add("INFO", "reconcile.load", f"loaded {len(entries) + 3} statement lines (statement 2026-11-13.csv, v1)")
    for _ in range(8):
        log.noise()
    order = list(range(len(entries)))
    rng.shuffle(order)
    unmatched_lines = []
    for k, i in enumerate(order, start=1):
        eid, m, ref, amount, direction, kind = entries[i]
        line_id = f"L20261113-{k:05d}"
        if direction == "D":
            unmatched_lines.append((line_id, f"ZWROT/{ref}", -amount))
            log.add("DEBUG", "reconcile.match", f"no entry for {line_id} ref=ZWROT/{ref} amount={-amount}", (1, 6))
        else:
            log.add("DEBUG", "reconcile.match", f"matched {line_id} ref={ref} amount={amount} -> {eid}", (1, 6))
        if rng.random() < 0.05:
            log.noise()
    for k, title in enumerate(["OPLATA ZA PROWADZENIE RACHUNKU", "PROWIZJA ZA PRZELEWY SEPA",
                               "OPLATA ZA WYCIAG"], start=len(order) + 1):
        line_id = f"L20261113-{k:05d}"
        amount = -rng.choice([500, 1200, 300])
        unmatched_lines.append((line_id, "", amount))
        log.add("DEBUG", "reconcile.match", f"no entry for {line_id} ref= amount={amount} title=\"{title}\"")
    for line_id, ref, amount in unmatched_lines:
        log.add("WARN", "reconcile.match", f"unmatched bank line {line_id} ref={ref} amount={amount} "
                "booked=2026-11-13")
    refunds = [e for e in entries if e[4] == "D"]
    for eid, m, ref, amount, direction, kind in refunds:
        log.add("WARN", "reconcile.match", f"unmatched entry {eid} ref={ref} kind={kind} direction={direction} "
                f"amount={amount} booked_on=2026-11-13")
    log.add("ERROR", "reconcile.run", f"finished with {len(unmatched_lines)} unmatched bank lines and {len(refunds)} "
            "unmatched entries (threshold 0); report sent to finance-ops")
    while len(log.lines) < 2996:
        log.noise()
    log.add("INFO", "reconcile.run", "run=C20261114 done (exit 1)")
    return "\n".join(log.lines) + "\n"


# --------------------------------------------------------------------------------------------------------------------
# Data files
# --------------------------------------------------------------------------------------------------------------------

_COUNTERPARTIES = ["Jan Kowalski", "Anna Nowak", "Firma Handlowa \"Pod Lipą\" Sp. z o.o.", "Piotr Wiśniewski",
                   "Katarzyna Wójcik", "Usługi IT Zieliński", "Fundacja Dobre Serce", "Marek Lewandowski",
                   "Ewa Kamińska", "Studio Graficzne; Oko", "Tomasz Szymański", "Agnieszka Woźniak"]


def _pl_amount(grosze: int, sep: str) -> str:
    """`-123456` -> `-1 234,56` with `sep` between thousands."""
    sign = "-" if grosze < 0 else ""
    whole, part = divmod(abs(grosze), 100)
    return sign + f"{whole:,}".replace(",", sep) + f",{part:02d}"


def _v2_field(text: str) -> str:
    if any(ch in text for ch in ';"'):
        return '"' + text.replace('"', '""') + '"'
    return text


def _iban(rng: random.Random) -> str:
    digits = "".join(str(_rint(rng, 0, 9)) for _ in range(26))
    return "PL" + " ".join([digits[:2]] + [digits[i:i + 4] for i in range(2, 26, 4)])


V2_HEADER = ["Data księgowania", "Data waluty", "Kwota", "Waluta", "Kontrahent", "Rachunek kontrahenta", "Tytuł",
             "Referencja", "Id transakcji"]


def sample_v2(rng: random.Random, merchant_ids: list[str]) -> tuple[str, int, int]:
    """The sample statement in format v2: `(text, rows, sum of amounts in grosze)`."""
    rows, total = [], 0
    day = date(2026, 11, 2)
    for n in range(1, 141):
        if n % 9 == 0:
            day += timedelta(days=1)
        m = rng.choice([x for x in merchant_ids if not x.startswith("MP")])
        refund = rng.random() < 0.06
        amount = -_rint(rng, 900, 59_000) if refund else _rint(rng, 500, 2_450_000)
        ref = f"RFND/{m}/{_rint(rng, 100, 999_999):06d}" if refund else (
            f"PAY/{m}/{_rint(rng, 100, 999_999):06d}" if rng.random() < 0.9 else "")
        title = f"Zamówienie {_rint(rng, 10_000, 99_999)}" + ("; dopłata" if rng.random() < 0.05 else "")
        sep = " " if rng.random() < 0.4 else " "
        rows.append(";".join(_v2_field(x) for x in [
            f"{day:%d.%m.%Y}", f"{day + timedelta(days=_rint(rng, 0, 1)):%d.%m.%Y}", _pl_amount(amount, sep), "PLN",
            rng.choice(_COUNTERPARTIES), _iban(rng), title, ref, f"2611{n:010d}"]))
        total += amount
    text = "﻿" + ";".join(V2_HEADER) + "\r\n" + "\r\n".join(rows) + "\r\n"
    text += f"#KONTROLA;{len(rows)};{_pl_amount(total, ' ')}\r\n"
    return text, len(rows), total


def month_files(rng: random.Random, merchant_ids: list[str]) -> tuple[str, str]:
    """The November journal export and v1 statement (sub-task 6): 24 000 entries and lines each, enough for the
    quadratic matcher to take about a minute."""
    ordinary = [m for m in merchant_ids if not m.startswith("MP")]
    entries, lines = [], []
    for n in range(1, 24_001):
        m = rng.choice(ordinary)
        booked = date(2026, 11, 1) + timedelta(days=_rint(rng, 0, 27))
        refund = rng.random() < 0.03
        ref = f"{'RFND' if refund else 'PAY'}/{m}/{n:06d}"
        amount = _rint(rng, 900, 59_000) if refund else _rint(rng, 500, 450_000)
        entries.append(f"E202611-{n:06d},{m},{ref},{amount},{'D' if refund else 'C'},{booked.isoformat()},"
                       f"{'refund' if refund else 'payment'}")
        bank_day = booked + timedelta(days=rng.choice([0, 0, 1, 1, 2]))
        lines.append((bank_day, rng.random(), [bank_day.isoformat(), bank_day.isoformat(),
                                               str(-amount if refund else amount), "PLN",
                                               rng.choice(_COUNTERPARTIES).replace('"', "").replace(";", ""),
                                               _iban(rng).replace(" ", ""), f"Zamowienie {n:05d}",
                                               f"ZWROT/{ref}" if refund else ref]))
    lines.sort(key=lambda item: (item[0], item[1]))
    out = ["booking_date,value_date,amount_grosze,currency,counterparty,iban,title,reference,txn_id"]
    for k, (_, _, row) in enumerate(lines, start=1):
        out.append(",".join(row + [f"T202611{k:07d}"]))
    journal = ["entry_id,merchant_id,reference,amount,direction,booked_on,kind", *entries]
    return "\n".join(journal) + "\n", "\n".join(out) + "\n"


_RATE_CURRENCIES = {"EUR": (41_900, 43_100), "USD": (38_800, 40_600), "GBP": (48_700, 50_400), "CZK": (1_690, 1_760)}
_BAD_RATE_DAYS = {(date(2026, 11, 12), "EUR"), (date(2026, 11, 13), "USD"), (date(2026, 11, 13), "GBP")}


def _rate_text(scaled: int) -> str:
    return f"{scaled // 10_000},{scaled % 10_000:04d}"


def _float_truncates(scaled: int) -> bool:
    """True when `int(float(text) * 10 000)` does not give back `scaled` (the planted fx bug bites)."""
    return int(float(_rate_text(scaled).replace(",", ".")) * 10_000) != scaled


def _half_up(numerator: int, denominator: int) -> int:
    quotient, remainder = divmod(numerator, denominator)
    return quotient + (1 if 2 * remainder >= denominator else 0)


def nbp_rates(rng: random.Random) -> tuple[str, dict]:
    """The NBP mid-rate tables of November 2026 (business days only): `(csv text, {(day, currency): scaled})`.

    On the days of `_BAD_RATE_DAYS` the rate is one that a float round trip truncates; on 12 and 13 November every
    other currency has a rate that survives it, so the card mismatches point at exactly those tables.
    """
    rows, scaled = ["date;table;currency;rate"], {}
    day, number = date(2026, 11, 2), 212
    while day.month == 11:
        if day.weekday() < 5 and day != date(2026, 11, 11):
            table = f"{number}/A/NBP/2026"
            number += 1
            for currency, (low, high) in _RATE_CURRENCIES.items():
                while True:
                    value = _rint(rng, low, high)
                    bad = _float_truncates(value)
                    if (day, currency) in _BAD_RATE_DAYS:
                        if bad:
                            break
                    elif day in (date(2026, 11, 12), date(2026, 11, 13)):
                        if not bad:
                            break
                    else:
                        break
                scaled[(day, currency)] = value
                rows.append(f"{day.isoformat()};{table};{currency};{_rate_text(value)}")
        day += timedelta(days=1)
    return "\n".join(rows) + "\n", scaled


def cards_log(rng: random.Random, merchant_ids: list[str], scaled: dict) -> tuple[str, dict]:
    """The log of the card settlement check of 2026-11-15 (sub-task 5); about 5 000 lines.

    Card payments of 12-14 November in EUR, USD, GBP, CZK and PLN are booked at our rate and compared with what the
    acquirer paid (converted at the exact table rate). They differ only where the planted float parsing of a rate
    truncates it, i.e. on the tables of `_BAD_RATE_DAYS`. Returns the log and facts for the hidden tests.
    """
    log = _Log(rng, datetime(2026, 11, 15, 4, 0, 0))
    ordinary = [m for m in merchant_ids if not m.startswith("MP")]
    log.add("INFO", "cards.run", "run=K20261115 card settlement check, acquirer file ACQ-20261115-01.csv")
    log.add("INFO", "cards.rates", "loaded NBP tables from reference-data/nbp-rates-2026-11.csv")
    for _ in range(5):
        log.noise()
    differences, total, sample_bad = 0, 0, None
    count = 0
    for day in (date(2026, 11, 12), date(2026, 11, 13), date(2026, 11, 14)):
        table_day = day if day.weekday() < 5 else day - timedelta(days=1)
        log.add("INFO", "cards.run", f"checking card payments of {day.isoformat()}")
        for n in range(1, 1201):
            count += 1
            currency = rng.choice(["EUR"] * 5 + ["USD"] * 3 + ["GBP"] * 2 + ["CZK", "PLN", "PLN"])
            m = rng.choice(ordinary)
            minor = _rint(rng, 300, 90_000) if currency != "CZK" else _rint(rng, 5_000, 900_000)
            tx = f"CT{day:%y%m%d}{n:05d}"
            if currency == "PLN":
                log.add("DEBUG", "cards.book", f"tx {tx} merchant={m} PLN {minor} -> {minor}", (1, 6))
                continue
            exact = scaled[(table_day, currency)]
            truncated = int(float(_rate_text(exact).replace(",", ".")) * 10_000)
            paid, booked = _half_up(minor * exact, 10_000), _half_up(minor * truncated, 10_000)
            log.add("DEBUG", "cards.book", f"tx {tx} merchant={m} {currency} {minor} rate={_rate_text(exact)} "
                    f"-> {booked}", (1, 6))
            if paid != booked:
                differences += 1
                total += paid - booked
                if sample_bad is None and currency == "EUR":
                    sample_bad = (tx, m, day, currency, minor, paid)
                log.add("WARN", "cards.check", f"tx {tx} acquirer paid {paid}, booked {booked} "
                        f"(diff {paid - booked})")
            if rng.random() < 0.04:
                log.noise()
        for _ in range(_rint(rng, 3, 6)):
            log.add("WARN", "cards.chargeback", f"chargeback notice for tx CT{day:%y%m%d}{_rint(rng, 1, 1200):05d}: "
                    f"reason {rng.choice(['4837', '4853', '4863'])}, will be booked by the disputes job")
    log.add("ERROR", "cards.check", f"{differences} of {count} card payments differ from the acquirer "
            f"(sum of differences {total} grosze); report sent to finance-ops")
    while len(log.lines) < 3995:
        log.noise()
    log.add("INFO", "cards.run", "run=K20261115 done (exit 1)")
    return "\n".join(log.lines) + "\n", {"differences": differences, "sample": sample_bad}


def limits_log(rng: random.Random, merchant_ids: list[str]) -> str:
    """The log of the velocity limit checks of 2026-11-16 (sub-task 7); about 3 000 lines.

    Two ordinary merchants really exceed 40 payouts (legitimate holds); the marketplace sellers (`MP0042-S...`) each
    make 12-16 payouts but share one counter, so most of their payouts after the 40th of the marketplace are held.
    The log shows the counter's value, never its key.
    """
    log = _Log(rng, datetime(2026, 11, 16, 0, 5, 0))
    ordinary = [m for m in merchant_ids if not m.startswith("MP")]
    sellers = [m for m in merchant_ids if m.startswith("MP")]
    heavy = {ordinary[3]: 46, ordinary[17]: 42}
    items = []
    for m in ordinary:
        items += [m] * heavy.get(m, _rint(rng, 18, 34))
    for m in sellers:
        items += [m] * _rint(rng, 12, 16)
    rng.shuffle(items)
    log.add("INFO", "limits.run", "run=V20261116 velocity checks of the payouts created on 2026-11-16 "
            "(limit 40 per day)")
    counters: dict[str, int] = {}
    held = 0
    for n, m in enumerate(items, start=1):
        key = m[:6]
        counters[key] = counters.get(key, 0) + 1
        pid = f"P20261116-{n:06d}"
        if counters[key] > 40:
            held += 1
            log.add("WARN", "limits.velocity", f"velocity hold payout {pid} merchant={m} count={counters[key]} "
                    "limit=40", (5, 400))
        else:
            log.add("DEBUG", "limits.velocity", f"payout {pid} merchant={m} count={counters[key]} limit=40", (5, 400))
        if rng.random() < 0.06:
            log.noise()
    log.add("WARN", "limits.run", f"{held} payouts held by the velocity limit; merchants notified")
    while len(log.lines) < 2995:
        log.noise()
    log.add("INFO", "limits.run", "run=V20261116 done")
    return "\n".join(log.lines) + "\n"


def corporate_v2(rng: random.Random, merchant_ids: list[str]) -> tuple[str, dict]:
    """The sample corporate export in format v2 with three account sections: `(text, {account: rows})`."""
    out, counts = ["﻿"], {}
    accounts = ["PL27 1140 2004 0000 3502 7741 9031", "PL61 1090 1014 0000 0712 1981 2874",
                "PL83 1020 1026 0000 1602 0365 7713"]
    for account in accounts:
        rows, total = [], 0
        for n in range(1, _rint(rng, 48, 72)):
            m = rng.choice([x for x in merchant_ids if not x.startswith("MP")])
            amount = _rint(rng, 500, 1_900_000) if rng.random() > 0.08 else -_rint(rng, 900, 59_000)
            day = date(2026, 11, 2) + timedelta(days=n // 7)
            rows.append(";".join(_v2_field(x) for x in [
                f"{day:%d.%m.%Y}", f"{day:%d.%m.%Y}", _pl_amount(amount, " " if rng.random() < 0.4 else " "),
                "PLN", rng.choice(_COUNTERPARTIES), _iban(rng), f"Faktura FV/{_rint(rng, 100, 999)}/11/2026",
                f"PAY/{m}/{_rint(rng, 100, 999_999):06d}", f"2611{len(counts)}{n:09d}"]))
            total += amount
        out.append(f"#RACHUNEK;{account}\r\n" + ";".join(V2_HEADER) + "\r\n" + "\r\n".join(rows) + "\r\n"
                   + f"#KONTROLA;{len(rows)};{_pl_amount(total, ' ')}\r\n")
        counts["".join(account.split())] = len(rows)
    return "".join(out), counts


# --------------------------------------------------------------------------------------------------------------------
# Documents, visible tests
# --------------------------------------------------------------------------------------------------------------------

STATEMENT_V2_DOC = _d('''
    # Bank statement export, format v2

    From 1 December 2026 the bank sends the daily statements of our collection account in this format; it replaces
    format v1 (`ledger.importer.import_statement`). The bank's own description is in Polish; this is our summary of
    it, checked against the sample in `data/statements/`.

    ## File

    - Encoding UTF-8. The file may start with a byte order mark (U+FEFF); it is not part of the first column name.
    - Field separator `;`, text qualifier `"`. A qualified field may contain `;`. A `"` inside a qualified field is
      written twice (`""`). Fields never contain line breaks.
    - Line ending CRLF.
    - The first line is the header with the column names below, in this order. The last line is the control record.

    ## Columns

    | # | Column | Example | Notes |
    |---|---|---|---|
    | 1 | Data księgowania | 12.11.2026 | booking date, `DD.MM.YYYY` |
    | 2 | Data waluty | 13.11.2026 | value date, `DD.MM.YYYY` |
    | 3 | Kwota | -1 234,56 | signed amount: money out is negative; decimal comma, always two decimals; thousands may be separated by a space or a non-breaking space (U+00A0) |
    | 4 | Waluta | PLN | ISO 4217 code |
    | 5 | Kontrahent | "Firma ""Pod Lipą"" Sp. z o.o." | counterparty name |
    | 6 | Rachunek kontrahenta | PL61 1090 1014 0000 0712 1981 2874 | counterparty IBAN, may contain spaces |
    | 7 | Tytuł | Zamówienie 12345 | free text |
    | 8 | Referencja | PAY/M10417/000123 | our reference when the payer gave one; may be empty |
    | 9 | Id transakcji | 26110000000017 | the bank's id of the transaction, unique |

    ## Control record

    `#KONTROLA;<number of statement rows>;<sum of all amounts>`, the sum written like `Kwota`. The importer checks
    both values and refuses the whole file (`StatementError`) when either does not match. The control record is not
    a statement line.

    ## Mapping to `StatementLine`

    | Field | From |
    |---|---|
    | `booking_date`, `value_date` | columns 1 and 2 as `datetime.date` |
    | `amount` | column 3 in grosze, signed |
    | `currency` | column 4, upper case. Our accounts are PLN only: a row in another currency makes the file invalid (`StatementError`) |
    | `counterparty` | column 5, surrounding spaces removed |
    | `iban` | column 6 without spaces, upper case |
    | `title` | column 7, surrounding spaces removed |
    | `reference` | column 8, surrounding spaces removed (`""` when empty) |
    | `txn_id` | column 9 |

    Rows are returned in file order. A file whose header differs from the column list is refused (`StatementError`).

    ## Corporate export (several accounts)

    Corporate customers get one file with several of our accounts. Each account is a section: a line
    `#RACHUNEK;<IBAN of our account>`, then the header and the rows as above, then the section's own control record.
    The file starts with the first `#RACHUNEK` line (a byte order mark may precede it); a row before it, an account
    that appears twice or a section whose control record does not match makes the whole file invalid
    (`StatementError`). Account IBANs are compared without spaces, upper case.
    ''')

ARCHITECTURE_DOC = _d('''
    # Ledger: how the pieces fit

    The ledger collects card and transfer payments for merchants, pays them out every night and checks the bank's
    statements against its own journal. Everything runs as batch jobs; partners use the API modules.

    | Module | What it does |
    |---|---|
    | `money` | grosze helpers: parsing, formatting, rounding (`round_half_up`) |
    | `config`, `profiles`, `settings` | default settings, per-environment overrides, the effective settings (`settings.load`) |
    | `bankfile` | the payout files we upload to the bank, and what the old clearing interface needs |
    | `refs` | batch ids, payout ids, end-to-end and refund references |
    | `payouts`, `settlement` | payouts, their hold period, the nightly batches |
    | `statements`, `importer` | bank statement lines and the statement importers |
    | `journal`, `matching_rules`, `reconcile` | our postings and the nightly reconciliation against the statement |
    | `fees`, `merchants` | fee plans and merchant master data |
    | `api_v1`, `api_v2` | partner APIs: v1 is stable, new endpoints go to v2 |
    | `cards`, `fx` | card payments in foreign currencies and the NBP rate tables |
    | `limits` | velocity limits on payouts |
    | `bankdays`, `iban`, `audit`, `notifications`, `refunds`, `reports`, `errors` | supporting pieces |

    ## Jobs

    - `settlement` (01:00 UTC, 00:30 with the legacy interface): balances -> payouts -> batches -> bank files ->
      upload -> wait for the bank's status reports. Logs go to `data/logs/settlement-<date>.log`.
    - `reconcile` (03:15 UTC): the previous day's statement against the journal export. Logs go to
      `data/logs/reconcile-<date>.log`.
    - `cards` (04:00 UTC): foreign card payments booked at the NBP rate (`reference-data/`) and compared with what the
      acquirer paid. Logs go to `data/logs/cards-<date>.log`.
    - `limits` (all day): the daily velocity limit on payouts. Logs go to `data/logs/limits-<date>.log`.

    ## Conventions

    - Fees and conversions round half up through `money.round_half_up`.
    - Tests: `python3 -m unittest discover -s tests -t .` from the repository root (stdlib `unittest` only).
    ''')

README = _d('''
    # ledger

    Payments ledger of the payments team: settlement, bank statements, reconciliation, fees and the partner API.
    See `docs/architecture.md` for the modules and the nightly jobs.

    ```
    python3 -m unittest discover -s tests -t .
    ```

    Data from production that we keep for investigations lives in `data/` (logs, statements, journal exports).
    ''')

VISIBLE_TESTS = {
    "tests/__init__.py": "",
    "tests/test_money.py": _d('''
        import unittest

        from ledger import money
        from ledger.errors import ValidationError


        class MoneyTest(unittest.TestCase):
            def test_parse_amount(self):
                self.assertEqual(money.parse_amount("12.34"), 1234)
                self.assertEqual(money.parse_amount("12,34"), 1234)
                self.assertEqual(money.parse_amount("-0,5"), -50)
                self.assertEqual(money.parse_amount("7"), 700)

            def test_parse_amount_rejects_garbage(self):
                for text in ("", "1,234.5", "12.345", "abc"):
                    with self.assertRaises(ValidationError):
                        money.parse_amount(text)

            def test_format(self):
                self.assertEqual(money.format_amount(123456), "1234.56")
                self.assertEqual(money.format_amount(-5), "-0.05")
                self.assertEqual(money.format_amount_pl(123456), "1 234,56")

            def test_round_half_up(self):
                self.assertEqual(money.round_half_up(5, 2), 3)
                self.assertEqual(money.round_half_up(-5, 2), -3)
                self.assertEqual(money.round_half_up(4, 3), 1)
                self.assertEqual(money.percent_of(10_000, 190), 190)

            def test_split_evenly(self):
                self.assertEqual(money.split_evenly(100, 3), [34, 33, 33])


        if __name__ == "__main__":
            unittest.main()
        '''),
    "tests/test_refs.py": _d('''
        import unittest
        from datetime import date

        from ledger import refs


        class RefsTest(unittest.TestCase):
            def test_ids(self):
                self.assertEqual(refs.batch_id(date(2026, 11, 12), 7), "B20261112-07")
                self.assertEqual(refs.payout_id(date(2026, 11, 12), 412), "P20261112-000412")

            def test_e2e_reference_of_an_ordinary_merchant(self):
                self.assertEqual(refs.e2e_reference("M10417", "B20261112-07", 12), "SETL/M10417/B20261112-07/0012")

            def test_parse(self):
                self.assertEqual(refs.parse_e2e_reference("SETL/M10417/B20261112-07/0012"),
                                 ("M10417", "B20261112-07", 12))
                self.assertIsNone(refs.parse_e2e_reference("PAY/M10417/000123"))

            def test_sepa_text(self):
                self.assertTrue(refs.is_sepa_text("SETL/M10417/B20261112-07/0012"))
                self.assertFalse(refs.is_sepa_text("SETL_M10417"))


        if __name__ == "__main__":
            unittest.main()
        '''),
    "tests/test_settlement.py": _d('''
        import unittest
        from datetime import date, datetime

        from ledger import settlement
        from ledger.errors import SettlementError
        from ledger.payouts import Payout

        AT = datetime(2026, 11, 12, 1, 0)


        class SettlementTest(unittest.TestCase):
            def test_build_batch_numbers_each_merchants_payouts(self):
                batch = settlement.build_batch("B20261112-01", [
                    Payout("P1", "M10417", 1000, AT), Payout("P2", "M10418", 2000, AT), Payout("P3", "M10417", 500, AT)])
                self.assertEqual([line.e2e_reference for line in batch.lines],
                                 ["SETL/M10417/B20261112-01/0001", "SETL/M10418/B20261112-01/0001",
                                  "SETL/M10417/B20261112-01/0002"])
                self.assertEqual(batch.control_sum, 3500)
                settlement.validate_batch(batch)

            def test_empty_and_non_positive(self):
                with self.assertRaises(SettlementError):
                    settlement.build_batch("B20261112-01", [])
                with self.assertRaises(SettlementError):
                    settlement.build_batch("B20261112-01", [Payout("P1", "M10417", 0, AT)])

            def test_marketplace_gets_its_own_batches(self):
                items = [Payout(f"P{i}", "M10417", 100, AT) for i in range(3)] + [Payout("P9", "MP0042-S00401", 100, AT)]
                batches = settlement.split_into_batches(date(2026, 11, 12), items, 2)
                self.assertEqual([b.batch_id for b in batches], ["B20261112-01", "B20261112-02", "B20261112-03"])
                self.assertEqual([line.merchant_id for line in batches[2].lines], ["MP0042-S00401"])


        if __name__ == "__main__":
            unittest.main()
        '''),
    "tests/test_settings.py": _d('''
        import unittest

        from ledger import settings
        from ledger.errors import ConfigError


        class SettingsTest(unittest.TestCase):
            def test_development_uses_the_defaults_for_the_hold(self):
                self.assertEqual(settings.load("development", env={})["reserve_hold_minutes"], 1440)

            def test_sandbox_holds_one_hour(self):
                self.assertEqual(settings.load("sandbox", env={})["reserve_hold_minutes"], 60)

            def test_production_talks_to_the_legacy_interface(self):
                values = settings.load("production", env={})
                self.assertTrue(values["legacy_bank_mode"])
                self.assertEqual(values["bank_file_encoding"], "cp1250")

            def test_preprod_uses_the_sorbnet_gateway(self):
                values = settings.load("preprod", env={})
                self.assertEqual((values["legacy_bank_mode"], values["bank_interface"]), (True, "sorbnet"))

            def test_environment_variables_win(self):
                values = settings.load("staging", env={"LEDGER_DB_POOL_SIZE": "3", "OTHER": "x"})
                self.assertEqual(values["db_pool_size"], 3)

            def test_unknown_environment(self):
                with self.assertRaises(ConfigError):
                    settings.load("qa", env={})


        if __name__ == "__main__":
            unittest.main()
        '''),
    "tests/test_importer.py": _d('''
        import os
        import tempfile
        import unittest
        from datetime import date

        from ledger import importer
        from ledger.errors import StatementError

        V1 = ("booking_date,value_date,amount_grosze,currency,counterparty,iban,title,reference,txn_id\\n"
              "2026-11-03,2026-11-03,12550,PLN,Jan Kowalski,pl61 1090 1014,Zamowienie 1,PAY/M10417/000123,T1\\n"
              "2026-11-03,2026-11-04,-4599,pln,Anna Nowak,PL61109010140000,Zwrot,RFND/M10417/000877,T2\\n"
              "2026-11-03,2026-11-04,-4599,pln,Anna Nowak,PL61109010140000,Zwrot,RFND/M10417/000877,T2\\n")


        class ImporterV1Test(unittest.TestCase):
            def write(self, text):
                fd, path = tempfile.mkstemp(suffix=".csv")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
                self.addCleanup(os.unlink, path)
                return path

            def test_reads_lines_and_drops_repeats(self):
                lines = importer.import_statement(self.write(V1))
                self.assertEqual(len(lines), 2)
                self.assertEqual(lines[0].amount, 12550)
                self.assertEqual(lines[0].iban, "PL6110901014")
                self.assertEqual(lines[1].currency, "PLN")
                self.assertEqual(lines[1].value_date, date(2026, 11, 4))

            def test_wrong_header(self):
                with self.assertRaises(StatementError):
                    importer.import_statement(self.write("a,b\\n1,2\\n"))


        if __name__ == "__main__":
            unittest.main()
        '''),
    "tests/test_reconcile.py": _d('''
        import unittest
        from datetime import date

        from ledger import reconcile
        from ledger.journal import LedgerEntry
        from ledger.statements import StatementLine


        def line(ref, amount, day=date(2026, 11, 13)):
            return StatementLine(day, day, amount, "PLN", "x", "PL0", "t", ref, f"T-{ref}-{amount}")


        class ReconcileTest(unittest.TestCase):
            def test_payment_matches_its_line(self):
                entry = LedgerEntry("E1", "M10417", "PAY/M10417/000123", 12550, "C", date(2026, 11, 12))
                result = reconcile.match_lines([entry], [line(" pay/m10417/000123 ", 12550)])
                self.assertEqual(len(result.matched), 1)
                self.assertEqual(reconcile.summary(result), {"matched": 1, "unmatched_lines": 0,
                                                             "unmatched_entries": 0})

            def test_dates_too_far_apart(self):
                entry = LedgerEntry("E1", "M10417", "PAY/M10417/000123", 12550, "C", date(2026, 11, 9))
                result = reconcile.match_lines([entry], [line("PAY/M10417/000123", 12550)])
                self.assertEqual((len(result.matched), len(result.unmatched_lines)), (0, 1))

            def test_a_day_of_bank_fees_only(self):
                result = reconcile.match_lines([], [line("", -100 - i) for i in range(30)])
                self.assertEqual(reconcile.summary(result), {"matched": 0, "unmatched_lines": 30,
                                                             "unmatched_entries": 0})

            def test_a_bank_fee_has_no_entry(self):
                result = reconcile.match_lines([], [line("", -500)])
                self.assertEqual(len(result.unmatched_lines), 1)


        if __name__ == "__main__":
            unittest.main()
        '''),
    "tests/test_fees.py": _d('''
        import unittest

        from ledger import fees
        from ledger.errors import FeeError


        class FeesTest(unittest.TestCase):
            def test_standard(self):
                self.assertEqual(fees.quote(10_000, "standard"), 220)
                self.assertEqual(fees.quote(100, "standard"), 50)  # minimum

            def test_volume_and_nonprofit(self):
                self.assertEqual(fees.quote(10_000, "volume"), 160)
                self.assertEqual(fees.quote(10_050, "nonprofit"), 90)

            def test_net_amount(self):
                self.assertEqual(fees.net_amount(10_000, "standard"), 9_780)

            def test_unknown_plan_and_negative_amount(self):
                with self.assertRaises(FeeError):
                    fees.quote(100, "gold")
                with self.assertRaises(FeeError):
                    fees.quote(-1, "standard")


        if __name__ == "__main__":
            unittest.main()
        '''),
    "tests/test_api_v1.py": _d('''
        import unittest

        from ledger import api_v1, fees, merchants


        class ApiV1Test(unittest.TestCase):
            def test_quote_uses_the_merchants_plan(self):
                m = merchants.MERCHANTS[0]
                self.assertEqual(api_v1.quote_fee(10_000, m.merchant_id), fees.quote(10_000, m.plan))
                self.assertEqual(api_v1.merchant_plan(m.merchant_id), m.plan)

            def test_unknown_merchant_gets_the_default_plan(self):
                self.assertEqual(api_v1.quote_fee(10_000, "M99999"), fees.quote(10_000, "standard"))

            def test_plans_are_listed(self):
                self.assertTrue({"standard", "volume", "nonprofit"} <= set(api_v1.list_plans()))

            def test_release_time_in_sandbox(self):
                self.assertEqual(api_v1.payout_release_time("2026-11-12T01:00:00", "sandbox"), "2026-11-12T02:00:00")

            def test_reference_parts(self):
                self.assertEqual(api_v1.reference_parts("SETL/M10417/B20261112-07/0012"),
                                 {"merchant_id": "M10417", "batch_id": "B20261112-07", "seq": 12})


        if __name__ == "__main__":
            unittest.main()
        '''),
    "tests/test_bankdays.py": _d('''
        import unittest
        from datetime import date

        from ledger import bankdays


        class BankDaysTest(unittest.TestCase):
            def test_business_days(self):
                self.assertTrue(bankdays.is_business_day(date(2026, 11, 12)))
                self.assertFalse(bankdays.is_business_day(date(2026, 11, 11)))  # Independence Day
                self.assertFalse(bankdays.is_business_day(date(2026, 11, 14)))
                self.assertEqual(bankdays.next_business_day(date(2026, 11, 10)), date(2026, 11, 12))

            def test_clearing_of_the_api_calendars(self):
                self.assertEqual(bankdays.clearing_minutes("standard"), 1440)
                self.assertEqual(bankdays.clearing_minutes("express"), 240)


        if __name__ == "__main__":
            unittest.main()
        '''),
    "tests/test_bankfile.py": _d('''
        import unittest
        from datetime import datetime

        from ledger import bankfile, settings, settlement
        from ledger.errors import SettlementError
        from ledger.payouts import Payout

        AT = datetime(2026, 11, 12, 1, 0)


        class BankFileTest(unittest.TestCase):
            def batch(self):
                return settlement.build_batch("B20261112-01", [Payout("P1", "M10417", 12550, AT),
                                                               Payout("P2", "M10418", 99, AT)])

            def test_render_for_the_legacy_interface(self):
                rendered = bankfile.render_batch(self.batch(), {"M10417": "PL61", "M10418": "PL27"},
                                                 settings.load("production", env={}))
                text = rendered.content.decode("cp1250")
                self.assertEqual(rendered.name, "B20261112-01.csv")
                self.assertEqual(text.split("\\r\\n")[0], bankfile.HEADER)
                self.assertIn("#CONTROL;2;126.49", text)

            def test_missing_iban(self):
                with self.assertRaises(SettlementError):
                    bankfile.render_batch(self.batch(), {"M10417": "PL61"}, settings.load("development", env={}))


        if __name__ == "__main__":
            unittest.main()
        '''),
    "tests/test_journal.py": _d('''
        import os
        import tempfile
        import unittest
        from datetime import date

        from ledger import journal
        from ledger.errors import ReconcileError

        HEAD = "entry_id,merchant_id,reference,amount,direction,booked_on,kind\\n"


        class JournalTest(unittest.TestCase):
            def write(self, text):
                fd, path = tempfile.mkstemp(suffix=".csv")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
                self.addCleanup(os.unlink, path)
                return path

            def test_load_and_balance(self):
                entries = journal.load_journal(self.write(HEAD + "E1,M10417,PAY/M10417/000123,12550,C,2026-11-12,payment\\n"
                                                          "E2,M10417,RFND/M10417/000877,4599,D,2026-11-13,refund\\n"))
                self.assertEqual([e.booked_on for e in entries], [date(2026, 11, 12), date(2026, 11, 13)])
                self.assertEqual(journal.balance(entries), 12550 - 4599)

            def test_bad_rows(self):
                for row in ("E1,M1,R,0,C,2026-11-12,payment\\n", "E1,M1,R,5,X,2026-11-12,payment\\n",
                            "E1,M1,R,5,C,2026-11-12,payment\\nE1,M1,R,5,C,2026-11-12,payment\\n"):
                    with self.assertRaises(ReconcileError):
                        journal.load_journal(self.write(HEAD + row))


        if __name__ == "__main__":
            unittest.main()
        '''),
    "tests/test_statements.py": _d('''
        import unittest
        from datetime import date

        from ledger import statements
        from ledger.statements import StatementLine


        def line(txn, amount, day=date(2026, 11, 3)):
            return StatementLine(day, day, amount, "PLN", "x", "PL0", "t", "r", txn)


        class StatementsTest(unittest.TestCase):
            def test_helpers(self):
                self.assertEqual(statements.normalise_iban(" pl61 1090 1014 "), "PL6110901014")
                self.assertEqual(statements.bank_reference(" pay/m10417/000123 "), "PAY/M10417/000123")
                lines = [line("T1", 100), line("T2", -40, date(2026, 11, 4)), line("T1", 100)]
                self.assertEqual(statements.total(lines), 160)
                self.assertEqual([l.txn_id for l in statements.dedupe(lines)], ["T1", "T2"])
                self.assertEqual(sorted(statements.by_day(lines)), [date(2026, 11, 3), date(2026, 11, 4)])


        if __name__ == "__main__":
            unittest.main()
        '''),
    "tests/test_cards.py": _d('''
        import unittest
        from datetime import date

        from ledger import cards, fx


        class CardsTest(unittest.TestCase):
            def test_pln_payments_are_booked_as_they_are(self):
                tx = cards.CardTransaction("CT1", "M10417", date(2026, 11, 12), "PLN", 12345)
                self.assertEqual(cards.book(tx, {}), 12345)

            def test_conversion_and_the_table_of_the_last_business_day(self):
                rates = {(date(2026, 11, 13), "EUR"): fx.Rate("EUR", 42_500, "222/A/NBP/2026")}
                tx = cards.CardTransaction("CT2", "M10417", date(2026, 11, 15), "EUR", 1000)
                self.assertEqual(cards.book(tx, rates), 4250)
                self.assertEqual(cards.mismatches([tx], {"CT2": 4251}, rates), [("CT2", 4251, 4250)])
                with self.assertRaises(KeyError):
                    fx.rate_for(rates, date(2026, 11, 13), "USD")


        if __name__ == "__main__":
            unittest.main()
        '''),
    "tests/test_limits.py": _d('''
        import unittest
        from datetime import datetime, timedelta

        from ledger import limits
        from ledger.payouts import Payout


        class LimitsTest(unittest.TestCase):
            def test_an_ordinary_merchant_is_held_after_the_limit(self):
                at = datetime(2026, 11, 16, 8, 0)
                items = [Payout(f"P{i}", "M10417", 1000, at + timedelta(minutes=i)) for i in range(42)]
                self.assertEqual(limits.held_by_velocity(items, {"limits_daily_payout_count": 40}), items[40:])
                self.assertTrue(limits.over_daily_count(40, {"limits_daily_payout_count": 40}))


        if __name__ == "__main__":
            unittest.main()
        '''),
    "tests/test_payouts.py": _d('''
        import unittest
        from datetime import datetime

        from ledger import payouts
        from ledger.payouts import Payout


        class PayoutsTest(unittest.TestCase):
            def test_release_after_the_hold(self):
                settings = {"reserve_hold_minutes": 90, "payout_min_amount": 1000}
                created = datetime(2026, 11, 12, 1, 0)
                self.assertEqual(payouts.release_at(created, settings), datetime(2026, 11, 12, 2, 30))
                items = [Payout("P1", "M1", 5000, created), Payout("P2", "M1", 10, created)]
                self.assertEqual(payouts.releasable(items, datetime(2026, 11, 12, 3, 0), settings), items[:1])
                self.assertEqual(payouts.releasable(items, datetime(2026, 11, 12, 2, 0), settings), [])


        if __name__ == "__main__":
            unittest.main()
        '''),
}


def _records_test(records: dict[str, list]) -> str:
    """`tests/test_records.py`: a row round trip of every generated record, plus its registry and code table."""
    out = ["import importlib", "import unittest", "", "", "class RecordsTest(unittest.TestCase):"]
    for module in sorted(records):
        for noun, fields, registry, table, totals in records[module]:
            cls = f"{_camel(noun)}Record"
            values = ", ".join(f"{f}={(1234 + i) if t == 'int' else repr(f'{f}-{i}')}" for i, (f, t) in enumerate(fields))
            out += [f"    def test_{module}_{noun}(self):",
                    f"        mod = importlib.import_module(\"ledger.{module}\")",
                    f"        item = mod.{cls}({values})",
                    f"        self.assertEqual(mod.{cls}.from_row(item.to_row()), item)"]
            if totals:
                out.append(f"        self.assertEqual(mod.total_{noun}_amount([item, item]), 2 * item.amount)")
            if registry:
                out += [f"        registry = mod.{_camel(noun)}Registry([item])",
                        f"        self.assertEqual((len(registry), registry.get(item.{noun}_id)), (1, item))",
                        f"        registry.remove(item.{noun}_id)",
                        "        self.assertEqual(len(registry), 0)"]
            if table:
                out.append(f"        self.assertIn(\"unknown\", mod.describe_{noun}_code(\"??\"))")
            out.append("")
    out += ["", "if __name__ == \"__main__\":", "    unittest.main()", ""]
    return "\n".join(out)


def _codes_test(validators: dict[str, list[tuple[str, str, str]]]) -> str:
    """`tests/test_codes.py`: one test per generated validator (a valid and an invalid value)."""
    out = ["import importlib", "import unittest", "", "from ledger.errors import ValidationError", "", "CASES = ["]
    for module in sorted(validators):
        for fn, valid, invalid in validators[module]:
            out.append(f"    ({module!r}, {fn!r}, {valid!r}, {invalid!r}),")
    out += ["]", "", "", "class CodesTest(unittest.TestCase):", "    def test_validators(self):",
            "        for module, name, valid, invalid in CASES:",
            "            with self.subTest(function=f\"{module}.{name}\"):",
            "                check = getattr(importlib.import_module(f\"ledger.{module}\"), name)",
            "                self.assertEqual(check(f\"  {valid} \"), valid)",
            "                with self.assertRaises(ValidationError):",
            "                    check(invalid)", "", "", "if __name__ == \"__main__\":", "    unittest.main()", ""]
    return "\n".join(out)


# --------------------------------------------------------------------------------------------------------------------
# The contest
# --------------------------------------------------------------------------------------------------------------------

def _sources() -> tuple[dict[str, str], dict]:
    """All generated files of the untouched contest and a few facts about them (merchant ids, sample sums); fresh
    copies of the cached generation."""
    files, info = _generate()
    return dict(files), dict(info)


@functools.lru_cache(maxsize=1)
def _generate() -> tuple[dict[str, str], dict]:
    rng = random.Random(SEED)
    validators: dict = {}
    merchants_core = _merchants_core(rng)
    merchant_ids = re.findall(r'Merchant\("([A-Z0-9-]+)"', merchants_core)
    cores = {**_CORES, "config": _config_core(), "merchants": merchants_core}
    files = {"ledger/__init__.py": '"""Payments ledger: settlement, statements, reconciliation and fees."""\n'}
    for name, (doc, imports, nouns, target) in _MODULES.items():
        target = 450 if name == "api_v1" else MODULE_LINES
        files[f"ledger/{name}.py"] = _assemble(name, doc, imports, cores[name], nouns, target, rng, validators)
    files["data/logs/settlement-2026-11-12.log"] = settlement_log(rng, merchant_ids)
    files["data/logs/reconcile-2026-11-14.log"] = reconcile_log(rng, merchant_ids)
    sample, rows, total = sample_v2(rng, merchant_ids)
    files["data/statements/sample-2026-11-v2.csv"] = sample
    journal, statement = month_files(rng, merchant_ids)
    files["data/journal/2026-11.csv"] = journal
    files["data/statements/2026-11-full.csv"] = statement
    rates_text, rates = nbp_rates(rng)
    files["reference-data/nbp-rates-2026-11.csv"] = rates_text
    files["data/logs/cards-2026-11-15.log"], cards_facts = cards_log(rng, merchant_ids, rates)
    files["data/logs/limits-2026-11-16.log"] = limits_log(rng, merchant_ids)
    files["data/statements/sample-corporate-2026-11-v2.csv"], corporate = corporate_v2(rng, merchant_ids)
    standard = next(m for m, plan in re.findall(r'Merchant\("(M\d{5})", "[^"]+", "([a-z]+)"', merchants_core)
                    if plan == "standard" and not m.startswith("M3011"))
    files["docs/bank-statement-v2.md"] = STATEMENT_V2_DOC
    files["docs/architecture.md"] = ARCHITECTURE_DOC
    files["README.md"] = README
    files.update(VISIBLE_TESTS)
    records = validators.pop("_records")
    files["tests/test_codes.py"] = _codes_test(validators)
    files["tests/test_records.py"] = _records_test(records)
    return files, {"merchant_ids": merchant_ids, "sample_rows": rows, "sample_total": total, "rates": rates,
                   "cards": cards_facts, "corporate": corporate, "statement_merchant": standard}


def contest_files(reference: bool = False, extended: bool = False) -> dict[str, str]:
    """The files of the contest (path relative to the contest -> text); with `reference`, the reference solution
    of every sub-task is applied (tests only); with `extended`, the files of X1-X10 are added (case `long-chain-xl`).

    Raises:
        AssertionError: A reference replacement does not apply exactly once (the generator changed).
    """
    files, _ = _sources()
    if extended:
        files.update(_generate_ext()[0])
    if reference:
        files = _patched(files, REFERENCE)
        files = _console_in_polish(files)
        if extended:
            files = _patched(files, EXT_REFERENCE)
    return files


def _patched(files: dict[str, str], patches: list) -> dict[str, str]:
    """`files` with every `(file, old, new, regex)` patch applied; each must apply exactly once."""
    files = dict(files)
    for rel, old, new, regex in patches:
        text = files[rel]
        if regex:
            text, count = re.subn(old, new, text)
        else:
            count = text.count(old)
            text = text.replace(old, new)
        if count != 1:
            raise AssertionError(f"reference patch of {rel} applies {count} times: {old[:60]!r}")
        files[rel] = text
    return files


def facts() -> dict:
    """Facts the hidden tests and the case are built from (sample sums, merchant ids)."""
    return _sources()[1]


# --------------------------------------------------------------------------------------------------------------------
# Hidden tests (`<tmp>/hidden/chain/`, run with the contest as cwd)
# --------------------------------------------------------------------------------------------------------------------

def _flex_fee(amount: int) -> int:
    """The flex fee the hidden tests expect: 1.375 % rounded half up, plus 25 grosze."""
    quotient, remainder = divmod(amount * 1375, 100_000)
    return quotient + (1 if 2 * remainder >= 100_000 else 0) + 25


def _standard_fee(amount: int) -> int:
    quotient, remainder = divmod(amount * 190, 10_000)
    return max(quotient + (1 if 2 * remainder >= 10_000 else 0) + 30, 50)


_HIDDEN_S1 = _d('''
    """Sub-task 1: end-to-end references stay unique and valid for long (marketplace) merchant ids."""
    import re
    import unittest
    from datetime import datetime

    from ledger import refs, settlement
    from ledger.payouts import Payout

    SEPA = re.compile(r"^[A-Za-z0-9/\\-?:().,'+ ]+$")
    LONG = [f"MP0042-S{n:05d}" for n in range(300, 340)]


    class E2EReferenceTest(unittest.TestCase):
        def test_ordinary_references_keep_their_format(self):
            self.assertEqual(refs.e2e_reference("M10417", "B20261112-07", 12), "SETL/M10417/B20261112-07/0012")
            self.assertEqual(refs.e2e_reference("M29981", "B20261231-24", 1), "SETL/M29981/B20261231-24/0001")

        def test_unique_for_one_marketplace_merchant(self):
            got = [refs.e2e_reference("MP0042-S00317", "B20261112-22", seq) for seq in range(1, 400)]
            self.assertEqual(len(set(got)), len(got))

        def test_unique_across_marketplace_merchants(self):
            got = {refs.e2e_reference(m, "B20261112-22", seq) for m in LONG for seq in range(1, 30)}
            self.assertEqual(len(got), len(LONG) * 29)

        def test_length_and_charset(self):
            for m in LONG + ["M10417"]:
                for seq in (1, 9, 10, 999, 9999):
                    ref = refs.e2e_reference(m, "B20261112-22", seq)
                    self.assertLessEqual(len(ref), 35, ref)
                    self.assertRegex(ref, SEPA)

        def test_deterministic(self):
            self.assertEqual(refs.e2e_reference("MP0042-S00317", "B20261112-22", 3),
                             refs.e2e_reference("MP0042-S00317", "B20261112-22", 3))

        def test_marketplace_batch_has_unique_references(self):
            at = datetime(2026, 11, 12, 1, 0)
            items = [Payout(f"P{i}", "MP0042-S00317", 1000 + i, at) for i in range(16)]
            items += [Payout(f"Q{i}", "MP0042-S00318", 2000 + i, at) for i in range(9)]
            batch = settlement.build_batch("B20261112-22", items)
            references = [line.e2e_reference for line in batch.lines]
            self.assertEqual(len(set(references)), len(references))
            settlement.validate_batch(batch)


    if __name__ == "__main__":
        unittest.main()
    ''')

_HIDDEN_S2 = _d('''
    """Sub-task 2: `importer.import_statement_v2` reads the bank's format v2 (docs/bank-statement-v2.md)."""
    import os
    import tempfile
    import unittest
    from datetime import date

    from ledger import importer
    from ledger.errors import StatementError
    from ledger.statements import StatementLine

    HEADER = "Data księgowania;Data waluty;Kwota;Waluta;Kontrahent;Rachunek kontrahenta;Tytuł;Referencja;Id transakcji"
    ROWS = [
        '03.11.2026;04.11.2026;1 234,56;PLN;"Firma ""Pod Lipą"" Sp. z o.o.";pl61 1090 1014 0000 0712 1981 2874;'
        '" Zamówienie 7; dopłata ";PAY/M10417/000123;26110000000017',
        "03.11.2026;03.11.2026;-45,99;PLN;  Anna Nowak ;PL27114020040000350277419031;Zwrot;;26110000000018",
    ]
    EXPECTED = [
        StatementLine(date(2026, 11, 3), date(2026, 11, 4), 123456, "PLN", 'Firma "Pod Lipą" Sp. z o.o.',
                      "PL61109010140000071219812874", "Zamówienie 7; dopłata", "PAY/M10417/000123", "26110000000017"),
        StatementLine(date(2026, 11, 3), date(2026, 11, 3), -4599, "PLN", "Anna Nowak",
                      "PL27114020040000350277419031", "Zwrot", "", "26110000000018"),
    ]


    def row(amount: str, n: int, currency: str = "PLN") -> str:
        return f"05.11.2026;05.11.2026;{amount};{currency};Jan Kowalski;PL61109010140000071219812874;t;;2611{n:010d}"


    class ImportV2Test(unittest.TestCase):
        def write(self, rows, control=None):
            if control is None:
                control = f"#KONTROLA;{len(rows)};{self.total}"
            text = "\\ufeff" + HEADER + "\\r\\n" + "".join(r + "\\r\\n" for r in rows) + control + "\\r\\n"
            fd, path = tempfile.mkstemp(suffix=".csv")
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            self.addCleanup(os.unlink, path)
            return path

        def test_reads_every_field(self):
            self.total = "1 188,57"
            self.assertEqual(importer.import_statement_v2(self.write(ROWS)), EXPECTED)

        def test_amount_formats(self):
            amounts = ["1 234,56", "1\\u00a0234,56", "-12,50", "0,07", "12\\u00a0345\\u00a0678,90", "-1 000 000,00"]
            self.total = "11 348 135,59"
            lines = importer.import_statement_v2(self.write([row(a, i) for i, a in enumerate(amounts)]))
            self.assertEqual([line.amount for line in lines], [123456, 123456, -1250, 7, 1234567890, -100000000])
            for line in lines:
                self.assertIs(type(line.amount), int)

        def test_control_count_mismatch(self):
            self.total = "1 188,57"
            with self.assertRaises(StatementError):
                importer.import_statement_v2(self.write(ROWS, control="#KONTROLA;3;1 188,57"))

        def test_control_sum_mismatch(self):
            self.total = "1 188,57"
            with self.assertRaises(StatementError):
                importer.import_statement_v2(self.write(ROWS, control="#KONTROLA;2;1 188,58"))

        def test_other_currency_is_refused(self):
            self.total = "10,00"
            with self.assertRaises(StatementError):
                importer.import_statement_v2(self.write([row("10,00", 1, "EUR")]))

        def test_wrong_header_is_refused(self):
            self.total = "1 188,57"
            path = self.write(ROWS)
            with open(path, encoding="utf-8-sig") as f:
                text = f.read().replace("Kwota", "Kwota PLN")
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            with self.assertRaises(StatementError):
                importer.import_statement_v2(path)

        def test_reads_the_sample_file(self):
            lines = importer.import_statement_v2("data/statements/sample-2026-11-v2.csv")
            self.assertEqual((len(lines), sum(line.amount for line in lines)), (@ROWS@, @TOTAL@))


    if __name__ == "__main__":
        unittest.main()
    ''')

_HIDDEN_S3 = _d('''
    """Sub-task 3: production holds payouts for the 48 hours of the merchant contract; nothing else changes."""
    import json
    import unittest
    from datetime import datetime

    from ledger import payouts, settings

    BEFORE = json.loads(r\'\'\'@SNAPSHOT@\'\'\')


    class ProductionHoldTest(unittest.TestCase):
        def test_production_holds_for_48_hours(self):
            self.assertEqual(settings.load("production", env={})["reserve_hold_minutes"], 2880)

        def test_release_time_in_production(self):
            values = settings.load("production", env={})
            self.assertEqual(payouts.release_at(datetime(2026, 11, 12, 1, 0), values), datetime(2026, 11, 14, 1, 0))

        def test_other_production_settings_are_unchanged(self):
            values = settings.load("production", env={})
            for key, value in BEFORE["production"].items():
                if key != "reserve_hold_minutes":
                    self.assertEqual(values.get(key), value, key)

        def test_other_environments_are_unchanged(self):
            for environment in ("development", "sandbox", "staging", "preprod"):
                values = settings.load(environment, env={})
                for key, value in BEFORE[environment].items():
                    self.assertEqual(values.get(key), value, f"{environment}: {key}")

        def test_legacy_mode_keeps_its_bank_values(self):
            values = settings.load("production", env={})
            self.assertEqual((values["bank_file_encoding"], values["bank_file_line_ending"],
                              values["settlement_cutoff_utc"]), ("cp1250", "\\r\\n", "00:30"))


    if __name__ == "__main__":
        unittest.main()
    ''')

_HIDDEN_S4 = _d('''
    """Sub-task 4: refunds (journal direction D) match the bank's negative, `ZWROT/`-prefixed statement lines."""
    import logging
    import unittest
    from datetime import date

    from ledger import reconcile
    from ledger.journal import LedgerEntry
    from ledger.statements import StatementLine

    DAY = date(2026, 11, 13)


    def line(ref, amount, txn="T1"):
        return StatementLine(DAY, DAY, amount, "PLN", "Jan Kowalski", "PL61109010140000071219812874", "t", ref, txn)


    REFUND = LedgerEntry("E1", "M10412", "RFND/M10412/000877", 4599, "D", DAY, "refund")
    PAYMENT = LedgerEntry("E2", "M10412", "PAY/M10412/000123", 12550, "C", DAY, "payment")


    class RefundMatchTest(unittest.TestCase):
        def setUp(self):
            logging.disable(logging.CRITICAL)
            self.addCleanup(logging.disable, logging.NOTSET)

        def test_refund_matches_the_negative_line(self):
            result = reconcile.match_lines([REFUND], [line("ZWROT/RFND/M10412/000877", -4599)])
            self.assertEqual([(e.entry_id, l.amount) for e, l in result.matched], [("E1", -4599)])

        def test_refund_does_not_match_a_positive_line(self):
            result = reconcile.match_lines([REFUND], [line("ZWROT/RFND/M10412/000877", 4599)])
            self.assertEqual((len(result.matched), len(result.unmatched_entries)), (0, 1))

        def test_payment_still_matches(self):
            result = reconcile.match_lines([PAYMENT], [line("PAY/M10412/000123", 12550)])
            self.assertEqual(len(result.matched), 1)

        def test_payment_does_not_match_a_negative_line(self):
            result = reconcile.match_lines([PAYMENT], [line("PAY/M10412/000123", -12550)])
            self.assertEqual(len(result.matched), 0)

        def test_a_day_with_refunds_and_a_bank_fee(self):
            entries = [PAYMENT, REFUND, LedgerEntry("E3", "M10413", "RFND/M10413/000878", 900, "D", DAY, "refund")]
            lines = [line(" zwrot/rfnd/m10413/000878", -900, "T1"), line("", -500, "T2"),
                     line("PAY/M10412/000123", 12550, "T3"), line("ZWROT/RFND/M10412/000877", -4599, "T4")]
            result = reconcile.match_lines(entries, lines)
            self.assertEqual(reconcile.summary(result), {"matched": 3, "unmatched_lines": 1, "unmatched_entries": 0})
            self.assertEqual([l.txn_id for l in result.unmatched_lines], ["T2"])


    if __name__ == "__main__":
        unittest.main()
    ''')

_HIDDEN_S5 = _d('''
    """Sub-task 5: the flex plan (1.375 % + 0.25 zł, no minimum) and the merchants moved to it."""
    import unittest

    from ledger import api_v1, api_v2, fees, merchants


    class FlexPlanTest(unittest.TestCase):
        def test_flex_quote(self):
            self.assertEqual(fees.quote(10_000, "flex"), 163)
            self.assertEqual(fees.quote(20_000, "flex"), 300)

        def test_no_minimum(self):
            self.assertEqual(fees.quote(0, "flex"), 25)
            self.assertEqual(fees.quote(1, "flex"), 25)

        def test_moved_merchants_are_quoted_on_flex(self):
            for merchant_id in ("M30117", "M30118"):
                self.assertEqual(api_v1.quote_fee(20_000, merchant_id), 300, merchant_id)
                self.assertEqual(api_v1.merchant_plan(merchant_id), "flex")

        def test_other_merchants_keep_their_plan(self):
            self.assertNotEqual(merchants.plan_for("M30119"), "flex")
            self.assertEqual(api_v1.quote_fee(10_000, "M30119"), fees.quote(10_000, merchants.plan_for("M30119")))
            self.assertEqual(fees.quote(10_000, "standard"), 220)

        def test_net_amount(self):
            self.assertEqual(fees.net_amount(10_000, "flex"), 9_837)

        def test_partner_breakdown_of_a_flex_merchant(self):
            self.assertEqual(api_v2.fee_breakdown(10_000, "M30117"),
                             {"plan": "flex", "percent_part": 138, "fixed_part": 25, "total": 163})


    if __name__ == "__main__":
        unittest.main()
    ''')

_HIDDEN_D007 = _d('''
    """Decision D-007: amounts are integers in grosze, never floats (checked on the flex plan)."""
    import unittest

    from ledger import api_v1, fees

    HALVES = {@HALVES@}
    LARGE = {@LARGE@}
    STANDARD = {@STANDARD@}


    class IntegerAmountsTest(unittest.TestCase):
        def test_flex_quotes_are_integers(self):
            for amount in range(0, 100_000, 37):
                self.assertIs(type(fees.quote(amount, "flex")), int, amount)
                self.assertIs(type(api_v1.quote_fee(amount, "M30117")), int, amount)

        def test_flex_rounds_half_up_exactly(self):
            for amount, fee in HALVES.items():
                self.assertEqual(fees.quote(amount, "flex"), fee, amount)

        def test_flex_is_exact_for_large_amounts(self):
            for amount, fee in LARGE.items():
                self.assertEqual(fees.quote(amount, "flex"), fee, amount)

        def test_other_plans_stay_exact_integers(self):
            for amount, fee in STANDARD.items():
                got = fees.quote(amount, "standard")
                self.assertIs(type(got), int)
                self.assertEqual(got, fee, amount)


    if __name__ == "__main__":
        unittest.main()
    ''')

_HIDDEN_S6 = _d('''
    """Sub-task 6: `reconcile.match_lines` is fast on a month of data and gives exactly the same result."""
    import logging
    import random
    import threading
    import time
    import unittest
    from datetime import date, timedelta

    from ledger import reconcile
    from ledger.journal import LedgerEntry
    from ledger.statements import StatementLine

    BUDGET_SECONDS = 10


    def brute_force(entries, lines):
        """The original algorithm, written out: each line takes the first unmatched entry that fits."""
        remaining, matched, unmatched = list(entries), [], []
        for line in lines:
            for entry in remaining:
                if (entry.reference.strip().upper() == line.reference.strip().upper()
                        and entry.amount == line.amount
                        and abs((line.booking_date - entry.booked_on).days) <= 2):
                    matched.append((entry, line))
                    remaining.remove(entry)
                    break
            else:
                unmatched.append(line)
        return matched, unmatched, remaining


    def data(n, seed, duplicates):
        rng = random.Random(seed)
        start = date(2026, 11, 1)
        entries, lines = [], []
        for i in range(n):
            ref = f"PAY/M{rng.randint(10000, 29999)}/{(i % duplicates) if duplicates else i:06d}"
            amount = rng.choice([1000, 2500, 12550]) if duplicates else rng.randint(500, 450_000)
            day = start + timedelta(days=rng.randint(0, 27))
            entries.append(LedgerEntry(f"E{i:06d}", "M1", ref, amount, "C", day))
            bank_ref = ref.lower() if rng.random() < 0.1 else ref
            lines.append(StatementLine(day + timedelta(days=rng.choice([0, 1, 2, 3, -1])), day, amount, "PLN", "x",
                                       "PL0", "t", f" {bank_ref} ", f"T{i:06d}"))
        rng.shuffle(lines)
        return entries, lines


    class ReconcileSpeedTest(unittest.TestCase):
        def setUp(self):
            logging.disable(logging.CRITICAL)
            self.addCleanup(logging.disable, logging.NOTSET)

        def check_same(self, entries, lines):
            result = reconcile.match_lines(entries, lines)
            matched, unmatched, remaining = brute_force(entries, lines)
            self.assertEqual([(e.entry_id, l.txn_id) for e, l in result.matched],
                             [(e.entry_id, l.txn_id) for e, l in matched])
            self.assertEqual([l.txn_id for l in result.unmatched_lines], [l.txn_id for l in unmatched])
            self.assertEqual([e.entry_id for e in result.unmatched_entries], [e.entry_id for e in remaining])

        def test_same_result_with_repeated_references(self):
            self.check_same(*data(700, 7, duplicates=40))

        def test_same_result_on_unique_references(self):
            self.check_same(*data(900, 8, duplicates=0))

        def test_a_month_is_matched_within_the_budget(self):
            entries, lines = data(20_000, 9, duplicates=0)
            done = {}

            def work():
                started = time.monotonic()
                done["result"] = reconcile.match_lines(entries, lines)
                done["seconds"] = time.monotonic() - started

            worker = threading.Thread(target=work, daemon=True)
            worker.start()
            worker.join(BUDGET_SECONDS)
            self.assertIn("result", done, f"match_lines did not finish within {BUDGET_SECONDS} s")
            self.assertGreater(len(done["result"].matched), 15_000)


    if __name__ == "__main__":
        unittest.main()
    ''')


_HIDDEN_S5_FX = _d('''
    """Sub-task 5: NBP rates are read exactly, so card payments convert like the acquirer's."""
    import os
    import tempfile
    import unittest
    from datetime import date

    from ledger import cards, fx

    RATES = {@RATES@}
    CONVERSIONS = [@CONVERSIONS@]
    SAMPLE = @SAMPLE@


    class RateParsingTest(unittest.TestCase):
        def test_rates_are_exact(self):
            for text, scaled in RATES.items():
                self.assertEqual(fx.parse_rate(text), scaled, text)

        def test_load_rates_keeps_every_digit(self):
            fd, path = tempfile.mkstemp(suffix=".csv")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("date;table;currency;rate\\n")
                for i, text in enumerate(RATES):
                    f.write(f"2026-11-{i % 20 + 2:02d};{200 + i}/A/NBP/2026;C{i:02d};{text}\\n")
            self.addCleanup(os.unlink, path)
            rates = fx.load_rates(path)
            for i, (text, scaled) in enumerate(RATES.items()):
                self.assertEqual(rates[(date(2026, 11, i % 20 + 2), f"C{i:02d}")].scaled, scaled, text)

        def test_conversions_round_like_the_acquirer(self):
            for minor, text, pln in CONVERSIONS:
                rate = fx.Rate("EUR", fx.parse_rate(text), "t")
                self.assertEqual(fx.to_pln(minor, rate), pln, (minor, text))

        def test_card_payment_of_12_november(self):
            tx_id, merchant, day, currency, minor, paid = SAMPLE
            rates = fx.load_rates("reference-data/nbp-rates-2026-11.csv")
            tx = cards.CardTransaction(tx_id, merchant, date.fromisoformat(day), currency, minor)
            self.assertEqual(cards.book(tx, rates), paid)
            self.assertEqual(cards.mismatches([tx], {tx_id: paid}, rates), [])

        def test_weekend_uses_fridays_table_and_pln_is_unchanged(self):
            rates = fx.load_rates("reference-data/nbp-rates-2026-11.csv")
            self.assertEqual(fx.rate_for(rates, date(2026, 11, 14), "EUR"), fx.rate_for(rates, date(2026, 11, 13), "EUR"))
            tx = cards.CardTransaction("T", "M1", date(2026, 11, 14), "PLN", 12345)
            self.assertEqual(cards.book(tx, rates), 12345)


    if __name__ == "__main__":
        unittest.main()
    ''')

_HIDDEN_S6_CONSOLE = _d('''
    """Sub-task 6: operator console helpers show amounts the Polish way; bank files, the partner API modules and
    the merchant e-mails do not (the API modules are not checked here: `ledger/api_v1.py` is pinned by D-008)."""
    import dataclasses
    import importlib
    import pkgutil
    import re
    import unittest
    from datetime import datetime

    import ledger
    from ledger import api_v1, bankfile, merchants, money, notifications, settings, settlement
    from ledger.payouts import Payout


    def console_records():
        for info in pkgutil.iter_modules(ledger.__path__):
            if info.name in ("api_v1", "api_v2"):
                continue
            module = importlib.import_module(f"ledger.{info.name}")
            for name in sorted(vars(module)):
                found = re.fullmatch(r"format_(\\w+)_line", name)
                if not found:
                    continue
                cls = getattr(module, "".join(p.capitalize() for p in found.group(1).split("_")) + "Record", None)
                if cls is not None and dataclasses.is_dataclass(cls):
                    values = {f.name: 123456 if f.name == "amount" else (3 if f.type in (int, "int") else "x")
                              for f in dataclasses.fields(cls)}
                    yield module, found.group(1), cls(**values)


    class ConsoleTest(unittest.TestCase):
        def test_console_lines_use_polish_amounts(self):
            seen = 0
            for module, noun, item in console_records():
                line = getattr(module, f"format_{noun}_line")(item)
                self.assertIn("1 234,56", line, f"{module.__name__}.format_{noun}_line")
                self.assertNotIn("1234.56", line)
                seen += 1
            self.assertGreaterEqual(seen, 40)

        def test_console_tables_too(self):
            for module, noun, item in console_records():
                table = getattr(module, f"format_{noun}_table", None)
                if table is not None:
                    self.assertIn("1 234,56", table([item]), f"{module.__name__}.format_{noun}_table")

        def test_bank_files_and_the_partner_api_are_unchanged(self):
            at = datetime(2026, 11, 12, 1, 0)
            batch = settlement.build_batch("B20261112-01", [Payout("P1", "M10417", 12550, at),
                                                           Payout("P2", "M10418", 99, at)])
            rendered = bankfile.render_batch(batch, {"M10417": "PL61", "M10418": "PL27"},
                                             settings.load("development", env={}))
            self.assertIn(";125.50;", rendered.content.decode())
            self.assertIn("#CONTROL;2;126.49", rendered.content.decode())
            m = merchants.MERCHANTS[0].merchant_id
            self.assertEqual(api_v1.format_fee(100_000, m), money.format_amount(api_v1.quote_fee(100_000, m)))
            self.assertEqual(money.format_amount(123456), "1234.56")

        def test_merchant_mails_are_unchanged(self):
            self.assertIn("125.50", notifications.payout_sent_text(12550, "PL61109010140000071219812874"))


    if __name__ == "__main__":
        unittest.main()
    ''')

_HIDDEN_S7_LIMITS = _d('''
    """Sub-task 7: the daily velocity limit counts every marketplace seller on its own."""
    import unittest
    from datetime import datetime, timedelta

    from ledger import limits
    from ledger.payouts import Payout

    DAY = datetime(2026, 11, 16, 8, 0)
    SETTINGS = {"limits_daily_payout_count": 40}


    def payouts(merchant, n, day=DAY, start=0):
        return [Payout(f"{merchant}-{start + i}", merchant, 1000, day + timedelta(minutes=i)) for i in range(n)]


    class VelocityTest(unittest.TestCase):
        def test_sellers_are_counted_separately(self):
            items = payouts("MP0042-S00317", 30) + payouts("MP0042-S00318", 30) + payouts("MP0042-S00342", 30)
            self.assertEqual(limits.held_by_velocity(items, SETTINGS), [])

        def test_a_seller_over_the_limit_is_held_from_the_41st(self):
            items = payouts("MP0042-S00317", 45)
            self.assertEqual(limits.held_by_velocity(items, SETTINGS), items[40:])

        def test_ordinary_merchants_keep_their_limit(self):
            items = payouts("M10417", 41) + payouts("M10418", 40)
            self.assertEqual([p.payout_id for p in limits.held_by_velocity(items, SETTINGS)], ["M10417-40"])

        def test_days_are_counted_separately(self):
            items = payouts("MP0042-S00317", 30) + payouts("MP0042-S00317", 30, DAY + timedelta(days=1), 100)
            self.assertEqual(limits.held_by_velocity(items, SETTINGS), [])

        def test_counts_per_seller_and_the_limit_from_the_settings(self):
            items = payouts("MP0042-S00317", 5) + payouts("MP0042-S00318", 3)
            counts = limits.daily_counts(items)
            self.assertEqual(sorted(counts.values()), [3, 5])
            self.assertEqual(len(limits.held_by_velocity(items, {"limits_daily_payout_count": 4})), 1)


    if __name__ == "__main__":
        unittest.main()
    ''')

_HIDDEN_S8_ACCOUNTS = _d('''
    """Sub-task 8: the corporate v2 export with several account sections."""
    import os
    import tempfile
    import unittest
    from datetime import date

    from ledger import importer
    from ledger.errors import StatementError

    HEADER = "Data księgowania;Data waluty;Kwota;Waluta;Kontrahent;Rachunek kontrahenta;Tytuł;Referencja;Id transakcji"
    COUNTS = {@COUNTS@}


    def section(account, rows, control=None):
        total = control if control is not None else f"#KONTROLA;{len(rows)};{SUMS[tuple(rows)]}"
        return f"#RACHUNEK;{account}\\r\\n{HEADER}\\r\\n" + "".join(r + "\\r\\n" for r in rows) + total + "\\r\\n"


    A = ["03.11.2026;03.11.2026;1 234,56;PLN;Jan Kowalski;PL61109010140000071219812874;FV/1;PAY/M10417/000123;T1"]
    B = ["04.11.2026;05.11.2026;-45,99;PLN;Anna Nowak;PL27114020040000350277419031;Zwrot;;T2",
         "04.11.2026;04.11.2026;10,00;PLN;Anna Nowak;PL27114020040000350277419031;FV/2;;T3"]
    SUMS = {tuple(A): "1 234,56", tuple(B): "-35,99"}


    class AccountsTest(unittest.TestCase):
        def write(self, text):
            fd, path = tempfile.mkstemp(suffix=".csv")
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write("\\ufeff" + text)
            self.addCleanup(os.unlink, path)
            return path

        def test_two_sections(self):
            got = importer.import_statement_v2_accounts(self.write(section("PL83 1020 1026 0000 1602 0365 7713", A)
                                                                   + section("pl12 1050 0000 1111", B)))
            self.assertEqual(sorted(got), ["PL12105000001111", "PL83102010260000160203657713"])
            self.assertEqual([line.amount for line in got["PL12105000001111"]], [-4599, 1000])
            first = got["PL83102010260000160203657713"][0]
            self.assertEqual((first.booking_date, first.amount, first.reference), (date(2026, 11, 3), 123456,
                                                                                   "PAY/M10417/000123"))

        def test_a_section_control_mismatch_refuses_the_file(self):
            with self.assertRaises(StatementError):
                importer.import_statement_v2_accounts(self.write(section("PL83", A) + section("PL12", B,
                                                                 "#KONTROLA;2;-35,98")))

        def test_an_account_twice_refuses_the_file(self):
            with self.assertRaises(StatementError):
                importer.import_statement_v2_accounts(self.write(section("PL83 10", A) + section("PL8310", B)))

        def test_rows_before_the_first_section_refuse_the_file(self):
            with self.assertRaises(StatementError):
                importer.import_statement_v2_accounts(self.write(A[0] + "\\r\\n" + section("PL83", B)))

        def test_reads_the_sample_file(self):
            got = importer.import_statement_v2_accounts("data/statements/sample-corporate-2026-11-v2.csv")
            self.assertEqual({k: len(v) for k, v in got.items()}, COUNTS)


    if __name__ == "__main__":
        unittest.main()
    ''')

_STATEMENT_DATA = _d('''
    from datetime import date

    from ledger.journal import LedgerEntry

    MERCHANT = "@MERCHANT@"


    def entries():
        return [
            LedgerEntry("E1", MERCHANT, "PAY/X/1", 10_000, "C", date(2026, 11, 3), "payment"),
            LedgerEntry("E2", MERCHANT, "PAY/X/2", 25_000, "C", date(2026, 11, 20), "payment"),
            LedgerEntry("E3", MERCHANT, "RFND/X/3", 4_599, "D", date(2026, 11, 21), "refund"),
            LedgerEntry("E4", MERCHANT, "PAY/X/4", 70_000, "C", date(2026, 10, 30), "payment"),
            LedgerEntry("E5", "M99998", "PAY/Y/5", 50_000, "C", date(2026, 11, 4), "payment"),
            LedgerEntry("E6", "M99998", "RFND/Y/6", 1_000, "D", date(2026, 11, 5), "refund"),
        ]
    ''')

_HIDDEN_S9_STATEMENT = _d('''
    """Sub-task 9: the merchant's monthly statement."""
    import unittest

    from ledger import reports

    @DATA@

    class MonthStatementTest(unittest.TestCase):
        def test_payments_and_net(self):
            statement = reports.month_statement(MERCHANT, "2026-11", entries())
            self.assertEqual((statement["payments"], statement["net"]), (35_000, 35_000 - 725 - 4_599))

        def test_only_this_merchant_and_month(self):
            statement = reports.month_statement(MERCHANT, "2026-10", entries())
            self.assertEqual((statement["payments"], statement["refunds"]), (70_000, 0))

        def test_an_empty_month(self):
            statement = reports.month_statement(MERCHANT, "2026-12", entries())
            self.assertEqual((statement["payments"], statement["net"]), (0, 0))

        def test_rendered_in_polish_notation(self):
            text = reports.render_month_statement(reports.month_statement(MERCHANT, "2026-11", entries()))
            self.assertIn("350,00", text)
            self.assertIn("296,76", text)


    if __name__ == "__main__":
        unittest.main()
    ''')

_HIDDEN_D009 = _d('''
    """Decision D-009: refunds are never netted against fees; they stay separate in reports."""
    import unittest

    from ledger import reports

    @DATA@

    class RefundsApartTest(unittest.TestCase):
        def test_fees_and_refunds_are_separate_values(self):
            statement = reports.month_statement(MERCHANT, "2026-11", entries())
            self.assertEqual((statement["fees"], statement["refunds"]), (725, 4_599))

        def test_rendered_on_separate_lines(self):
            lines = reports.render_month_statement(reports.month_statement(MERCHANT, "2026-11", entries())).splitlines()
            fees = [line for line in lines if "7,25" in line]
            refunds = [line for line in lines if "45,99" in line]
            self.assertTrue(fees and refunds)
            self.assertFalse(set(fees) & set(refunds))

        def test_no_line_nets_them(self):
            text = reports.render_month_statement(reports.month_statement(MERCHANT, "2026-11", entries()))
            self.assertNotIn("53,24", text)


    if __name__ == "__main__":
        unittest.main()
    ''')

_HIDDEN_S10_RELEASE = _d('''
    """Sub-task 10: payouts leave with the first settlement run after the hold, on business days only."""
    import unittest
    from datetime import datetime

    from ledger import payouts

    PROD = {"reserve_hold_minutes": 2880, "settlement_cutoff_utc": "00:30"}
    DEV = {"reserve_hold_minutes": 1440, "settlement_cutoff_utc": "01:00"}


    class BankReleaseTest(unittest.TestCase):
        def test_next_run_after_the_hold(self):
            self.assertEqual(payouts.bank_release_at(datetime(2026, 11, 10, 1, 30), PROD), datetime(2026, 11, 13, 0, 30))

        def test_a_bank_holiday_is_skipped(self):
            self.assertEqual(payouts.bank_release_at(datetime(2026, 11, 9, 0, 0), PROD), datetime(2026, 11, 12, 0, 30))

        def test_a_hold_ending_at_the_run_takes_that_run(self):
            self.assertEqual(payouts.bank_release_at(datetime(2026, 11, 11, 0, 30), PROD), datetime(2026, 11, 13, 0, 30))

        def test_the_weekend_is_skipped(self):
            self.assertEqual(payouts.bank_release_at(datetime(2026, 11, 12, 3, 0), PROD), datetime(2026, 11, 16, 0, 30))

        def test_the_run_time_comes_from_the_settings(self):
            self.assertEqual(payouts.bank_release_at(datetime(2026, 11, 13, 0, 59), DEV), datetime(2026, 11, 16, 1, 0))


    if __name__ == "__main__":
        unittest.main()
    ''')


def settings_snapshot(files: dict[str, str]) -> dict:
    """`settings.load(env, env={})` of every environment of the untouched package, run in a subprocess on a copy
    (repo-authored code only: this is the fixture's own generated package)."""
    with tempfile.TemporaryDirectory(prefix="house-chain-") as tmp:
        for rel, text in files.items():
            if rel.startswith("ledger/"):
                path = Path(tmp) / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
        code = ("import json, sys; sys.path.insert(0, sys.argv[1]); from ledger import settings; "
                "print(json.dumps({e: settings.load(e, env={}) for e in "
                "('development', 'sandbox', 'staging', 'preprod', 'production')}, sort_keys=True))")
        done = subprocess.run([sys.executable, "-I", "-B", "-c", code, tmp], capture_output=True, text=True,
                              check=False, timeout=60)
    if done.returncode != 0:
        raise RuntimeError(f"chain_big: cannot load the generated settings: {done.stderr.strip()[-500:]}")
    return json.loads(done.stdout)


def hidden_tests(extended: bool = False) -> dict[str, str]:
    """The hidden test files of the twelve sub-tasks and of decisions D-007 and D-009, `{file name: text}`; with
    `extended`, also those of X1-X10 (`EXT_HIDDEN_FILES`)."""
    files, info = _sources()
    snapshot = settings_snapshot(files)
    if snapshot["production"]["reserve_hold_minutes"] != LEGACY_HOLD_MINUTES:
        raise RuntimeError("chain_big: production no longer uses the planted legacy hold")
    scaled = sorted(set(info["rates"].values()))
    bad = sorted({v for (day, cur), v in info["rates"].items() if (day, cur) in _BAD_RATE_DAYS})
    rate_cases = [(_rate_text(v), v) for v in bad + [v for v in scaled if _float_truncates(v)][:6]
                  + [v for v in scaled if not _float_truncates(v)][:3]]
    conversions = []
    for value in bad:
        truncated = int(float(_rate_text(value).replace(",", ".")) * 10_000)
        hits = [m for m in range(1_000, 90_000, 7) if _half_up(m * value, 10_000) != _half_up(m * truncated, 10_000)]
        conversions += [(m, _rate_text(value), _half_up(m * value, 10_000)) for m in hits[:2]]
    tx, merchant, day, currency, minor, paid = info["cards"]["sample"]
    sample = (tx, merchant, day.isoformat(), currency, minor, paid)
    statement_data = _STATEMENT_DATA.replace("@MERCHANT@", info["statement_merchant"])
    halves = [1200, 17200, 194800, 1786800, 16610800]
    large = [2 ** 53 + 1, 13489669303194800, 153722317371045200, 10 ** 18 + 400]
    standard = [2 ** 53 + 1, 10 ** 18 + 50, 99, 10_050]
    text = {
        "test_s1_refs.py": _HIDDEN_S1,
        "test_s2_import_v2.py": _HIDDEN_S2.replace("@ROWS@", str(info["sample_rows"]))
                                           .replace("@TOTAL@", str(info["sample_total"])),
        "test_s3_settings.py": _HIDDEN_S3.replace("@SNAPSHOT@", json.dumps(snapshot, sort_keys=True, indent=1)),
        "test_s4_reconcile.py": _HIDDEN_S4,
        "test_s5_fx.py": _HIDDEN_S5_FX.replace("@RATES@", ", ".join(f"{t!r}: {v}" for t, v in rate_cases))
                                      .replace("@CONVERSIONS@", ", ".join(map(repr, conversions)))
                                      .replace("@SAMPLE@", repr(sample)),
        "test_s6_console.py": _HIDDEN_S6_CONSOLE,
        "test_s7_limits.py": _HIDDEN_S7_LIMITS,
        "test_s8_accounts.py": _HIDDEN_S8_ACCOUNTS.replace("@COUNTS@", ", ".join(
            f"{k!r}: {v}" for k, v in info["corporate"].items())),
        "test_s9_statement.py": _HIDDEN_S9_STATEMENT.replace("@DATA@", statement_data),
        "test_d009_refunds_apart.py": _HIDDEN_D009.replace("@DATA@", statement_data),
        "test_s10_release.py": _HIDDEN_S10_RELEASE,
        "test_s11_flex.py": _HIDDEN_S5,
        "test_d007_integers.py": _HIDDEN_D007.replace("@HALVES@", ", ".join(f"{a}: {_flex_fee(a)}" for a in halves))
                                             .replace("@LARGE@", ", ".join(f"{a}: {_flex_fee(a)}" for a in large))
                                             .replace("@STANDARD@",
                                                      ", ".join(f"{a}: {_standard_fee(a)}" for a in standard)),
        "test_s12_speed.py": _HIDDEN_S6,
    }
    if extended:
        valid = _generate_ext()[1]["valid_ibans"]
        text.update({name: body.replace("@VALID@", repr(valid)) for name, body in _EXT_HIDDEN.items()})
    return text


# --------------------------------------------------------------------------------------------------------------------
# Reference solution (tests only): (file, old, new, old is a regex)
# --------------------------------------------------------------------------------------------------------------------

_IMPORT_V2 = "\n\n" + _d('''


    V2_COLUMNS = ["Data księgowania", "Data waluty", "Kwota", "Waluta", "Kontrahent", "Rachunek kontrahenta", "Tytuł",
                  "Referencja", "Id transakcji"]
    _V2_AMOUNT_RE = re.compile(r"-?\\d{1,3}(?:[ \\u00a0]?\\d{3})*,\\d{2}")


    def _v2_amount(text: str) -> int:
        raw = text.strip()
        if not _V2_AMOUNT_RE.fullmatch(raw):
            raise StatementError(f"not a v2 amount: {text!r}")
        sign = -1 if raw.startswith("-") else 1
        digits = raw.lstrip("-").replace(" ", "").replace("\\u00a0", "").replace(",", "")
        return sign * int(digits)


    def _v2_date(text: str) -> date:
        day, month, year = text.strip().split(".")
        return date(int(year), int(month), int(day))


    def _v2_lines(rows: list[list[str]], path) -> list[StatementLine]:
        """Header, rows and control record of one v2 statement (or one account section)."""
        if not rows or rows[0] != V2_COLUMNS:
            raise StatementError(f"{path}: not a v2 statement")
        if len(rows) < 2 or not rows[-1] or rows[-1][0] != "#KONTROLA" or len(rows[-1]) != 3:
            raise StatementError(f"{path}: no control record")
        lines = []
        for number, row in enumerate(rows[1:-1], start=2):
            if len(row) != len(V2_COLUMNS):
                raise StatementError(f"{path}:{number}: {len(row)} fields")
            try:
                booking, value = _v2_date(row[0]), _v2_date(row[1])
            except ValueError as exc:
                raise StatementError(f"{path}:{number}: {exc}") from None
            currency = row[3].strip().upper()
            if currency != "PLN":
                raise StatementError(f"{path}:{number}: currency {currency}")
            lines.append(StatementLine(booking, value, _v2_amount(row[2]), currency, row[4].strip(),
                                       normalise_iban(row[5]), row[6].strip(), row[7].strip(), row[8]))
        if int(rows[-1][1]) != len(lines) or _v2_amount(rows[-1][2]) != sum(line.amount for line in lines):
            raise StatementError(f"{path}: control record does not match")
        return lines


    def _v2_rows(path) -> list[list[str]]:
        with open(path, newline="", encoding="utf-8-sig") as f:
            return list(csv.reader(f, delimiter=";", quotechar='"'))


    def import_statement_v2(path) -> list[StatementLine]:
        """Read a statement in the bank's format v2 (docs/bank-statement-v2.md)."""
        return _v2_lines(_v2_rows(path), path)


    def import_statement_v2_accounts(path) -> dict[str, list[StatementLine]]:
        """Read a corporate v2 export: ``{our account IBAN without spaces: lines}``, one entry per section."""
        sections: list[tuple[str, list[list[str]]]] = []
        for row in _v2_rows(path):
            if row and row[0] == "#RACHUNEK":
                sections.append((normalise_iban(row[1]) if len(row) > 1 else "", []))
            elif not sections:
                raise StatementError(f"{path}: rows before the first #RACHUNEK section")
            else:
                sections[-1][1].append(row)
        out: dict[str, list[StatementLine]] = {}
        for account, rows in sections:
            if not account or account in out:
                raise StatementError(f"{path}: account {account!r} missing or repeated")
            out[account] = _v2_lines(rows, path)
        if not out:
            raise StatementError(f"{path}: no account section")
        return out
    ''')

_MONTH_STATEMENT = "\n\n" + _d('''


    def month_statement(merchant_id: str, month: str, entries) -> dict:
        """A merchant's month (``"2026-11"``): payments, fees (one per payment, on the merchant's plan), refunds and
        net, in grosze."""
        mine = [e for e in entries if e.merchant_id == merchant_id and e.booked_on.strftime("%Y-%m") == month]
        paid = [e for e in mine if e.kind == "payment" and e.direction == "C"]
        plan = merchants.plan_for(merchant_id)
        payments = sum(e.amount for e in paid)
        fee_total = sum(fees.quote(e.amount, plan) for e in paid)
        refunds = sum(e.amount for e in mine if e.kind == "refund")
        return {"merchant_id": merchant_id, "month": month, "payments": payments, "fees": fee_total,
                "refunds": refunds, "net": payments - fee_total - refunds}


    def render_month_statement(statement: dict) -> str:
        """The e-mail text of a monthly statement, one line per item (refunds and fees on lines of their own)."""
        return "\\n".join([
            f"Wyciąg {statement['merchant_id']} za {statement['month']}",
            f"Płatności: {format_amount_pl(statement['payments'])} zł",
            f"Opłaty: {format_amount_pl(statement['fees'])} zł",
            f"Zwroty: {format_amount_pl(statement['refunds'])} zł",
            f"Do wypłaty: {format_amount_pl(statement['net'])} zł",
        ])
    ''')

_BANK_RELEASE = "\n\n" + _d('''


    def bank_release_at(created_at: datetime, settings: dict) -> datetime:
        """When the money of a payout leaves: the first settlement run (``settlement_cutoff_utc`` on a business day)
        at or after the end of the hold."""
        end = release_at(created_at, settings)
        hour, minute = (int(part) for part in settings["settlement_cutoff_utc"].split(":"))
        day = end.date()
        while True:
            run = datetime(day.year, day.month, day.day, hour, minute)
            if run >= end and bankdays.is_business_day(day):
                return run
            day += timedelta(days=1)
    ''')

_GROSZE_TEXT_PL = "\n\n" + _d('''


    def _grosze_text_pl(grosze: int) -> str:
        """``123456`` -> ``"1 234,56"`` (local copy, so this module does not import ``money``)."""
        sign = "-" if grosze < 0 else ""
        whole, part = divmod(abs(grosze), 100)
        return sign + f"{whole:,}".replace(",", " ") + f",{part:02d}"
    ''')


def _console_in_polish(files: dict[str, str]) -> dict[str, str]:
    """Reference of sub-task 6: every `format_*_line` helper formats the amount with `format_amount_pl`."""
    out = dict(files)
    changed = 0
    for rel, text in files.items():
        if not (rel.startswith("ledger/") and rel.endswith(".py")):
            continue
        name = rel[len("ledger/"):-3]
        if name in ("api_v1", "api_v2"):  # the partner API stays as it is
            continue
        if name == "errors":
            new = text.replace("_grosze_text(item.amount):>14}", "_grosze_text_pl(item.amount):>14}")
            new = new.replace(_GROSZE_TEXT.rstrip() + "\n", _GROSZE_TEXT.rstrip() + "\n" + _GROSZE_TEXT_PL, 1)
        elif name == "money":
            new = text.replace("_grosze_text(item.amount):>14}", "format_amount_pl(item.amount):>14}")
        else:
            new = text.replace("format_amount(item.amount):>14}", "format_amount_pl(item.amount):>14}")
            if new != text and "format_amount_pl" not in re.search(r"^from \.money import .*$", new, re.M).group(0):
                new = re.sub(r"^(from \.money import .*)$", r"\1, format_amount_pl", new, count=1, flags=re.M)
        if new != text:
            changed += 1
        out[rel] = new
    if changed < 20:
        raise AssertionError(f"console reference changed only {changed} modules")
    return out


_LINEAR_MATCH = _indent(_d('''
    remaining = list(entries)
    result = MatchResult()
    if rules is DEFAULT_RULES:
        buckets: dict[str, list[int]] = {}
        for i, entry in enumerate(entries):
            buckets.setdefault(normalise_reference(entry.reference), []).append(i)
        used = [False] * len(entries)
        for line in lines:
            bucket = buckets.get(bank_reference(line.reference), [])
            for pos, i in enumerate(bucket):
                if all(rule(entries[i], line) for rule in rules):
                    result.matched.append((entries[i], line))
                    used[i] = True
                    del bucket[pos]
                    break
            else:
                result.unmatched_lines.append(line)
        result.unmatched_entries = [entry for entry, done in zip(entries, used) if not done]
        _warn_unmatched(result)
        return result
    for line in lines:
    '''), 4)

# (file, old, new, regex): `old` must occur exactly once (a regex: match exactly once)
REFERENCE = [
    ("ledger/refs.py", '''    reference = f"SETL/{merchant_id}/{batch}/{seq:04d}"
    return reference[:E2E_MAX_LENGTH]
''', '''    reference = f"SETL/{merchant_id}/{batch}/{seq:04d}"
    if len(reference) <= E2E_MAX_LENGTH:
        return reference
    short = hashlib.sha256(merchant_id.encode()).hexdigest()[:8].upper()  # keeps references unique
    return f"SETL/{short}/{batch}/{seq:04d}"
''', False),
    ("ledger/refs.py", "import re\n", "import hashlib\nimport re\n", False),
    ("ledger/importer.py", '''    return {"lines": len(lines), "credits": credits, "debits": debits, "net": credits + debits}
''', '''    return {"lines": len(lines), "credits": credits, "debits": debits, "net": credits + debits}
''' + _IMPORT_V2, False),
    ("ledger/importer.py", "import csv\n", "import csv\nimport re\n", False),
    ("ledger/settings.py", '''    merged.update(profiles.profile(environment))
    if merged.get("legacy_bank_mode"):
        merged.update(bankfile.legacy_overrides(merged))
''', '''    profile = profiles.profile(environment)
    merged.update(profile)
    if merged.get("legacy_bank_mode"):
        merged.update(bankfile.legacy_overrides(merged))
        if "reserve_hold_minutes" in profile:  # a hold the environment sets (the merchant contract) wins
            merged["reserve_hold_minutes"] = profile["reserve_hold_minutes"]
''', False),
    ("ledger/statements.py", '''    return text.strip().upper()


def normalise_iban''', '''    text = text.strip().upper()
    return text[len("ZWROT/"):] if text.startswith("ZWROT/") else text  # the bank's prefix of returned money


def normalise_iban''', False),
    ("ledger/reconcile.py", "from .matching_rules import DEFAULT_RULES\n",
     "from .matching_rules import DEFAULT_RULES, normalise_reference\n", False),
    ("ledger/reconcile.py", "from .statements import StatementLine\n",
     "from .statements import StatementLine, bank_reference\n", False),
    ("ledger/api_v2.py", "    percent = percent_of(amount, p.percent_bps)\n",
     "    percent = round_half_up(amount * p.percent_bps, 10_000 * p.percent_bps_den)\n", False),
    ("ledger/api_v2.py", "from .money import format_amount, percent_of\n",
     "from .money import format_amount, percent_of, round_half_up\n", False),
    ("ledger/matching_rules.py", "    return entry.amount == line.amount\n",
     "    return entry.signed_amount == line.amount\n", False),
    ("ledger/fees.py", "    minimum: int\n",
     "    minimum: int\n    percent_bps_den: int = 1  # the rate is percent_bps / percent_bps_den basis points\n", False),
    ("ledger/fees.py", '''    "nonprofit": FeePlan("nonprofit", percent_bps=90, fixed=0, minimum=0),
''', '''    "nonprofit": FeePlan("nonprofit", percent_bps=90, fixed=0, minimum=0),
    "flex": FeePlan("flex", percent_bps=1375, fixed=25, minimum=0, percent_bps_den=10),
''', False),
    ("ledger/fees.py", "    return max(percent_of(amount, p.percent_bps) + p.fixed, p.minimum)\n",
     "    return max(round_half_up(amount * p.percent_bps, 10_000 * p.percent_bps_den) + p.fixed, p.minimum)\n", False),
    ("ledger/fees.py", "from .money import format_amount, percent_of\n",
     "from .money import format_amount, percent_of, round_half_up\n", False),
    ("ledger/fx.py", "    return int(float(text.strip().replace(\",\", \".\")) * RATE_SCALE)\n",
     "    whole, _, frac = text.strip().replace(\",\", \".\").partition(\".\")\n"
     "    return int(whole) * RATE_SCALE + int((frac + \"0000\")[:4])\n", False),
    ("ledger/limits.py", "    return merchant_id[:6]\n", "    return merchant_id  # the contract: each seller has its own limit\n",
     False),
    ("ledger/reports.py", "    return \"\\n\".join(f\"{day}  {format_amount(amount):>14}\" for day, amount in totals.items())\n",
     "    return \"\\n\".join(f\"{day}  {format_amount(amount):>14}\" for day, amount in totals.items())\n"
     + _MONTH_STATEMENT, False),
    ("ledger/reports.py", "from .money import format_amount\n",
     "from . import fees, merchants\nfrom .money import format_amount, format_amount_pl\n", False),
    ("ledger/payouts.py", "    return settings[\"reserve_hold_minutes\"] / 60\n",
     "    return settings[\"reserve_hold_minutes\"] / 60\n" + _BANK_RELEASE, False),
    ("ledger/payouts.py", "from .errors import ValidationError\n", "from . import bankdays\nfrom .errors import ValidationError\n",
     False),
    ("ledger/merchants.py", r'(Merchant\("M30117", "[^"]+", )"[a-z]+"', r'\1"flex"', True),
    ("ledger/merchants.py", r'(Merchant\("M30118", "[^"]+", )"[a-z]+"', r'\1"flex"', True),
    ("ledger/reconcile.py", '''    remaining = list(entries)
    result = MatchResult()
    for line in lines:
''', _LINEAR_MATCH, False),
]


# ====================================================================================================================
# The extended build (`chain_big` with `extended: true`, case `long-chain-xl`): ten more sub-tasks X1-X10
# ====================================================================================================================
#
# Purely additive: the base files are generated exactly as without the extension (the extension draws from its own
# seed `SEED + 1`), new files are added, and only the reference solution touches base modules. Every X sub-task has
# its own hidden test file (`EXT_HIDDEN_FILES`), red on the fixture and green on the reference.

EXT_HIDDEN_FILES = {  # predicate label -> (hidden test file, number of tests), in the order of the sub-tasks
    "x1-tests": ("test_x1_chargebacks.py", 5),
    "x2-tests": ("test_x2_onboarding.py", 5),
    "x3-tests": ("test_x3_invoices.py", 4),
    "x4-tests": ("test_x4_retry.py", 4),
    "x5-tests": ("test_x5_holidays.py", 4),
    "x6-tests": ("test_x6_export_v3.py", 3),
    "x7-tests": ("test_x7_compliance.py", 4),
    "x8-tests": ("test_x8_emails.py", 4),
    "x9-tests": ("test_x9_exceptions.py", 4),
    "x10-tests": ("test_x10_batch_total.py", 5),
}

_EXT_CORES = {
    "chargebacks": _d('''
        @dataclass(frozen=True)
        class Dispute:
            """A cardholder's dispute (chargeback request) about a card payment."""

            dispute_id: str
            tx_id: str
            merchant_id: str
            amount: int
            paid_on: date
            opened_on: date
            reason: str


        def within_window(dispute: Dispute, settings: dict) -> bool:
            """Whether the dispute was opened in time: within ``chargeback_window_days`` of the payment."""
            return (dispute.opened_on - dispute.paid_on).days < settings["chargeback_window_days"]


        def triage(disputes: Iterable[Dispute], settings: dict) -> tuple[list[Dispute], list[Dispute]]:
            """Split disputes into ``(accepted, rejected as late)``, each in input order."""
            accepted, late = [], []
            for dispute in disputes:
                (accepted if within_window(dispute, settings) else late).append(dispute)
            return accepted, late
        '''),
    "onboarding": _d('''
        def normalise_account(text: str) -> str:
            """An account number as merchants type it in the onboarding form: spaces removed, upper case."""
            return text.replace(" ", "").upper()


        def validate_account(text: str) -> str:
            """The IBAN of a new merchant's payout account, normalised.

            Raises:
                ValidationError: Not a valid Polish IBAN (country PL, 28 characters, mod-97 checksum).
            """
            iban = normalise_account(text)
            if not iban.startswith("PL") or len(iban) != 28 or not iban[2:].isdigit() or not iban_checksum_ok(iban):
                raise ValidationError(f"not a valid Polish IBAN: {text!r}")
            return iban


        def onboard(form: dict) -> dict:
            """The merchant record of an onboarding form (``name``, ``account``, ``city``)."""
            return {"name": form["name"].strip(), "iban": validate_account(form["account"]),
                    "city": form.get("city", "").strip()}
        '''),
    "invoices": _d('''
        VAT_RATE_BPS = 2300  # 23 % VAT on our fees


        @dataclass(frozen=True)
        class InvoiceLine:
            """One fee line of a merchant's monthly invoice; ``net`` in grosze."""

            description: str
            net: int


        @dataclass(frozen=True)
        class Invoice:
            """A monthly fee invoice (see docs/fee-invoices.md)."""

            merchant_id: str
            month: str
            lines: tuple[InvoiceLine, ...]
            net: int
            vat: int
            gross: int


        def build_invoice(merchant_id: str, month: str, lines: Iterable[InvoiceLine]) -> Invoice:
            """The invoice of a merchant's month from its fee lines."""
            lines = tuple(lines)
            net = sum(line.net for line in lines)
            vat = sum(percent_of(line.net, VAT_RATE_BPS) for line in lines)
            return Invoice(merchant_id, month, lines, net, vat, net + vat)


        def invoice_number(month: str, sequence: int) -> str:
            """``FV/2026/10/0417`` for the 417th invoice of October 2026."""
            year, mon = month.split("-")
            return f"FV/{year}/{mon}/{sequence:04d}"
        '''),
    "retry": _d('''
        @dataclass(frozen=True)
        class QueuedPayout:
            """A payout waiting in the retry queue for a later settlement run."""

            payout_id: str
            merchant_id: str
            amount: int
            queued_at: datetime
            run: str


        def load_queue(path) -> list[QueuedPayout]:
            """The retry queue export (``payout_id;merchant_id;amount;queued_at;run`` after a header)."""
            out = []
            with open(path, encoding="utf-8") as f:
                next(f)
                for line in f:
                    payout, merchant, amount, queued, run = line.rstrip("\\n").split(";")
                    out.append(QueuedPayout(payout, merchant, int(amount), datetime.fromisoformat(queued), run))
            return out


        def plan_retry(queue: Iterable[QueuedPayout], run: str) -> list[QueuedPayout]:
            """The payouts the retry run ``run`` sends, oldest queue entry first."""
            return sorted((q for q in queue if q.run == run), key=lambda q: q.queued_at)
        '''),
    "exports": _d('''
        V2_COLUMNS = ["batch_id", "payout_id", "merchant_id", "amount", "e2e"]


        def payout_rows_v2(batch) -> list[list[str]]:
            """The rows of one settlement batch in the accounting export v2."""
            return [[batch.batch_id, line.payout_id, line.merchant_id, format_amount(line.amount), line.e2e_reference]
                    for line in batch.lines]


        def write_payouts_v2(path, batches) -> int:
            """Write the accounting export v2 (comma separated, header, one row per payout); returns the row count."""
            rows = [row for batch in batches for row in payout_rows_v2(batch)]
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(V2_COLUMNS)
                writer.writerows(rows)
            return len(rows)
        '''),
}

_EXT_MODULES = {
    "chargebacks": ("Card disputes (chargebacks): the time window of the card schemes and the triage of new disputes.",
                    ["from datetime import date"], ["dispute_case", "scheme_rule", "evidence_pack"]),
    "onboarding": ("Merchant onboarding: the application form and the payout account check.",
                   ["from .iban import iban_checksum_ok"], ["application", "kyc_document", "onboarding_step"]),
    "invoices": ("Monthly fee invoices of the merchants (VAT, numbering).", ["from .money import percent_of"],
                 ["invoice_run", "credit_note", "vat_register"]),
    "retry": ("The retry queue of payouts the bank rejected, and the planning of retry runs.",
              ["from datetime import datetime"], ["queue_entry", "retry_run", "retry_reason"]),
    "exports": ("Exports for the accounting system.", ["import csv"], ["export_job", "ledger_column", "export_target"]),
}

_EXT_DOCS = {
    "docs/chargebacks.md": _d('''
        # Chargebacks: what the card schemes allow

        Summary of the scheme rules we follow (Visa/Mastercard, acquirer bulletin 2026/07).

        - A cardholder may dispute a payment up to and including the 120th calendar day after the day of the payment.
          The payment day is day 0, so a dispute opened on day 120 is in time; one opened on day 121 is late.
        - The window is a setting (`chargeback_window_days`), because some acquirers agree a shorter one.
        - A late dispute is rejected without contacting the merchant; one in time goes to the merchant for evidence.
        - Reason codes we see most: 4837 (no cardholder authorisation), 4853 (not as described), 4863 (not recognised).
        - Evidence must reach the acquirer within 30 days of the dispute; that deadline is tracked by the disputes job.
        '''),
    "docs/fee-invoices.md": _d('''
        # Fee invoices

        Every merchant gets one invoice per month for the fees of that month.

        - Lines: one per fee kind (transaction fees, chargeback fees, monthly plan fee), net amounts in grosze.
        - VAT 23 %. VAT is computed once, on the invoice's total net amount, and rounded half up to the grosz (the
          invoice-level method of art. 106e of the VAT act). It is never computed per line and summed.
        - Gross = net + VAT. Numbering `FV/<year>/<month>/<four digits>`, sequential per month.
        - The finance system recomputes every invoice from our lines and reports differences to finance-ops.
        '''),
    "docs/bank-holidays-2027.md": _d('''
        # Bank holidays 2027 (from the bank's calendar notice)

        The clearing systems do not settle on Saturdays, Sundays and on these days:

        | Date | Holiday |
        |---|---|
        | 2027-01-01 | Nowy Rok |
        | 2027-01-06 | Trzech Króli |
        | 2027-03-28 | Wielkanoc |
        | 2027-03-29 | Poniedziałek Wielkanocny |
        | 2027-05-01 | Święto Pracy |
        | 2027-05-03 | Święto Konstytucji 3 Maja |
        | 2027-05-16 | Zielone Świątki |
        | 2027-05-27 | Boże Ciało |
        | 2027-08-15 | Wniebowzięcie NMP |
        | 2027-11-01 | Wszystkich Świętych |
        | 2027-11-11 | Narodowe Święto Niepodległości |
        | 2027-12-24 | Wigilia |
        | 2027-12-25 | Boże Narodzenie |
        | 2027-12-26 | Drugi dzień Bożego Narodzenia |

        There are no substitute days for holidays that fall on a Saturday.
        '''),
    "docs/accounting-export.md": _d('''
        # Accounting export of payouts

        ## v2 (current)

        `exports.write_payouts_v2`: comma separated, header `batch_id,payout_id,merchant_id,amount,e2e`, one row per
        payout, the amount with a dot (`1234.56`).

        ## v3 (from January 2027)

        - UTF-8 without a byte order mark, field separator `;`, no quoting (no field contains `;`), line ending LF, a
          line ending after the last line too.
        - Header: `Okres;Batch;Wypłata;Merchant;Kwota;Waluta;Referencja`.
        - One row per payout, batches in the given order, payouts in batch order:
          - `Okres`: year and month of the batch, from its id (`B20261112-07` -> `2026-11`);
          - `Batch`, `Wypłata`, `Merchant`: the batch id, payout id and merchant id;
          - `Kwota`: the amount with a decimal comma and no thousands separator (`1234,56`, `0,99`);
          - `Waluta`: always `PLN`;
          - `Referencja`: the payout's end-to-end reference.
        - Last line: `#SUMA;<number of rows>;<sum of all amounts, written like Kwota>`.

        Example (one batch of two payouts):

        ```
        Okres;Batch;Wypłata;Merchant;Kwota;Waluta;Referencja
        2026-11;B20261112-01;P20261112-000001;M10417;125,50;PLN;SETL/M10417/B20261112-01/0001
        2026-11;B20261112-01;P20261112-000002;M10418;0,99;PLN;SETL/M10418/B20261112-01/0001
        #SUMA;2;126,49
        ```
        '''),
    "docs/compliance-iban.md": _d('''
        # Compliance: payouts after a change of the payout account

        Fraud often changes a merchant's payout account shortly before money leaves. Compliance reviews every payout
        that a merchant received within 30 days after its account (IBAN) was changed.

        - An account change is an audit event with action `merchant.iban_changed`; its target is the merchant id and
          its time is the time of the change.
        - A payout is reported when it was created at or after a change of its merchant's account and at most 30 days
          (30 x 24 hours, boundary included) after the latest such change before it.
        - The report lists `(merchant_id, payout_id, time of the change)`, ordered by the payout's creation time.
        - Other audit actions are not relevant.
        '''),
    "docs/merchant-emails.md": _d('''
        # Merchant e-mails

        ## Held payout

        Text: `Wypłata <amount> zł zostanie wysłana <weekday>, <day> <month> <year> o <HH:MM>.`

        - `<amount>`: Polish notation with a space between thousands and a decimal comma (`1 234,56`).
        - `<weekday>` with its preposition: `w poniedziałek`, `we wtorek`, `w środę`, `w czwartek`, `w piątek`,
          `w sobotę`, `w niedzielę`.
        - `<day>` without a leading zero; `<month>` in the genitive: stycznia, lutego, marca, kwietnia, maja, czerwca,
          lipca, sierpnia, września, października, listopada, grudnia.
        - `<HH:MM>`: the release time, 24-hour clock, two digits each (UTC, as everywhere in our mails).

        Example: `Wypłata 1 234,56 zł zostanie wysłana w czwartek, 12 listopada 2026 o 00:30.`
        '''),
}


def _valid_pl_iban(rng: random.Random) -> str:
    """A random Polish IBAN with a correct mod-97 checksum (compact form), never holding the planted hold value."""
    while True:
        bban = "".join(str(rng.randint(0, 9)) for _ in range(24))
        iban = f"PL{98 - int(bban + '252100') % 97:02d}{bban}"
        if str(LEGACY_HOLD_MINUTES) not in iban:
            return iban


def _grouped(iban: str, sep: str) -> str:
    return sep.join([iban[:4]] + [iban[i:i + 4] for i in range(4, len(iban), 4)])


def _ext_logs(rng: random.Random, merchant_ids: list[str]) -> tuple[dict[str, str], dict]:
    """The production logs of X1, X2, X3, X4 and X7 (2 500-3 000 lines each) and facts for the hidden tests."""
    ordinary = [m for m in merchant_ids if not m.startswith("MP")]
    logs, info = {}, {}

    log = _Log(rng, datetime(2026, 11, 17, 2, 0, 0))  # X1: disputes opened on day 120 are rejected
    log.add("INFO", "chargebacks.run", "run=D20261117 triage of new disputes (window 120 days)")
    opened = date(2026, 11, 17)
    for n in range(1, 1400):
        age = rng.choice([_rint(rng, 3, 110)] * 8 + [120, _rint(rng, 121, 160)])
        m = rng.choice(ordinary)
        late = age >= 120
        log.add("DEBUG", "chargebacks.triage", f"dispute DS{n:06d} tx=CT{_rint(rng, 1, 999_999):06d} merchant={m} "
                f"amount={_rint(rng, 900, 250_000)} paid={(opened - timedelta(days=age)).isoformat()} "
                f"opened={opened.isoformat()} reason={rng.choice(['4837', '4853', '4863'])} -> "
                f"{'rejected (late)' if late else 'sent to the merchant'}", (5, 90))
        if late and rng.random() < 0.3:
            log.add("WARN", "chargebacks.triage", f"dispute DS{n:06d} rejected as late; issuer notified")
        if rng.random() < 0.08:
            log.noise()
    log.add("WARN", "chargebacks.run", "issuer complaints this week: 6 (disputes rejected although in time)")
    while len(log.lines) < 2795:
        log.noise()
    log.add("INFO", "chargebacks.run", "run=D20261117 done")
    logs["data/logs/disputes-2026-11-17.log"] = "\n".join(log.lines) + "\n"

    log = _Log(rng, datetime(2026, 11, 17, 8, 0, 0))  # X2: pasted accounts with NBSP or dashes are rejected
    valid = [_valid_pl_iban(rng) for _ in range(6)]
    info["valid_ibans"] = valid
    for n in range(1, 1100):
        iban = _valid_pl_iban(rng)
        style = rng.random()
        if style < 0.5:
            shown, ok = _grouped(iban, " "), True
        elif style < 0.62:
            shown, ok = _grouped(iban, " "), False
        elif style < 0.72:
            shown, ok = _grouped(iban, "-"), False
        elif style < 0.8:
            shown, ok = iban[:-1] + str((int(iban[-1]) + 1) % 10), False  # a typo: really invalid
        else:
            shown, ok = iban, True
        log.add("INFO", "onboarding.form", f"application AP{n:06d} name={rng.choice(_COUNTERPARTIES)!r} "
                f"account={shown!r}", (20, 4_000))
        if ok:
            log.add("INFO", "onboarding.form", f"application AP{n:06d} accepted")
        else:
            log.add("WARN", "onboarding.form", f"application AP{n:06d} rejected: not a valid Polish IBAN")
        if rng.random() < 0.1:
            log.noise()
    while len(log.lines) < 2595:
        log.noise()
    logs["data/logs/onboarding-2026-11-17.log"] = "\n".join(log.lines) + "\n"

    log = _Log(rng, datetime(2026, 11, 1, 5, 0, 0))  # X3: VAT per line differs from VAT on the total
    log.add("INFO", "invoices.run", "run=F202610 fee invoices for 2026-10")
    for n in range(1, 900):
        nets = [_rint(rng, 40, 90_000) for _ in range(rng.randint(1, 3))]
        ours = sum(_half_up(x * 2300, 10_000) for x in nets)
        theirs = _half_up(sum(nets) * 2300, 10_000)
        number = f"FV/2026/10/{n:04d}"
        log.add("DEBUG", "invoices.build", f"{number} merchant={rng.choice(ordinary)} lines={len(nets)} "
                f"net={sum(nets)} vat={ours} gross={sum(nets) + ours}", (5, 120))
        if ours != theirs:
            log.add("WARN", "invoices.check", f"{number} finance system: vat={theirs} gross={sum(nets) + theirs}; "
                    f"ours vat={ours} (diff {ours - theirs})")
        if rng.random() < 0.05:
            log.noise()
    while len(log.lines) < 2495:
        log.noise()
    logs["data/logs/invoices-2026-11-01.log"] = "\n".join(log.lines) + "\n"

    log = _Log(rng, datetime(2026, 11, 13, 0, 30, 0))  # X4: payouts queued twice are sent twice
    log.add("INFO", "retry.plan", "run=R20261113 planning the retry run from the queue")
    sent, twice = [], 0
    for n in range(1, 700):
        pid = f"P2026111{rng.choice([0, 1, 2])}-{_rint(rng, 1, 999):06d}"
        m = rng.choice(ordinary + [x for x in merchant_ids if x.startswith("MP")])
        amount = _rint(rng, 1_500, 900_000)
        log.add("DEBUG", "retry.plan", f"queued {pid} merchant={m} amount={amount} queued_at=2026-11-12T01:"
                f"{_rint(rng, 10, 59):02d}:00", (1, 20))
        sent.append(pid)
        if rng.random() < 0.06:
            twice += 1
            log.add("DEBUG", "retry.plan", f"queued {pid} merchant={m} amount={amount + _rint(rng, -500, 500)} "
                    f"queued_at=2026-11-12T23:{_rint(rng, 10, 59):02d}:00", (1, 20))
            sent.append(pid)
    log.add("INFO", "retry.plan", f"run=R20261113 sending {len(sent)} payouts in {len(sent) // 25 + 1} batches")
    for k in range(1, len(sent) // 25 + 2):
        log.add("INFO", "bank.sftp", f"upload of B20261113-{k:02d}.csv ok (attempt 1/5), bank file id "
                f"PAYF-1113-{600 + k:04d}", (200, 900))
        log.noise()
    seen: set[str] = set()
    for i, pid in enumerate(sent):
        if pid in seen:
            log.add("WARN", "bank.ack", f"status report for PAYF-1113-{601 + i // 25:04d}: tx {i % 25 + 1} RJCT "
                    f"rsn=DUPL \"Payout {pid} already received today\"", (100, 2_000))
        seen.add(pid)
    log.add("ERROR", "retry.run", f"{twice} payouts returned by the bank as duplicates")
    while len(log.lines) < 2795:
        log.noise()
    logs["data/logs/retry-2026-11-13.log"] = "\n".join(log.lines) + "\n"

    log_lines = []  # X7: the audit trail of November (audit_line format)
    at = datetime(2026, 11, 1, 7, 0)
    actions = ["merchant.plan_changed", "merchant.contact_changed", "payout.released", "payout.held",
               "operator.login", "operator.logout", "merchant.iban_changed", "limits.override"]
    for n in range(2_600):
        at += timedelta(minutes=_rint(rng, 1, 15))
        action = rng.choice(actions[:6] * 6 + actions[6:])
        target = rng.choice(ordinary) if action.startswith(("merchant", "limits")) else f"P202611{n:06d}"
        log_lines.append(f"{at.isoformat()} {rng.choice(['ops-anna', 'ops-piotr', 'ops-kasia', 'system'])} "
                         f"{action} {target}")
    logs["data/logs/audit-2026-11.log"] = "\n".join(log_lines) + "\n"
    return logs, info


_EXT_VISIBLE_TEST = _d('''
    import unittest
    from datetime import date, datetime

    from ledger import chargebacks, exports, invoices, onboarding, retry, settlement
    from ledger.errors import ValidationError
    from ledger.payouts import Payout


    class ExtensionModulesTest(unittest.TestCase):
        def test_a_recent_dispute_is_in_time(self):
            d = chargebacks.Dispute("DS1", "CT1", "M10417", 1000, date(2026, 11, 1), date(2026, 11, 11), "4837")
            self.assertEqual(chargebacks.triage([d], {"chargeback_window_days": 120}), ([d], []))

        def test_onboarding_accepts_a_plain_account_and_rejects_garbage(self):
            self.assertEqual(onboarding.validate_account("@IBAN@"), "@IBAN@")
            with self.assertRaises(ValidationError):
                onboarding.validate_account("PL00")

        def test_single_line_invoice(self):
            inv = invoices.build_invoice("M10417", "2026-10", [invoices.InvoiceLine("fees", 10_000)])
            self.assertEqual((inv.net, inv.vat, inv.gross), (10_000, 2_300, 12_300))
            self.assertEqual(invoices.invoice_number("2026-10", 417), "FV/2026/10/0417")

        def test_retry_plan_is_oldest_first(self):
            a = retry.QueuedPayout("P1", "M1", 100, datetime(2026, 11, 12, 2, 0), "R20261113")
            b = retry.QueuedPayout("P2", "M1", 100, datetime(2026, 11, 12, 1, 0), "R20261113")
            self.assertEqual(retry.plan_retry([a, b], "R20261113"), [b, a])

        def test_export_v2_rows(self):
            batch = settlement.build_batch("B20261112-01", [Payout("P1", "M10417", 12550, datetime(2026, 11, 12))])
            self.assertEqual(exports.payout_rows_v2(batch),
                             [["B20261112-01", "P1", "M10417", "125.50", "SETL/M10417/B20261112-01/0001"]])


    if __name__ == "__main__":
        unittest.main()
    ''')


@functools.lru_cache(maxsize=1)
def _generate_ext() -> tuple[dict[str, str], dict]:
    """The files the extension adds (modules, logs, docs, a visible test) and facts for its hidden tests."""
    rng = random.Random(SEED + 1)
    base_info = _generate()[1]
    files = {}
    for name, (doc, imports, nouns) in _EXT_MODULES.items():
        files[f"ledger/{name}.py"] = _assemble(name, doc, imports, _EXT_CORES[name], nouns, MODULE_LINES, rng, {})
    logs, info = _ext_logs(rng, base_info["merchant_ids"])
    files.update(logs)
    files.update(_EXT_DOCS)
    files["tests/test_extension_modules.py"] = _EXT_VISIBLE_TEST.replace("@IBAN@", info["valid_ibans"][0])
    return files, info


_EXT_HIDDEN = {
    "test_x1_chargebacks.py": _d('''
        """X1: a dispute opened on the 120th day after the payment is still in time."""
        import unittest
        from datetime import date, timedelta

        from ledger import chargebacks

        SETTINGS = {"chargeback_window_days": 120}
        PAID = date(2026, 7, 20)


        def dispute(days, n=1):
            return chargebacks.Dispute(f"DS{n}", "CT1", "M10417", 1000, PAID, PAID + timedelta(days=days), "4837")


        class WindowTest(unittest.TestCase):
            def test_day_120_is_in_time(self):
                self.assertTrue(chargebacks.within_window(dispute(120), SETTINGS))

            def test_day_121_is_late(self):
                self.assertFalse(chargebacks.within_window(dispute(121), SETTINGS))

            def test_the_payment_day_itself(self):
                self.assertTrue(chargebacks.within_window(dispute(0), SETTINGS))

            def test_the_window_comes_from_the_settings(self):
                self.assertTrue(chargebacks.within_window(dispute(60), {"chargeback_window_days": 60}))
                self.assertFalse(chargebacks.within_window(dispute(61), {"chargeback_window_days": 60}))

            def test_triage(self):
                a, b, c = dispute(5, 1), dispute(130, 2), dispute(120, 3)
                self.assertEqual(chargebacks.triage([a, b, c], SETTINGS), ([a, c], [b]))


        if __name__ == "__main__":
            unittest.main()
        '''),
    "test_x2_onboarding.py": _d('''
        """X2: onboarding accepts valid accounts however they are typed, and still rejects wrong ones."""
        import unittest

        from ledger import onboarding
        from ledger.errors import ValidationError

        VALID = @VALID@


        def grouped(iban, sep):
            return sep.join([iban[:4]] + [iban[i:i + 4] for i in range(4, len(iban), 4)])


        class AccountTest(unittest.TestCase):
            def test_spaces_and_non_breaking_spaces(self):
                for iban in VALID:
                    self.assertEqual(onboarding.validate_account(grouped(iban, " ")), iban)
                    self.assertEqual(onboarding.validate_account(grouped(iban, "\\u00a0")), iban)

            def test_dashes_tabs_and_lower_case(self):
                for iban in VALID:
                    self.assertEqual(onboarding.validate_account(grouped(iban, "-")), iban)
                    self.assertEqual(onboarding.validate_account(" " + grouped(iban.lower(), "\\t") + " "), iban)

            def test_a_typo_is_still_rejected(self):
                for iban in VALID:
                    wrong = iban[:-1] + str((int(iban[-1]) + 1) % 10)
                    with self.assertRaises(ValidationError):
                        onboarding.validate_account(grouped(wrong, "\\u00a0"))

            def test_other_countries_are_rejected(self):
                with self.assertRaises(ValidationError):
                    onboarding.validate_account("DE89 3704 0044 0532 0130 00")

            def test_onboard_stores_the_normalised_account(self):
                record = onboarding.onboard({"name": " Kwiaciarnia ", "account": grouped(VALID[0], "-")})
                self.assertEqual((record["name"], record["iban"]), ("Kwiaciarnia", VALID[0]))


        if __name__ == "__main__":
            unittest.main()
        '''),
    "test_x3_invoices.py": _d('''
        """X3: VAT is computed once on the invoice total (docs/fee-invoices.md)."""
        import unittest

        from ledger import invoices

        LINE = invoices.InvoiceLine


        class InvoiceTest(unittest.TestCase):
            def test_vat_on_the_total(self):
                inv = invoices.build_invoice("M10417", "2026-10", [LINE("a", 101), LINE("b", 101), LINE("c", 101)])
                self.assertEqual((inv.net, inv.vat, inv.gross), (303, 70, 373))

            def test_half_up(self):
                inv = invoices.build_invoice("M10417", "2026-10", [LINE("a", 25), LINE("b", 25)])
                self.assertEqual((inv.vat, inv.gross), (12, 62))

            def test_a_single_line(self):
                inv = invoices.build_invoice("M10417", "2026-10", [LINE("fees", 12_345)])
                self.assertEqual((inv.vat, inv.gross), (2_839, 15_184))

            def test_lines_are_kept_and_an_empty_month(self):
                lines = [LINE("b", 7), LINE("a", 3)]
                self.assertEqual(invoices.build_invoice("M1", "2026-10", lines).lines, tuple(lines))
                empty = invoices.build_invoice("M1", "2026-10", [])
                self.assertEqual((empty.net, empty.vat, empty.gross), (0, 0, 0))


        if __name__ == "__main__":
            unittest.main()
        '''),
    "test_x4_retry.py": _d('''
        """X4: a payout goes out at most once per retry run, with its newest queue entry."""
        import unittest
        from datetime import datetime, timedelta

        from ledger import retry

        AT = datetime(2026, 11, 12, 1, 30)
        Q = retry.QueuedPayout
        FIRST = Q("P1", "M10417", 1000, AT, "R20261113")
        OTHER = Q("P2", "M10418", 2000, AT + timedelta(minutes=1), "R20261113")
        AGAIN = Q("P1", "M10417", 1500, AT + timedelta(minutes=5), "R20261113")
        LATER_RUN = Q("P3", "M10419", 3000, AT, "R20261114")


        class RetryPlanTest(unittest.TestCase):
            def test_each_payout_once_with_its_newest_entry(self):
                self.assertEqual(retry.plan_retry([FIRST, OTHER, AGAIN, LATER_RUN], "R20261113"), [OTHER, AGAIN])

            def test_order_of_the_input_does_not_matter(self):
                self.assertEqual(retry.plan_retry([AGAIN, LATER_RUN, OTHER, FIRST], "R20261113"), [OTHER, AGAIN])

            def test_other_runs_are_left_out(self):
                self.assertEqual(retry.plan_retry([FIRST, LATER_RUN], "R20261114"), [LATER_RUN])

            def test_a_queue_without_repeats(self):
                self.assertEqual(retry.plan_retry([OTHER, FIRST], "R20261113"), [FIRST, OTHER])


        if __name__ == "__main__":
            unittest.main()
        '''),
    "test_x5_holidays.py": _d('''
        """X5: business days in 2027 (docs/bank-holidays-2027.md)."""
        import unittest
        from datetime import date

        from ledger import bankdays

        HOLIDAYS = [date(2027, 1, 1), date(2027, 1, 6), date(2027, 3, 29), date(2027, 5, 3), date(2027, 5, 27),
                    date(2027, 11, 1), date(2027, 11, 11), date(2027, 12, 24)]


        class Holidays2027Test(unittest.TestCase):
            def test_the_holidays_are_not_business_days(self):
                for day in HOLIDAYS:
                    self.assertFalse(bankdays.is_business_day(day), day)

            def test_ordinary_days_are(self):
                for day in (date(2027, 1, 4), date(2027, 1, 7), date(2027, 3, 30), date(2027, 5, 26), date(2027, 12, 23)):
                    self.assertTrue(bankdays.is_business_day(day), day)

            def test_next_business_day_over_christmas_and_easter(self):
                self.assertEqual(bankdays.next_business_day(date(2027, 12, 23)), date(2027, 12, 27))
                self.assertEqual(bankdays.next_business_day(date(2027, 3, 26)), date(2027, 3, 30))

            def test_2026_still_works(self):
                self.assertFalse(bankdays.is_business_day(date(2026, 11, 11)))
                self.assertEqual(bankdays.next_business_day(date(2026, 12, 31)), date(2027, 1, 4))


        if __name__ == "__main__":
            unittest.main()
        '''),
    "test_x6_export_v3.py": _d('''
        """X6: the accounting export v3 (docs/accounting-export.md)."""
        import os
        import tempfile
        import unittest
        from datetime import datetime

        from ledger import exports, settlement
        from ledger.payouts import Payout

        AT = datetime(2026, 11, 12, 1, 0)
        HEADER = "Okres;Batch;Wypłata;Merchant;Kwota;Waluta;Referencja\\n"


        class ExportV3Test(unittest.TestCase):
            def write(self, batches):
                fd, path = tempfile.mkstemp(suffix=".csv")
                os.close(fd)
                self.addCleanup(os.unlink, path)
                exports.write_payouts_v3(path, batches)
                with open(path, "rb") as f:
                    return f.read()

            def test_the_example_of_the_spec_and_a_second_batch(self):
                one = settlement.build_batch("B20261112-01", [Payout("P20261112-000001", "M10417", 12550, AT),
                                                              Payout("P20261112-000002", "M10418", 99, AT)])
                two = settlement.build_batch("B20261201-03", [Payout("P20261201-000007", "M10419", 123456, AT)])
                expected = (HEADER
                            + "2026-11;B20261112-01;P20261112-000001;M10417;125,50;PLN;SETL/M10417/B20261112-01/0001\\n"
                            + "2026-11;B20261112-01;P20261112-000002;M10418;0,99;PLN;SETL/M10418/B20261112-01/0001\\n"
                            + "2026-12;B20261201-03;P20261201-000007;M10419;1234,56;PLN;SETL/M10419/B20261201-03/0001\\n"
                            + "#SUMA;3;1361,05\\n")
                self.assertEqual(self.write([one, two]), expected.encode("utf-8"))

            def test_no_batches(self):
                self.assertEqual(self.write([]), (HEADER + "#SUMA;0;0,00\\n").encode("utf-8"))

            def test_no_byte_order_mark_and_lf_only(self):
                one = settlement.build_batch("B20261112-01", [Payout("P1", "M10417", 100, AT)])
                data = self.write([one])
                self.assertFalse(data.startswith(b"\\xef\\xbb\\xbf"))
                self.assertNotIn(b"\\r", data)


        if __name__ == "__main__":
            unittest.main()
        '''),
    "test_x7_compliance.py": _d('''
        """X7: payouts within 30 days after a change of the merchant's account (docs/compliance-iban.md)."""
        import unittest
        from datetime import datetime

        from ledger import audit
        from ledger.payouts import Payout

        E = audit.AuditEvent
        CHANGE_A = E(datetime(2026, 10, 10, 9, 0), "ops-anna", "merchant.iban_changed", "M10417")
        CHANGE_B = E(datetime(2026, 11, 1, 9, 0), "ops-piotr", "merchant.iban_changed", "M10418")
        PLAN = E(datetime(2026, 11, 1, 9, 0), "ops-anna", "merchant.plan_changed", "M10419")


        def payout(pid, merchant, at):
            return Payout(pid, merchant, 1000, at)


        class IbanChangeReportTest(unittest.TestCase):
            def test_report(self):
                payouts = [payout("P1", "M10417", datetime(2026, 11, 9, 1, 0)), payout("P2", "M10417", datetime(2026, 11, 10, 10, 0)),
                           payout("P3", "M10418", datetime(2026, 11, 2, 1, 0)), payout("P4", "M10419", datetime(2026, 11, 2, 1, 0)),
                           payout("P5", "M10418", datetime(2026, 10, 30, 1, 0))]
                self.assertEqual(audit.iban_change_report([CHANGE_A, CHANGE_B, PLAN], payouts),
                                 [("M10418", "P3", CHANGE_B.at), ("M10417", "P1", CHANGE_A.at)])

            def test_exactly_30_days_is_included(self):
                payouts = [payout("P1", "M10417", datetime(2026, 11, 9, 9, 0)), payout("P2", "M10417", datetime(2026, 11, 9, 9, 1))]
                self.assertEqual(audit.iban_change_report([CHANGE_A], payouts), [("M10417", "P1", CHANGE_A.at)])

            def test_the_latest_change_before_the_payout_counts(self):
                old = E(datetime(2026, 9, 1, 9, 0), "ops-anna", "merchant.iban_changed", "M10420")
                new = E(datetime(2026, 10, 20, 9, 0), "ops-anna", "merchant.iban_changed", "M10420")
                got = audit.iban_change_report([new, old], [payout("P9", "M10420", datetime(2026, 11, 5, 1, 0))])
                self.assertEqual(got, [("M10420", "P9", new.at)])

            def test_other_actions_and_no_events(self):
                self.assertEqual(audit.iban_change_report([PLAN], [payout("P4", "M10419", datetime(2026, 11, 2))]), [])
                self.assertEqual(audit.iban_change_report([], []), [])


        if __name__ == "__main__":
            unittest.main()
        '''),
    "test_x8_emails.py": _d('''
        """X8: the held-payout e-mail in Polish (docs/merchant-emails.md)."""
        import unittest
        from datetime import datetime

        from ledger import notifications


        class HeldPayoutMailTest(unittest.TestCase):
            def test_the_example(self):
                self.assertEqual(notifications.payout_held_text(123456, datetime(2026, 11, 12, 0, 30)),
                                 "Wypłata 1 234,56 zł zostanie wysłana w czwartek, 12 listopada 2026 o 00:30.")

            def test_tuesday_takes_we(self):
                self.assertEqual(notifications.payout_held_text(99, datetime(2026, 11, 17, 1, 0)),
                                 "Wypłata 0,99 zł zostanie wysłana we wtorek, 17 listopada 2026 o 01:00.")

            def test_other_days_and_months(self):
                self.assertEqual(notifications.payout_held_text(100_000_000, datetime(2027, 3, 1, 0, 30)),
                                 "Wypłata 1 000 000,00 zł zostanie wysłana w poniedziałek, 1 marca 2027 o 00:30.")
                self.assertEqual(notifications.payout_held_text(5_000, datetime(2026, 12, 23, 13, 5)),
                                 "Wypłata 50,00 zł zostanie wysłana w środę, 23 grudnia 2026 o 13:05.")

            def test_the_weekend(self):
                self.assertEqual(notifications.payout_held_text(12_550, datetime(2026, 11, 14, 9, 0)),
                                 "Wypłata 125,50 zł zostanie wysłana w sobotę, 14 listopada 2026 o 09:00.")


        if __name__ == "__main__":
            unittest.main()
        '''),
    "test_x9_exceptions.py": _d('''
        """X9: reconciliation exceptions grouped by merchant."""
        import unittest
        from datetime import date

        from ledger import reconcile
        from ledger.journal import LedgerEntry
        from ledger.statements import StatementLine

        DAY = date(2026, 11, 13)


        def line(ref, amount, txn):
            return StatementLine(DAY, DAY, amount, "PLN", "x", "PL0", "t", ref, txn)


        def entry(eid, merchant, ref):
            return LedgerEntry(eid, merchant, ref, 1000, "C", DAY)


        class ByMerchantTest(unittest.TestCase):
            def test_grouping(self):
                result = reconcile.MatchResult(
                    matched=[(entry("E0", "M10499", "PAY/M10499/000001"), line("PAY/M10499/000001", 1000, "T0"))],
                    unmatched_lines=[line("PAY/M10417/000123", 12550, "T1"), line("ZWROT/RFND/M10417/000877", -4599, "T2"),
                                     line(" pay/m10418/000001 ", 100, "T3"), line("", -500, "T4"),
                                     line("OPLATA ZA WYCIAG", -300, "T5")],
                    unmatched_entries=[entry("E1", "M10417", "PAY/M10417/000999"), entry("E2", "M10419", "odd ref")])
                self.assertEqual(reconcile.unmatched_by_merchant(result), {
                    "M10417": {"lines": 2, "entries": 1}, "M10418": {"lines": 1, "entries": 0},
                    "M10419": {"lines": 0, "entries": 1}, "?": {"lines": 2, "entries": 0}})

            def test_nothing_unmatched(self):
                self.assertEqual(reconcile.unmatched_by_merchant(reconcile.MatchResult()), {})

            def test_refund_reference_without_the_prefix(self):
                result = reconcile.MatchResult(unmatched_lines=[line("RFND/M10420/000001", -100, "T1")])
                self.assertEqual(reconcile.unmatched_by_merchant(result), {"M10420": {"lines": 1, "entries": 0}})

            def test_marketplace_sellers(self):
                result = reconcile.MatchResult(unmatched_lines=[line("PAY/MP0042-S00317/000001", 100, "T1")])
                self.assertEqual(reconcile.unmatched_by_merchant(result), {"MP0042-S00317": {"lines": 1, "entries": 0}})


        if __name__ == "__main__":
            unittest.main()
        '''),
    "test_x10_batch_total.py": _d('''
        """X10: no batch above the control sum limit of the bank (settlement_batch_max_total)."""
        import unittest
        from datetime import date, datetime

        from ledger import settings, settlement
        from ledger.payouts import Payout

        DAY = date(2026, 11, 12)
        AT = datetime(2026, 11, 12, 1, 0)


        def payouts(merchant, amounts, start=0):
            return [Payout(f"P{start + i}", merchant, a, AT) for i, a in enumerate(amounts)]


        def amounts(batches):
            return [[line.amount for line in b.lines] for b in batches]


        class BatchTotalTest(unittest.TestCase):
            def test_a_new_batch_before_the_limit_is_passed(self):
                items = payouts("M10417", [90_000_000, 90_000_000, 30_000_000, 10_000_000, 250_000_000, 5])
                batches = settlement.split_into_batches(DAY, items, 25, 200_000_000)
                self.assertEqual(amounts(batches), [[90_000_000, 90_000_000], [30_000_000, 10_000_000], [250_000_000],
                                                    [5]])
                self.assertEqual([b.batch_id for b in batches], [f"B20261112-0{k}" for k in range(1, 5)])

            def test_the_payout_limit_still_applies(self):
                batches = settlement.split_into_batches(DAY, payouts("M10417", [100] * 30), 25, 200_000_000)
                self.assertEqual([len(b.lines) for b in batches], [25, 5])

            def test_marketplace_sellers_still_get_their_own_batches(self):
                items = payouts("MP0042-S00317", [150_000_000, 100_000_000]) + payouts("M10417", [10], 9)
                batches = settlement.split_into_batches(DAY, items, 25, 200_000_000)
                self.assertEqual(amounts(batches), [[10], [150_000_000], [100_000_000]])

            def test_no_limit_by_default(self):
                batches = settlement.split_into_batches(DAY, payouts("M10417", [150_000_000, 150_000_000]), 25)
                self.assertEqual(amounts(batches), [[150_000_000, 150_000_000]])

            def test_the_setting(self):
                self.assertEqual(settings.load("production", env={})["settlement_batch_max_total"], 200_000_000)
                self.assertIsNone(settings.load("development", env={}).get("settlement_batch_max_total"))


        if __name__ == "__main__":
            unittest.main()
        '''),
}


_EXT_REF_V3 = "\n\n" + _d('''


    def write_payouts_v3(path, batches) -> int:
        """Write the accounting export v3 (docs/accounting-export.md); returns the row count."""
        def amount(grosze: int) -> str:
            return format_amount(grosze).replace(".", ",")

        rows, total = [], 0
        for batch in batches:
            period = f"{batch.batch_id[1:5]}-{batch.batch_id[5:7]}"
            for line in batch.lines:
                rows.append(";".join([period, batch.batch_id, line.payout_id, line.merchant_id, amount(line.amount),
                                      "PLN", line.e2e_reference]))
                total += line.amount
        text = "Okres;Batch;Wypłata;Merchant;Kwota;Waluta;Referencja\\n" + "".join(r + "\\n" for r in rows)
        text += f"#SUMA;{len(rows)};{amount(total)}\\n"
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return len(rows)
    ''')

_EXT_REF_AUDIT = "\n\n" + _d('''


    def iban_change_report(events, payouts) -> list[tuple[str, str, datetime]]:
        """Payouts within 30 days after their merchant's latest account change (docs/compliance-iban.md)."""
        changes: dict[str, list[datetime]] = {}
        for event in events:
            if event.action == "merchant.iban_changed":
                changes.setdefault(event.target, []).append(event.at)
        out = []
        for payout in sorted(payouts, key=lambda p: p.created_at):
            before = [at for at in changes.get(payout.merchant_id, []) if at <= payout.created_at]
            if before and payout.created_at - max(before) <= timedelta(days=30):
                out.append((payout.merchant_id, payout.payout_id, max(before)))
        return out
    ''')

_EXT_REF_MAIL = "\n\n" + _d('''


    _WEEKDAYS = ["w poniedziałek", "we wtorek", "w środę", "w czwartek", "w piątek", "w sobotę", "w niedzielę"]
    _MONTHS = ["stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca", "sierpnia", "września",
               "października", "listopada", "grudnia"]


    def payout_held_text(amount: int, release_at) -> str:
        """The held-payout e-mail (docs/merchant-emails.md)."""
        from .money import format_amount_pl
        return (f"Wypłata {format_amount_pl(amount)} zł zostanie wysłana {_WEEKDAYS[release_at.weekday()]}, "
                f"{release_at.day} {_MONTHS[release_at.month - 1]} {release_at.year} o {release_at:%H:%M}.")
    ''')

_EXT_REF_EXCEPTIONS = "\n\n" + _d('''


    def unmatched_by_merchant(result: MatchResult) -> dict[str, dict[str, int]]:
        """Unmatched lines and entries per merchant; lines without a PAY/RFND reference under ``"?"``."""
        out: dict[str, dict[str, int]] = {}

        def bump(merchant: str, kind: str) -> None:
            out.setdefault(merchant, {"lines": 0, "entries": 0})[kind] += 1

        for line in result.unmatched_lines:
            parts = line.reference.strip().upper().split("/")
            if parts and parts[0] == "ZWROT":
                parts = parts[1:]
            bump(parts[1] if len(parts) >= 3 and parts[0] in ("PAY", "RFND") else "?", "lines")
        for entry in result.unmatched_entries:
            bump(entry.merchant_id, "entries")
        return out
    ''')

_EXT_REF_SPLIT = _d('''
    def split_into_batches(day: date, items: list[Payout], max_payouts: int, max_total: int | None = None) -> list[Batch]:
        """Split a day's payouts into batches of at most ``max_payouts`` and, when given, a control sum of at most
        ``max_total`` (a payout above it gets a batch of its own); marketplace sellers get batches of their own,
        after the ordinary merchants."""
        ordinary = [p for p in items if not p.merchant_id.startswith("MP")]
        marketplace = [p for p in items if p.merchant_id.startswith("MP")]
        batches, number = [], 0
        for group in (ordinary, marketplace):
            current: list[Payout] = []
            for payout in group:
                total = sum(p.amount for p in current)
                if current and (len(current) >= max_payouts
                                or (max_total is not None and total + payout.amount > max_total)):
                    number += 1
                    batches.append(build_batch(refs.batch_id(day, number), current))
                    current = []
                current.append(payout)
            if current:
                number += 1
                batches.append(build_batch(refs.batch_id(day, number), current))
        return batches
    ''')

# (file, old, new, regex) like REFERENCE, applied after it (and after the console migration) in an extended build
EXT_REFERENCE = [
    ("ledger/chargebacks.py", '    return (dispute.opened_on - dispute.paid_on).days < settings["chargeback_window_days"]\n',
     '    return (dispute.opened_on - dispute.paid_on).days <= settings["chargeback_window_days"]\n', False),
    ("ledger/onboarding.py", '    return text.replace(" ", "").upper()\n',
     '    return re.sub(r"[\\s\\-]", "", text).upper()  # \\s covers tabs and non-breaking spaces too\n', False),
    ("ledger/invoices.py", "    vat = sum(percent_of(line.net, VAT_RATE_BPS) for line in lines)\n",
     "    vat = percent_of(net, VAT_RATE_BPS)  # once, on the total (docs/fee-invoices.md)\n", False),
    ("ledger/retry.py", "    return sorted((q for q in queue if q.run == run), key=lambda q: q.queued_at)\n",
     "    newest: dict[str, QueuedPayout] = {}\n"
     "    for q in queue:\n"
     "        if q.run == run and (q.payout_id not in newest or q.queued_at > newest[q.payout_id].queued_at):\n"
     "            newest[q.payout_id] = q\n"
     "    return sorted(newest.values(), key=lambda q: q.queued_at)\n", False),
    ("ledger/bankdays.py", "    return day.weekday() < 5 and day not in HOLIDAYS_2026\n",
     "    return day.weekday() < 5 and day not in HOLIDAYS_2026 and day not in HOLIDAYS_2027\n", False),
    ("ledger/bankdays.py", "                 date(2026, 11, 11), date(2026, 12, 24), date(2026, 12, 25), date(2026, 12, 26)}\n",
     "                 date(2026, 11, 11), date(2026, 12, 24), date(2026, 12, 25), date(2026, 12, 26)}\n"
     "HOLIDAYS_2027 = {date(2027, 1, 1), date(2027, 1, 6), date(2027, 3, 28), date(2027, 3, 29), date(2027, 5, 1),\n"
     "                 date(2027, 5, 3), date(2027, 5, 16), date(2027, 5, 27), date(2027, 8, 15), date(2027, 11, 1),\n"
     "                 date(2027, 11, 11), date(2027, 12, 24), date(2027, 12, 25), date(2027, 12, 26)}\n", False),
    ("ledger/exports.py", "    return len(rows)\n", "    return len(rows)\n" + _EXT_REF_V3, False),
    ("ledger/audit.py", '    return f"{event.at.isoformat()} {event.actor} {event.action} {event.target}"\n',
     '    return f"{event.at.isoformat()} {event.actor} {event.action} {event.target}"\n' + _EXT_REF_AUDIT, False),
    ("ledger/audit.py", "from datetime import datetime\n", "from datetime import datetime, timedelta\n", False),
    ("ledger/notifications.py", '    return PAYOUT_SENT.format(amount=format_amount(amount), iban=iban[-4:].rjust(len(iban), "*"))\n',
     '    return PAYOUT_SENT.format(amount=format_amount(amount), iban=iban[-4:].rjust(len(iban), "*"))\n'
     + _EXT_REF_MAIL, False),
    ("ledger/reconcile.py", '            "unmatched_entries": len(result.unmatched_entries)}\n',
     '            "unmatched_entries": len(result.unmatched_entries)}\n' + _EXT_REF_EXCEPTIONS, False),
    ("ledger/settlement.py", r"(?s)def split_into_batches\(day: date, items: list\[Payout\], max_payouts: int\) -> list\[Batch\]:"
     r".*?\n    return batches\n", lambda m: _EXT_REF_SPLIT, True),
    ("ledger/config.py", '    "fee_default_plan": "standard",  # plan of merchants without an explicit plan\n',
     '    "fee_default_plan": "standard",  # plan of merchants without an explicit plan\n'
     '    "settlement_batch_max_total": None,  # control sum limit of one bank file (grosze); None: no limit\n', False),
    ("ledger/bankfile.py", '    "settlement_batch_max_payouts": 25,  # the old interface refuses bigger files\n',
     '    "settlement_batch_max_payouts": 25,  # the old interface refuses bigger files\n'
     '    "settlement_batch_max_total": 200_000_000,  # and files above 2 000 000,00 zł\n', False),
]
