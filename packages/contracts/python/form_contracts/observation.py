"""PageObservation and related contracts (plan.md §8.2)."""

from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from .common import StrictModel, validate_origin


class TargetDescriptor(StrictModel):
    """Resilient semantic locator for a form control (plan.md §9)."""

    field_id: str
    role: str
    accessible_name: str | None = None
    input_type: str | None = None
    label: str | None = None
    name_attr: str | None = None
    autocomplete: str | None = None
    placeholder: str | None = None
    bounding_box: tuple[float, float, float, float] | None = None


# Input types whose values must never leave the page (invariant 2).
CREDENTIAL_INPUT_TYPES = frozenset({"password"})


class FieldPurpose(StrEnum):
    """What a control is FOR, beyond its input type. Perception classifies it
    from the control's own text (label, name, placeholder, autocomplete) so the
    backend can apply the right rule without site-specific knowledge:

    - STANDARD: an ordinary data field, fillable from facts. Includes masked
      identifiers (an Aadhaar/PAN/account number rendered as a password-type
      input) — their VALUE stays redacted, but they are not login secrets.
    - CREDENTIAL: a password / OTP / PIN / card secret. Never read, never
      written (invariant 2); the human enters it on the page.
    - CAPTCHA: a human-only challenge (text captcha box). Never written.
    - CONSENT: a declaration/consent control ("I consent to…"). Only set on the
      user's explicit say-so, never inferred from a profile.
    """

    STANDARD = "standard"
    CREDENTIAL = "credential"
    CAPTCHA = "captcha"
    CONSENT = "consent"


class FormField(StrictModel):
    field_id: str
    target: TargetDescriptor
    input_type: str
    label: str | None = None
    accessible_name: str | None = None
    required: bool = False
    disabled: bool = False
    readonly: bool = False
    visible: bool = True
    checked: bool | None = None
    current_value: str | None = None
    value_redacted: bool = False
    # Length of a redacted value (masked identifier) so a fill can still be
    # verified and the field known as filled without the value leaving the page.
    value_length: int | None = None
    purpose: FieldPurpose = FieldPurpose.STANDARD
    options: list[str] | None = None  # option values (what the executor selects)
    option_labels: list[str] | None = None  # human labels, parallel to options
    validation_message: str | None = None
    # HTML maxlength (chars) when the control enforces one — the reasoning layer
    # needs the real cap to produce a value that won't be silently truncated.
    max_length: int | None = None
    nearby_text: str | None = None

    @model_validator(mode="after")
    def _no_credential_values(self) -> "FormField":
        if self.value_redacted and self.current_value is not None:
            raise ValueError("a redacted field must not carry current_value")
        if self.input_type in CREDENTIAL_INPUT_TYPES and self.current_value is not None:
            raise ValueError(
                f"values of {self.input_type!r} inputs must never be observed "
                "(invariant 2); set value_redacted instead"
            )
        return self


class PageClass(StrEnum):
    FORM = "form"
    LOGIN = "login"
    MFA = "mfa"
    CAPTCHA = "captcha"
    CONFIRMATION = "confirmation"
    ERROR = "error"
    COOKIE_BANNER = "cookie_banner"
    OTHER = "other"


class PageClassCandidate(StrictModel):
    label: PageClass
    confidence: float = Field(ge=0.0, le=1.0)


class DialogKind(StrEnum):
    COOKIE_BANNER = "cookie_banner"
    MODAL = "modal"
    ALERT = "alert"
    OTHER = "other"


class DialogInfo(StrictModel):
    dialog_id: str
    kind: DialogKind
    text_snippet: str | None = None
    dismiss_target: TargetDescriptor | None = None
    # The dialog CONTAINS the form's fields (a registration modal): it is the
    # working surface, not an obstacle — the driver must not dismiss it.
    contains_form: bool = False


class FrameInfo(StrictModel):
    frame_id: str
    origin: str
    accessible: bool

    _origin_ok = field_validator("origin")(validate_origin)


class NavigationKind(StrEnum):
    NEXT = "next"
    PREVIOUS = "previous"
    SUBMIT = "submit"


class NavigationControl(StrictModel):
    """A control that advances, reverses, or submits a multi-step form."""

    control_id: str
    kind: NavigationKind
    target: TargetDescriptor
    label: str | None = None


class UnrecognizedControl(StrictModel):
    """An interactive element perception can SEE but cannot operate (a role
    with no executor, or a click-driven element with no role). Reported so the
    agent can say "I see X but can't fill it" instead of skipping silently."""

    role: str
    name: str | None = None


class PageObservation(StrictModel):
    run_id: str
    tab_id: int
    frame_id: str = "main"
    url: str
    origin: str
    title: str | None = None
    page_fingerprint: str
    observation_seq: int = Field(ge=0)
    observed_at: datetime
    classification: list[PageClassCandidate] = Field(default_factory=list)
    fields: list[FormField] = Field(default_factory=list)
    dialogs: list[DialogInfo] = Field(default_factory=list)
    iframes: list[FrameInfo] = Field(default_factory=list)
    navigation: list[NavigationControl] = Field(default_factory=list)
    # Multi-page progress hint when the page exposes one ("Step 2 of 4").
    step_label: str | None = None
    login_detected: bool = False
    captcha_detected: bool = False
    dom_stable: bool = True
    unrecognized_controls: list[UnrecognizedControl] = Field(default_factory=list)

    _origin_ok = field_validator("origin")(validate_origin)

    @model_validator(mode="after")
    def _url_matches_origin(self) -> "PageObservation":
        if not self.url.startswith(self.origin.rstrip("/") + "/") and self.url != self.origin:
            raise ValueError(f"url {self.url!r} is not within origin {self.origin!r}")
        return self
