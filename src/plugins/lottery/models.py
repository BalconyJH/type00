from pydantic import BaseModel, Field


class Lottery(BaseModel):
    id: str
    creator: str
    scene: str
    start_time: str
    end_time: str
    keyword: str
    number_of_winners: int = 1
    participants: list[str] = Field(default_factory=list)
    bot_id: str
    adapter: str

class Lotteries(BaseModel):
    lotteries: list[Lottery] = Field(default_factory=list)