from pydantic import BaseModel


class CapabilityOut(BaseModel):
    slug: str
    name: str
    description: str | None


class TraitOut(BaseModel):
    slug: str
    name: str
