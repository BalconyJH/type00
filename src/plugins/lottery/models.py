from pydantic import BaseModel, Field, model_validator, root_validator


class Lottery(BaseModel):
    id: str
    creator: str
    scene: str
    start_time: str
    end_time: str
    keyword: str
    participants_limits: int = 1
    participants: list = Field(default_factory=list)
    bot_id: str
    adapter: str

    @model_validator(mode="before")
    def check_model(cls, values):
        numbers = values.get("numbers", [])
        limits = values.get("participants_limits")
        if len(numbers) > limits:
            raise ValueError(
                f"Numbers list cannot exceed {limits} items."
            )
        return values
