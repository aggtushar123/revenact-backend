"""The two kinds the Pipelines book serves, and everything that differs
between them, named once: the model and its single-edit serializer, the date
field ("expected close" / "due by"), which stages are open, and the stage
the "…this quarter" tile counts (Closed Won / Mitigated). Every other module
takes a `Kind` and never branches on which one it is."""

from dataclasses import dataclass

from services.customers.models import Opportunity, Risk
from services.customers.serializers import OpportunitySerializer, RiskSerializer


@dataclass(frozen=True)
class Kind:
    #: The route segment and the `kind` in a response: "opportunities".
    key: str
    #: One item, as a row's `kind` and an audit prefix: "opportunity".
    item: str
    model: type
    serializer: type
    date_field: str
    date_label: str
    open_stages: tuple[str, ...]
    done_stage: str

    @property
    def stages(self) -> tuple[str, ...]:
        return tuple(self.model.Stage.values)


OPPORTUNITIES = Kind(
    key="opportunities",
    item="opportunity",
    model=Opportunity,
    serializer=OpportunitySerializer,
    date_field="expected_close",
    date_label="Expected Close",
    open_stages=tuple(stage.value for stage in Opportunity.OPEN_STAGES),
    done_stage=Opportunity.Stage.CLOSED_WON.value,
)
RISKS = Kind(
    key="risks",
    item="risk",
    model=Risk,
    serializer=RiskSerializer,
    date_field="due_by",
    date_label="Due By",
    open_stages=tuple(stage.value for stage in Risk.OPEN_STAGES),
    done_stage=Risk.Stage.MITIGATED.value,
)
KINDS = {kind.key: kind for kind in (OPPORTUNITIES, RISKS)}
