"""Domain models. These are the API's contract; Mongo documents are mapped onto them."""

from biostruct.models.project import (
    Budget,
    Infection,
    Project,
    ProjectCreate,
    ProjectUpdate,
    RankingWeights,
)
from biostruct.models.target import (
    ActiveSiteOut,
    EpitopeOut,
    EpitopeSelection,
    ReferenceOut,
    ResidueMapSummary,
    StructureRefOut,
    Target,
    TargetCandidate,
    TargetCreate,
    TargetSearchResponse,
)
from biostruct.models.user import LoginRequest, RegisterRequest, User, UserPublic

__all__ = [
    "ActiveSiteOut",
    "Budget",
    "EpitopeOut",
    "EpitopeSelection",
    "Infection",
    "LoginRequest",
    "Project",
    "ProjectCreate",
    "ProjectUpdate",
    "RankingWeights",
    "ReferenceOut",
    "RegisterRequest",
    "ResidueMapSummary",
    "StructureRefOut",
    "Target",
    "TargetCandidate",
    "TargetCreate",
    "TargetSearchResponse",
    "User",
    "UserPublic",
]
