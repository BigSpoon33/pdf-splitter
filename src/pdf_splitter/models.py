"""The Plan a client PUTs (Architecture § Data Types), validated before anything touches a path or the engine.

Every number must be finite: `profile_from_dict`'s range checks let NaN through, and the engine's `cuts.plan`
raises IndexError for a page past the book, so pages are checked against the job here (validation context
`pages` and `sizes`, from the job row and `analysis.json`).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from monograph_splitter.profile import ProfileError, profile_from_dict
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .worker.analyze import DEFAULT_SETTINGS, MAX_HEADING, MAX_SECTION_NAME, clean_text

MAX_SECTIONS = 2000
Source = Literal["outline", "headings", "manual", "ranges"]
Col = Literal["full", "left", "right"]
Cut = Annotated[float, Field(ge=0)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PlanSettings(_Strict):
    column_split: float = Field(default=DEFAULT_SETTINGS["column_split"], ge=0.2, le=0.8)
    single_column: bool = DEFAULT_SETTINGS["single_column"]
    header_band: float = Field(default=DEFAULT_SETTINGS["header_band"], ge=0, le=200)
    footer_band: float = Field(default=DEFAULT_SETTINGS["footer_band"], ge=0, le=200)
    heading_min_size: float = Field(default=DEFAULT_SETTINGS["heading_min_size"], ge=4, le=72)
    # Optional (STORY-009): a wrapped title's lines are one heading when their tops sit within this many
    # points; the engine's 16 splits titles from about 14 pt up, a big-type book needs ~30. Left out of the
    # saved plan unless the client sets it, so an untouched plan keeps its five keys and the index cache.
    heading_wrap_gap: float | None = Field(default=None, ge=0, le=200)

    def dump(self) -> dict[str, Any]:
        """The settings as `plan.json` carries them and `profile_from_dict` reads them."""
        return self.model_dump(exclude={"heading_wrap_gap"} if self.heading_wrap_gap is None else set())

    @model_validator(mode="after")
    def _engine_accepts(self) -> PlanSettings:
        # The ranges above sit inside the engine's own, so this only catches drift between the two.
        try:
            profile_from_dict(self.dump())
        except ProfileError as e:
            raise ValueError(str(e)) from e
        return self


class Section(_Strict):
    name: str = Field(min_length=1, max_length=MAX_SECTION_NAME)
    page: int = Field(ge=1)
    heading: str = Field(default="", max_length=MAX_HEADING)
    # ADR-009: the last page of a whole-page span (inclusive, 1-based). Only a `ranges` plan carries it — there
    # every section needs one; a chapter plan with one is refused rather than silently cut short. Validated on
    # its default too, so a missing one in ranges mode is reported at the field a client can point at.
    endPage: int | None = Field(default=None, ge=1, validate_default=True)

    @field_validator("name", "heading", mode="before")
    @classmethod
    def _clean(cls, v: Any) -> Any:
        # A JSON `"\udcff"` escape decodes to a lone surrogate that strict UTF-8 (the plan file, the ZIP entry
        # name, the response) rejects; the same U+FFFD rule as the analysis.
        return clean_text(v).strip() if isinstance(v, str) else v

    @field_validator("page")
    @classmethod
    def _in_book(cls, v: int, info: ValidationInfo) -> int:
        pages = (info.context or {}).get("pages")
        if pages is not None and v > pages:
            raise ValueError(f"page must be at most {pages}, the last page of the book")
        return v

    @field_validator("endPage")
    @classmethod
    def _span_in_book(cls, v: int | None, info: ValidationInfo) -> int | None:
        context = info.context or {}
        if not context.get("ranges"):
            if v is not None:
                raise ValueError("endPage only applies to a page-range plan")
            return None
        if v is None:
            raise ValueError("endPage is required in page-range mode")
        pages = context.get("pages")
        if pages is not None and v > pages:
            raise ValueError(f"endPage must be at most {pages}, the last page of the book")
        page = info.data.get("page")
        if page is not None and v < page:
            raise ValueError("endPage must be at least the section's first page")
        return v

    def dump(self) -> dict[str, Any]:
        """As `plan.json` carries it: `endPage` only where it applies, so a chapter plan keeps its three keys."""
        return self.model_dump(exclude={"endPage"} if self.endPage is None else set())


class Override(_Strict):
    """Only the keys a client sends are applied (the engine's `apply_overrides` sets present keys), so a
    `startCut: null` removes the start cut while an absent one keeps the engine's."""

    startCut: Cut | None = None
    startCol: Col = "full"
    endCut: Cut | None = None
    endCol: Col = "full"

    def dump(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("startCut", "startCol", "endCut", "endCol") if k in self.model_fields_set}


def check_override(ov: Override, height: float, max_height: float) -> str | None:
    """Why `ov` can't apply to a section whose first sheet is `height` tall (the last sheet is only known
    after planning, so the end cut is bounded by the tallest sheet in the book), or None."""
    if ov.startCut is not None and ov.startCut > height:
        return f"startCut must be at most the page height ({height})"
    if ov.endCut is not None and ov.endCut > max_height:
        return f"endCut must be at most the page height ({max_height})"
    return None


def _heights(info: ValidationInfo) -> list[float] | None:
    sizes = (info.context or {}).get("sizes")
    return [float(s["H"]) for s in sizes] if sizes else None


def dedupe_names(sections: list[Section]) -> None:
    """Duplicates are allowed in the UI (Architecture); the saved plan — and a list the preview plans — makes
    them distinct with a counter, so every display name maps to one section (the engine looks entries up by
    name)."""
    used: set[str] = set()
    for s in sections:
        name, k = s.name, 2
        while name in used:
            suffix = f" ({k})"
            name = s.name[: MAX_SECTION_NAME - len(suffix)].rstrip() + suffix
            k += 1
        used.add(name)
        s.name = name


class Plan(_Strict):
    source: Source
    settings: PlanSettings = Field(default_factory=PlanSettings)
    sections: list[Section] = Field(max_length=MAX_SECTIONS)
    overrides: dict[str, Override] = Field(default_factory=dict)

    @field_validator("overrides")
    @classmethod
    def _keyed_by_section(cls, v: dict[str, Override], info: ValidationInfo) -> dict[str, Override]:
        sections: list[Section] | None = info.data.get("sections")
        if sections is None:
            return v          # sections already failed; their error is the one to report
        if v and info.data.get("source") == "ranges":
            # ADR-009: a range is a whole-page span, there is no cut to place on its sheets.
            raise ValueError("overrides do not apply to a page-range plan")
        heights = _heights(info)
        for key, ov in v.items():
            if not key.isdecimal() or str(int(key)) != key or int(key) >= len(sections):
                raise ValueError(f"override {key!r} does not name a section index (0..{len(sections) - 1})")
            if heights:
                why = check_override(ov, heights[sections[int(key)].page - 1], max(heights))
                if why:
                    raise ValueError(f"override {key}: {why}")
        return v

    @model_validator(mode="after")
    def _dedupe_names(self) -> Plan:
        dedupe_names(self.sections)
        return self

    def dump(self) -> dict[str, Any]:
        """The normalized Plan as `plan.json` and the API carry it."""
        return {
            "source": self.source,
            "settings": self.settings.dump(),
            "sections": [s.dump() for s in self.sections],
            "overrides": {k: v.dump() for k, v in self.overrides.items()},
        }


class PreviewRequest(_Strict):
    """`POST /sections/{i}/plan`: settings default to the saved plan's; an absent `override` means the saved
    one, an explicit `null` the engine's own plan; `sections` is the list to plan `i` in — the client's local
    list, which may be ahead of the saved one — else the saved list. The override's bounds depend on which
    list `i` names a section of, so the route checks them (`check_override`) once it knows."""

    settings: PlanSettings | None = None
    override: Override | None = None
    sections: list[Section] | None = Field(default=None, max_length=MAX_SECTIONS)

    @model_validator(mode="after")
    def _dedupe_names(self) -> PreviewRequest:
        if self.sections is not None:
            dedupe_names(self.sections)
        return self


def validate_plan(raw: Any, *, pages: int, sizes: list[dict[str, float]]) -> Plan:
    """Raises `pydantic.ValidationError` (the route turns it into the 422)."""
    # The section rules depend on the plan's source, which a nested validator cannot see: it rides the context.
    ranges = isinstance(raw, dict) and raw.get("source") == "ranges"
    return Plan.model_validate(raw, context={"pages": pages, "sizes": sizes, "ranges": ranges})


__all__ = [
    "Override", "Plan", "PlanSettings", "PreviewRequest", "Section", "ValidationError", "check_override", "validate_plan",
]
