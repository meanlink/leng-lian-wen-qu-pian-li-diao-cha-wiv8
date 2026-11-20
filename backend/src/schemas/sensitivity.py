from pydantic import BaseModel, Field
class SensitivityInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    dataset_id: int
    lower: float
    upper: float
    step: float
    alpha: float = 0.05
